#!/usr/bin/env python3
"""Merge near-duplicate tags produced by canonicalisation (prompt or vision path)."""

import json
import sys
from collections import Counter
from pathlib import Path

from openai import OpenAI
from config import API_BASE, API_MODEL, API_KEY, API_TIMEOUT, IMG_DIR, PROMPT_MERGE
from utils import strip_thinking

_IMG = Path(IMG_DIR)

_CANDIDATES = [
    _IMG / "vision_canonicalized.jsonl",   # right column (preferred)
    _IMG / "canonicalized.jsonl",          # left column
    _IMG / "vision_tags.jsonl",            # raw vision (no canonicalize)
]
INPUT = next((p for p in _CANDIDATES if p.exists()), None)
OUTPUT = _IMG / "merged.jsonl"

if INPUT is None:
    print("No input found. Run 2_canonicalize.py or 1b_vision_tag.py first.",
          file=sys.stderr)
    sys.exit(1)

# ── Deterministic pre-merges ─────────────────────────────────────────

def normalize_tag(tag: str) -> str:
    """Sort words so pure reorderings become identical strings."""
    return " ".join(sorted(tag.strip().split()))

import re

def normalize_for_overlap(tag: str) -> set[str]:
    """Lowercase + split on any non-alphanumeric, so 'fighter-jet' == 'fighter jet'."""
    return set(re.findall(r'[a-z0-9]+', tag.lower()))

def safe_overlap(A: set[str], B: set[str],
                 min_size_ratio: float = 0.5,
                 min_common: int = 2,
                 min_jaccard: float = 0.85) -> float:
    """Overlap with subset fast-path and strict jaccard for non-subset pairs.

    - min_size_ratio: shorter tag must be >= 50% the length of the longer
      (prevents "girl" absorbing "girl dinosaur park").
    - min_common: needs >= 2 words in common (prevents 1-word coincidences).
    - Subset fast-path: if one tag contains all words of the other, merge
      freely — this is the safe "decorator/extension" case.
    - Non-subset pairs require jaccard >= min_jaccard, so tags that share
      an action but name different subjects (voldemort vs lily potter) do
      NOT merge here — the LLM decides.
    """
    if not A or not B:
        return 0.0
    smaller, larger = sorted([len(A), len(B)])
    if smaller / larger < min_size_ratio:
        return 0.0
    inter = len(A & B)
    if inter < min_common:
        return 0.0
    # Subset fast-path: A ⊆ B or B ⊆ A
    if A <= B or B <= A:
        return 1.0
    # Non-subset: require very strict string-level similarity
    jaccard = inter / len(A | B)
    if jaccard < min_jaccard:
        return 0.0
    return inter / smaller


def overlap_clusters(tags: list[str], threshold: float = 0.65,
                     min_size_ratio: float = 0.5,
                     min_common: int = 2,
                     min_jaccard: float = 0.85,
                     max_cluster_size: int = 25) -> list[list[str]]:
    """Union-find with size guards + max cluster cap."""
    parent = {t: t for t in tags}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    word_sets = {t: normalize_for_overlap(t) for t in tags}
    tag_list = list(tags)
    for i, a in enumerate(tag_list):
        sa = word_sets[a]
        for b in tag_list[i + 1:]:
            sb = word_sets[b]
            if safe_overlap(sa, sb, min_size_ratio, min_common, min_jaccard) >= threshold:
                union(a, b)

    clusters: dict[str, list[str]] = {}
    for t in tag_list:
        clusters.setdefault(find(t), []).append(t)

    result = []
    for c in clusters.values():
        if len(c) > max_cluster_size:
            print(f"  [warn] cluster too large ({len(c)} tags), skipping pre-merge. "
                  f"First: {c[0]!r}", flush=True)
            # Emit as singletons so they go individually to the LLM
            result.extend([[t] for t in c])
        else:
            result.append(c)
    return result

# ── LLM merge ────────────────────────────────────────────────────────

def llm_merge_clusters(clusters: list[list[str]], client: OpenAI,
                       batch_size: int = 50, pass_num: int = 1) -> dict[str, str]:
    """Merge each cluster into ONE tag via LLM. Singleton clusters map to themselves.

    Batches pack multiple clusters together but NEVER split a cluster across batches,
    so the LLM always sees all related tags at once.
    """
    full_mapping: dict[str, str] = {}
    for c in clusters:
        if len(c) == 1:
            full_mapping[c[0]] = c[0]

    multi = [c for c in clusters if len(c) >= 2]
    if not multi:
        return full_mapping

    # Pack clusters into batches (bin-packing, cluster integrity preserved)
    batches: list[list[list[str]]] = []
    current: list[list[str]] = []
    current_size = 0
    for c in multi:
        if current and current_size + len(c) > batch_size:
            batches.append(current)
            current, current_size = [], 0
        current.append(c)
        current_size += len(c)
    if current:
        batches.append(current)

    for bnum, batch in enumerate(batches, 1):
        # Separate clusters by blank lines so the LLM sees group boundaries
        body = "\n\n".join("\n".join(c) for c in batch)
        user_msg = f"Clusters (blank-line separated):\n\n{body}\n\nReturn the JSON mapping."
        n_tags = sum(len(c) for c in batch)
        print(f"  [{pass_num}] batch {bnum}/{len(batches)}: "
              f"{len(batch)} clusters, {n_tags} tags", flush=True)

        res = client.chat.completions.create(
            model=API_MODEL or None,
            messages=[
                {"role": "system", "content": PROMPT_MERGE},
                {"role": "user",   "content": user_msg},
            ],
            max_tokens=42000,
            temperature=0,
            seed=42,
            top_p=0.95,
            stop=["<|im_end|>", "<|im_start|>"],
        )
        content = res.choices[0].message.content
        reasoning = getattr(res.choices[0].message, "reasoning_content", None)
        if reasoning is None:
            content = strip_thinking(content)
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]

        try:
            batch_map = json.loads(text)
            for c in batch:
                for t in c:
                    if t not in batch_map:
                        batch_map[t] = t
            merges = [(k, v) for k, v in batch_map.items() if k != v]
            full_mapping.update(batch_map)
            if merges:
                print(f"           → {len(merges)} merges: ", end="", flush=True)
                for k, v in merges[:3]:
                    print(f"{k[:30]!r}→{v[:30]!r}", end="  ", flush=True)
                print(f"…+{len(merges)-3}" if len(merges) > 3 else "", flush=True)
            else:
                print("           → no merges", flush=True)
        except json.JSONDecodeError:
            for c in batch:
                for t in c:
                    full_mapping[t] = t
            print(f"           → [warn] unparseable JSON (identity fallback)", flush=True)

    return full_mapping


