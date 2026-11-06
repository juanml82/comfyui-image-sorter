#!/usr/bin/env python3
"""Move/copy images into per-tag subfolders."""

import json
import shutil
import sys
from pathlib import Path

from config import IMG_DIR, out_root

INPUT = Path(IMG_DIR) / "merged.jsonl"
DRY_RUN = "--dry-run" in sys.argv
MODE    = "move"

if DRY_RUN:
    print("*** DRY RUN — no files will be touched ***")


def sanitize_folder_name(tag: str) -> str:
    safe = tag.replace("/", "_").replace("\\", "_").replace(" ", "_")
    safe = "".join(c for c in safe if c.isalnum() or c in ("_", "-"))
    return safe[:80] or "untagged"


def main():
    root = Path(out_root)
    root.mkdir(parents=True, exist_ok=True)

    records = []
    with open(INPUT) as f:
        for line in f:
            records.append(json.loads(line))

    total = len(records)
    still_there = sum(1 for r in records if Path(r["path"]).exists())
    already_moved = total - still_there
    if already_moved:
        print(f"  Note: {already_moved} files already absent from source "
              f"(likely moved in a prior run).")
    moved = 0
    conflicts = 0

    for rec in records:
        tag = sanitize_folder_name(rec["tag"])
        dest_dir = root / tag
        dest_file = dest_dir / Path(rec["path"]).name

        # ── NEW: skip files that were already moved ──────────────
        src = Path(rec["path"])
        if not src.exists():
            # File already gone — check if it landed at the destination
            if dest_file.exists():
                moved += 1          # count it as done
            else:
                print(f"  ⚠ MISSING: {src.name} (not at source or dest)")
            continue
        # ──────────────────────────────────────────────────────────

        if dest_file.exists():
            conflicts += 1
            if DRY_RUN:
                print(f"  [conflict] {Path(rec['path']).name} → {tag}/")
            continue

        if not DRY_RUN:
            dest_dir.mkdir(parents=True, exist_ok=True)
            if MODE == "move":
                shutil.move(str(src), str(dest_file))
            else:
                shutil.copy2(str(src), str(dest_file))

        moved += 1
        if DRY_RUN:
            print(f"  {src.name}  →  {tag}/")

    print(f"{'[DRY RUN] ' if DRY_RUN else ''}Processed {len(records)} files.")
    print(f"  Moved/copied: {moved}")
    print(f"  Conflicts (dest already exists): {conflicts}")
    if conflicts:
        print("  Re-run after resolving, or delete the conflicting dest files.")


if __name__ == "__main__":
    main()
