#!/usr/bin/env python3
"""Tag images using a vision-language model via an OpenAI-compatible server."""

import base64
import json
import sys
import time
from io import BytesIO
from pathlib import Path

from openai import OpenAI
from PIL import Image

from config import (
    IMG_DIR,
    API_BASE,
    API_MODEL,
    API_KEY,
    API_TIMEOUT,
    PROMPT_VISION,
)

INPUT_DIR    = Path(IMG_DIR)
OUTPUT_JSONL = Path(IMG_DIR) / "vision_tags.jsonl"

from utils import strip_thinking
from config import IMG_DIR, API_BASE, API_MODEL, API_KEY, API_TIMEOUT, PROMPT_VISION

MAX_DIM = 2048  # downscale cap to keep payloads small

def encode_image(img: Image.Image) -> str:
    """Return a base64 data URI for the PIL image."""
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def clean_tag(raw: str) -> str:
    tag = raw.strip().lower()
    for prefix in ("tag:", "label:", "the ", "a ", "an "):
        if tag.startswith(prefix):
            tag = tag[len(prefix):]
    tag = tag.replace(",", " ").replace(".", " ").replace(";", " ")
    tag = " ".join(tag.split())
    words = tag.split()[:6]
    return " ".join(words) if words else "untagged"


def llm_tag_vision(client: OpenAI, img: Image.Image) -> str:
    data_uri = encode_image(img)
    res = client.chat.completions.create(
        model=API_MODEL or None,
        messages=[
            {"role": "system", "content": PROMPT_VISION},
            {"role": "user", "content": [
                {"type": "text", "text": "Tag this image:"},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]},
        ],
        max_tokens=30,
        temperature=0.0,
        top_p=1.0,
        stop=["<|im_end|>", "<|im_start|>"],
    )
    content = res.choices[0].message.content

    # ── strip thinking (no-op if absent) ──
    reasoning = getattr(res.choices[0].message, "reasoning_content", None)
    if reasoning is None:
        content = strip_thinking(content)
    return clean_tag(content)


def main():
    exts = {".png", ".webp", ".jpg", ".jpeg"}
    files = sorted(
        p for p in INPUT_DIR.rglob("*") if p.suffix.lower() in exts
    )
    print(f"Found {len(files)} images in {IMG_DIR}")

    if not files:
        print("No images found. Nothing to do.")
        return

    # ── Connect to server ───────────────────────────────────────────
    print(f"Connecting to vision server: {API_BASE}")
    client = OpenAI(
        base_url=API_BASE,
        api_key=API_KEY,
        timeout=API_TIMEOUT,
    )

    # Quick connectivity check
    try:
        client.models.list()
        print("Server reachable.\n")
    except Exception as e:
        print(f"✗ Cannot reach {API_BASE}: {e}", file=sys.stderr)
        sys.exit(1)

    # ── Tag each image ──────────────────────────────────────────────
    results: list[dict] = []
    skipped = 0
    t0 = time.time()

    for i, fp in enumerate(files):
        try:
            img = Image.open(fp).convert("RGB")

            # Downscale very large images
            w, h = img.size
            if max(w, h) > MAX_DIM:
                scale = MAX_DIM / max(w, h)
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

            tag = llm_tag_vision(client, img)

            if i == 0:
                # ── DEBUG: print raw response ──
                data_uri = encode_image(img)
                raw_res = client.chat.completions.create(
                    model=API_MODEL or None,
                    messages=[
                        {"role": "system", "content": PROMPT_VISION},
                        {"role": "user", "content": [
                            {"type": "text", "text": "Tag this image:"},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ]},
                    ],
                    max_tokens=30,
                    temperature=0.0,
                    top_p=1.0,
                )
                print(f"\n[DEBUG] finish_reason: {raw_res.choices[0].finish_reason}")
                print(f"[DEBUG] content:        {raw_res.choices[0].message.content!r}")
                print(f"[DEBUG] usage:          {raw_res.usage}")
                print(f"[DEBUG] image size:     {img.size}, encoded {len(data_uri)//1024} KB\n")

        except Exception as e:
            tag = "untagged"
            skipped += 1
            import traceback; traceback.print_exc()
            print(f"  [warn] {fp.name}: {e}", file=sys.stderr)

        results.append({"path": str(fp), "tag": tag})

        if (i + 1) % 25 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            eta = (len(files) - i - 1) / rate
            print(
                f"  [{i + 1}/{len(files)}] {elapsed:.0f}s elapsed, "
                f"ETA {eta:.0f}s",
                flush=True,
            )

    total_time = time.time() - t0
    print(f"\nTagged {len(files)} images in {total_time:.0f}s "
          f"({skipped} errors)")

    # Show top tags for sanity check
    from collections import Counter
    tag_counts = Counter(r["tag"] for r in results)
    print("\nTop 20 tags:")
    for tag, n in tag_counts.most_common(20):
        print(f"  {n:5d}  {tag}")

    # ── Write output ────────────────────────────────────────────────
    with open(OUTPUT_JSONL, "w") as out:
        for r in results:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n→ {OUTPUT_JSONL}")


if __name__ == "__main__":
    main()