def merge_until_stable(records: list[dict], client: OpenAI,
                       max_iters: int = 3) -> list[dict]:
    """Multi-pass: re-cluster + LLM merge each iteration.

    Each pass loosens BOTH overlap threshold and min_jaccard:
      - Pass 1: only subset-safe merges (very conservative)
      - Pass 2: medium (jaccard 0.65)
      - Pass 3: aggressive (jaccard 0.30) — the LLM's rule 1b keeps
                Voldemort from absorbing Lily; the metric just has to
                surface the pair so the LLM can decide.
    """
    settings = [(0.65, 0.85), (0.60, 0.65), (0.55, 0.30)]

    for iteration in range(max_iters):
        unique = sorted({r["tag"] for r in records})
        thr, mj = settings[min(iteration, len(settings) - 1)]
        print(f"\n  Pass {iteration + 1}/{max_iters}: "
              f"{len(unique)} unique tags (overlap {thr}, jaccard {mj})", flush=True)
        if len(unique) <= 15:
            print("  Already ≤ 15, skipping LLM.")
            break

        clusters = overlap_clusters(unique, threshold=thr, min_jaccard=mj)
        n_multi = sum(1 for c in clusters if len(c) >= 2)
        print(f"  {len(clusters)} clusters ({n_multi} with ≥2 tags)", flush=True)

        if n_multi == 0:
            print("  No multi-tag clusters to send to LLM.", flush=True)
            if iteration >= len(settings) - 1:
                break
            continue

        mapping = llm_merge_clusters(clusters, client, pass_num=iteration + 1)
        new_records = [{**r, "tag": mapping.get(r["tag"], r["tag"])} for r in records]
        new_unique = len({r["tag"] for r in new_records})
        delta = len(unique) - new_unique

        if new_unique == len(unique):
            print(f"  Converged (no change: {new_unique} tags).", flush=True)
            if iteration >= len(settings) - 1:
                break
            continue

        records = new_records
        print(f"  End of pass: {new_unique} tags (−{delta})", flush=True)

    return records

# ── Main ─────────────────────────────────────────────────────────────
def main():
    records: list[dict] = []
    with open(INPUT) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        print(f"No records found in {INPUT}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(records)} records from {INPUT.name}", flush=True)

    # Stage A: word-sort pre-merge
    tag_freq = Counter(r["tag"] for r in records)
    unique_tags = list(tag_freq.keys())

    norm_groups: dict[str, list[str]] = {}
    for t in unique_tags:
        norm_groups.setdefault(normalize_tag(t), []).append(t)

    pre_canonical: dict[str, str] = {}
    for _norm, variants in norm_groups.items():
        best = max(variants, key=lambda v: tag_freq[v])
        for v in variants:
            pre_canonical[v] = best

    for r in records:
        r["tag"] = pre_canonical.get(r["tag"], r["tag"])

    tag_freq = Counter(r["tag"] for r in records)
    unique_tags = sorted(tag_freq.keys(), key=lambda t: -tag_freq[t])
    print(f"After word-sort pre-merge: {len(unique_tags)} unique tags", flush=True)

    # Stage B: overlap pre-merge (conservative, union-find)
    clusters = overlap_clusters(unique_tags, threshold=0.65)
    pre_canonical = {}
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        canonical = min(cluster, key=lambda t: (len(t.split()), len(t)))
        for t in cluster:
            if t != canonical:
                pre_canonical[t] = canonical

    if pre_canonical:
        for r in records:
            r["tag"] = pre_canonical.get(r["tag"], r["tag"])

    tag_freq = Counter(r["tag"] for r in records)
    unique_tags = sorted(tag_freq.keys(), key=lambda t: -tag_freq[t])
    print(f"After overlap pre-merge:   {len(unique_tags)} unique tags", flush=True)

    # ── Stage C: iterative LLM merge ────────────────────────────────
    if len(unique_tags) > 15:
        print("Connecting to LLM server...", flush=True)
        client = OpenAI(base_url=API_BASE, api_key=API_KEY, timeout=API_TIMEOUT)
        try:
            client.models.list()
            print("Server reachable.", flush=True)
        except Exception as e:
            print(f"✗ Cannot reach {API_BASE}: {e}", file=sys.stderr)
            sys.exit(1)

        records = merge_until_stable(records, client, max_iters=3)
    else:
        print("Tag count already ≤ 15, skipping LLM merge.", flush=True)

    # ── Write output ────────────────────────────────────────────────
    records.sort(key=lambda r: r["tag"].lower())
    with open(OUTPUT, "w") as out:
        for r in records:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")

    final_unique = len({r["tag"] for r in records})
    print(f"\nDone. {final_unique} groups → {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
