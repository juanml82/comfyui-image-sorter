#!/usr/bin/env python3
"""LLM-normalize prompts into short canonical tags via OpenAI-compatible server."""

import json
import sys
import time
from collections import Counter
from pathlib import Path

from openai import OpenAI
from config import (
    API_BASE, API_MODEL, API_KEY, API_TIMEOUT,
    MAX_PROMPT_LEN, IMG_DIR,
)

INPUT_JSONL  = Path(IMG_DIR) / "extract_results.jsonl"
OUTPUT_JSONL = Path(IMG_DIR) / "canonicalized.jsonl"

from utils import strip_thinking

from config import API_BASE, API_MODEL, API_KEY, API_TIMEOUT, MAX_PROMPT_LEN, IMG_DIR, PROMPT_CANONICALIZE

def llm_tag(client: OpenAI, prompt: str) -> str:
    p = prompt[:MAX_PROMPT_LEN]
    res = client.chat.completions.create(
        model=API_MODEL or None,
        messages=[
            {"role": "system", "content": PROMPT_CANONICALIZE},
            {"role": "user",   "content": p},
        ],
        max_tokens=40,
        temperature=0,
        top_p=1.0,
        seed=42,
    )
    content = (res.choices[0].message.content or "").strip()
    reasoning = getattr(res.choices[0].message, "reasoning_content", None)

    # Strip thinking from whichever field it landed in
    if reasoning:
        # thinking was separated by the server; content should be clean
        pass
    else:
        content = strip_thinking(content)

    tag = content.lower().strip()
    return tag if tag else "untagged"

def main():
    # Connect
    print(f"Connecting to LLM server: {API_BASE}")
    client = OpenAI(base_url=API_BASE, api_key=API_KEY, timeout=API_TIMEOUT)

    try:
        client.models.list()
        print("Server reachable.\n")
    except Exception as e:
        print(f"✗ Cannot reach {API_BASE}: {e}", file=sys.stderr)
        sys.exit(1)

    # Read extracted records
    if not INPUT_JSONL.exists():
        print(f"✗ Not found: {INPUT_JSONL} — run 1_extract.py first.", file=sys.stderr)
        sys.exit(1)

    records = []
    with open(INPUT_JSONL) as f:
        for line in f:
            rec = json.loads(line)
            if rec["resolved"]:
                records.append(rec)

    # Deduplicate prompts
    unique_prompts = list({r["prompt"] for r in records})
    print(f"{len(records)} images, {len(unique_prompts)} unique prompts")

    # Tag each unique prompt
    prompt_to_tag: dict[str, str] = {}
    t0 = time.time()
    for i, p in enumerate(unique_prompts):
        tag = llm_tag(client, p)
        prompt_to_tag[p] = tag
        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            eta = (len(unique_prompts) - i - 1) / rate
            print(f"  [{i+1}/{len(unique_prompts)}] {elapsed:.0f}s elapsed, ETA {eta:.0f}s")

    print(f"Tagged {len(unique_prompts)} prompts in {time.time()-t0:.0f}s")

    # Show tag frequency (top 20) so you can sanity-check
    tag_counts = Counter(prompt_to_tag.values())
    print("\nTop 20 tags:")
    for tag, n in tag_counts.most_common(20):
        print(f"  {n:5d}  {tag}")

    # Write out
    with open(OUTPUT_JSONL, "w") as out:
        for r in records:
            slim = {"path": r["path"], "tag": prompt_to_tag[r["prompt"]]}
            out.write(json.dumps(slim, ensure_ascii=False) + "\n")

    print(f"\n→ {OUTPUT_JSONL}")


if __name__ == "__main__":
    main()
