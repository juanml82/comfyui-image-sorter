#!/usr/bin/env python3
"""Normalize short vision tags via OpenAI-compatible server."""

import json
import sys
import time
from collections import Counter
from pathlib import Path

from openai import OpenAI
from config import API_BASE, API_MODEL, API_KEY, API_TIMEOUT, IMG_DIR, PROMPT_CANONICALIZE
from utils import strip_thinking

INPUT_JSONL  = Path(IMG_DIR) / "vision_tags.jsonl"
OUTPUT_JSONL = Path(IMG_DIR) / "vision_canonicalized.jsonl"


def llm_normalize(client: OpenAI, tag: str) -> str:
    res = client.chat.completions.create(
        model=API_MODEL or None,
        messages=[
            {"role": "system", "content": PROMPT_CANONICALIZE},
            {"role": "user",   "content": tag},
        ],
        max_tokens=20,
        temperature=0.0,
        top_p=1.0,
        seed=42,
    )
    content = res.choices[0].message.content

    # ── strip thinking (no-op if absent) ──
    reasoning = getattr(res.choices[0].message, "reasoning_content", None)
    if reasoning is None:
        content = strip_thinking(content)

    out = content.strip().lower()
    out = out.replace(",", " ").replace(".", " ").replace(";", " ")
    out = " ".join(out.split())
    for art in ("the ", "a ", "an "):
        if out.startswith(art):
            out = out[len(art):]
    return out if out else tag

def main():
    if not INPUT_JSONL.exists():
        print(f"✗ Not found: {INPUT_JSONL} — run 1b_vision_tag.py first.",
              file=sys.stderr)
        sys.exit(1)

    records = []
    with open(INPUT_JSONL) as f:
        for line in f:
            rec = json.loads(line)
            if rec["tag"] != "untagged":
                records.append(rec)

    unique_tags = list({r["tag"] for r in records})
    print(f"{len(records)} images, {len(unique_tags)} unique tags")

    if not unique_tags:
        print("Nothing to normalize.")
        return

    # Connect
    print(f"Connecting to LLM server: {API_BASE}")
    client = OpenAI(base_url=API_BASE, api_key=API_KEY, timeout=API_TIMEOUT)
    try:
        client.models.list()
        print("Server reachable.\n")
    except Exception as e:
        print(f"✗ Cannot reach {API_BASE}: {e}", file=sys.stderr)
        sys.exit(1)

    # Normalize each unique tag
    tag_map: dict[str, str] = {}
    t0 = time.time()
    for i, t in enumerate(unique_tags):
        tag_map[t] = llm_normalize(client, t)
        if (i + 1) % 25 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            eta = (len(unique_tags) - i - 1) / rate
            print(f"  [{i+1}/{len(unique_tags)}] {elapsed:.0f}s, ETA {eta:.0f}s")

    print(f"\nNormalized {len(unique_tags)} tags in {time.time()-t0:.0f}s")

    # Show before/after
    print("\nBefore → After (top changes):")
    changes = [(k, v) for k, v in tag_map.items() if k != v]
    for k, v in changes[:15]:
        print(f"  {k!r} → {v!r}")
    unchanged = len(unique_tags) - len(changes)
    print(f"  …{unchanged} unchanged")

    # Write output
    with open(OUTPUT_JSONL, "w") as out:
        for r in records:
            slim = {"path": r["path"], "tag": tag_map[r["tag"]]}
            out.write(json.dumps(slim, ensure_ascii=False) + "\n")

    final_unique = len({tag_map[r["tag"]] for r in records})
    print(f"\n{len(unique_tags)} → {final_unique} groups. → {OUTPUT_JSONL}")


if __name__ == "__main__":
    main()
