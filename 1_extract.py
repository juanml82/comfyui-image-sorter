#!/usr/bin/env python3
"""Scan images, extract ComfyUI workflow metadata, resolve prompts."""

import json
import sys
from pathlib import Path

from config import IMG_DIR
from PIL import Image


# ── Metadata extraction ──────────────────────────────────────────────

def get_raw_metadata(path: Path) -> str | None:
    try:
        with Image.open(path) as img:
            info = img.info

            # PNG: tEXt chunk
            if "parameters" in info:
                return info["parameters"]

            # WebP: workflow string inside EXIF bytes
            if "exif" in info:
                text = info["exif"].decode("latin-1", errors="ignore")
                for marker in ("workflow:", "prompt="):
                    idx = text.find(marker)
                    if idx != -1:
                        return text[idx:]

            if "xmp" in info:
                blob = info["xmp"].decode("utf-8", errors="ignore")
                for marker in ("workflow:", "prompt="):
                    idx = blob.find(marker)
                    if idx != -1:
                        return blob[idx:]

            if "comment" in info:
                return info["comment"]

    except Exception as e:
        print(f"  [warn] can't read {path.name}: {e}", file=sys.stderr)
    return None


def parse_workflow(raw: str) -> dict | None:
    if raw is None:
        return None

    if raw.startswith("workflow:"):
        raw = raw[len("workflow:"):]
        depth = 0
        end = 0
        for i, ch in enumerate(raw):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        raw = raw[:end]
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  [warn] bad workflow JSON (webp): {e}", file=sys.stderr)
            return None

    idx = raw.find("prompt=")
    if idx != -1:
        raw = raw[idx + len("prompt="):]
        ei_idx = raw.find(" extra_info=")
        if ei_idx != -1:
            raw = raw[:ei_idx]
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  [warn] bad workflow JSON (png): {e}", file=sys.stderr)
            return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ── Graph helpers ────────────────────────────────────────────────────

def build_link_index(workflow: dict) -> dict[int, tuple[int, int]]:
    """Map link_id → (src_node_id, src_slot)."""
    idx = {}
    for link in workflow.get("links", []):
        idx[link[0]] = (link[1], link[2])
    return idx


def get_node_by_id(workflow: dict, node_id: int) -> dict | None:
    for n in workflow.get("nodes", []):
        if n["id"] == node_id:
            return n
    return None


# Non-KSampler-prefixed nodes that act as top-level sampling entry points.
SAMPLER_EXACT_TYPES = frozenset({
    "SamplerCustomAdvanced",
    "SamplerCustom",
    "UltimateSDUpscale",
})


def is_sampler_node(node: dict) -> bool:
    """True if this node is an enabled, top-level sampler entry point."""
    if node.get("mode", 0) != 0:
        return False
    t = node.get("type", "")
    return t.startswith("KSampler") or t in SAMPLER_EXACT_TYPES


# Nodes between sampler and text-encode that expose a `positive` CONDITIONING input.
GUIDER_TYPES = frozenset({
    "CFGGuider",
    "CFGGuiderFlux",
    "BasicGuider",
    "FluxGuidance",
    "FluxGuidanceSimple",
})

# Leaf / terminal text nodes: their content lives in widgets_values or properties.
TEXT_LEAF_TYPES = frozenset({
    "PrimitiveNode",
    "ShowText",
    "ShowText|sdforge",
    "ShowText|pysssss",
    "ttN text",
    "Note",
})

# Combine nodes: join all STRING inputs.
COMBINE_TYPES = frozenset({
    "Concatenate Text",
    "String Concat",
    "CombineStrings",
    "Text Concatenate",
    "StringJoin",
    "CR Combine Prompt",          # ← ComfyUI_Comfyroll_CustomNodes
    "Text Concatenate | WAS",     # WAS suite variant (just in case)
    "Switch String",              # rgthree switch (single-branch concat)
})


# ── Prompt resolution ────────────────────────────────────────────────

def _input_link_id(node: dict, slot: int) -> int | None:
    """Return the link id for an input slot, or None if unlinked."""
    inputs = node.get("inputs", [])
    if slot < len(inputs):
        return inputs[slot].get("link")
    return None


def resolve_input_slot(node: dict, slot: int, workflow: dict,
                       link_idx: dict, visited: set, depth: int) -> str:
    """Resolve a single input slot: literal widget value or follow a link."""
    link_id = _input_link_id(node, slot)

    if link_id is None:
        wv = node.get("widgets_values", [])
        return wv[slot] if slot < len(wv) and isinstance(wv[slot], str) else ""

    src_id, _src_slot = link_idx.get(link_id, (None, None))
    if src_id is None:
        return ""
    src_node = get_node_by_id(workflow, src_id)
    if src_node is None:
        return ""

    # Bypassed / muted → treat as passthrough: use widget value if present
    if src_node.get("mode", 0) >= 2:
        wv = src_node.get("widgets_values", [])
        for v in wv:
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    # --- Enabled node: type dispatch ---
    stype = src_node.get("type", "")
    if stype == "PrimitiveNode":
        return str(src_node.get("properties", {}).get("value", ""))
    if stype in TEXT_LEAF_TYPES:
        wv = src_node.get("widgets_values", [])
        for v in wv:
            if isinstance(v, str) and len(v) > 3:
                return v
        return ""

    # Anything else: recurse for deeper chains.
    return resolve_text(src_node, workflow, link_idx, visited, depth + 1)

def resolve_text(node: dict, workflow: dict, link_idx: dict,
                 visited: set, depth: int) -> str:
    """Extract text from a node, following links up to 5 hops deep."""
    if depth > 5 or node["id"] in visited:
        # Terminal: first usable string widget value.
        wv = node.get("widgets_values", [])
        for v in wv:
            if isinstance(v, str) and len(v) > 3:
                return v
        props = node.get("properties", {})
        if "value" in props and isinstance(props["value"], str):
            return props["value"]
        return ""

    visited.add(node["id"])
    ntype = node.get("type", "")
    inputs = node.get("inputs", [])

    # --- easy stylesSelector: meaningful text is the linked `positive` input ---
    if ntype == "easy stylesSelector":
        return resolve_input_slot(node, 0, workflow, link_idx, visited, depth + 1)

    # --- Multi-input combine node: resolve ALL string slots and join ---
    if ntype in COMBINE_TYPES:
        parts = []
        for i, inp in enumerate(inputs):
            if inp.get("type") == "STRING":
                part = resolve_input_slot(node, i, workflow, link_idx,
                                          visited, depth + 1)
                if part:
                    parts.append(part.strip())
        return " ".join(p for p in parts if p)

    # --- CLIPTextEncodeFlux: two parallel text channels (clip_l + t5xxl) ---
    if ntype == "CLIPTextEncodeFlux":
        parts = []
        for i, inp in enumerate(inputs):
            name = inp.get("name", "").lower()
            if name in ("clip_l", "t5xxl", "t5xl", "text"):
                part = resolve_input_slot(node, i, workflow, link_idx,
                                          visited, depth + 1)
                if part:
                    parts.append(part.strip())
        return "\n".join(p for p in parts if p)

    # --- Standard path: first STRING input named text/value/clip_l/t5xxl ---
    text_slot = None
    for i, inp in enumerate(inputs):
        if inp.get("name", "").lower() in ("text", "value", "clip_l", "t5xxl") \
           and inp.get("type") == "STRING":
            text_slot = i
            break
    if text_slot is None:
        for i, inp in enumerate(inputs):
            if inp.get("type") == "STRING":
                text_slot = i
                break

    if text_slot is None:
        # No string input — text must be a widget value.
        wv = node.get("widgets_values", [])
        for v in wv:
            if isinstance(v, str) and len(v) > 3:
                return v
        props = node.get("properties", {})
        if "value" in props and isinstance(props["value"], str):
            return props["value"]
        return ""

    return resolve_input_slot(node, text_slot, workflow, link_idx,
                              visited, depth + 1)


def find_positive_source_node(workflow: dict, link_idx: dict) -> dict | None:
    """Find the positive-conditioning source for the primary (lowest-order) sampler.

    Handles:
      • KSampler.positive → CLIPTextEncode  (direct)
      • SamplerCustomAdvanced.guider → CFGGuider.positive → CLIPTextEncodeFlux  (indirect)

    When multiple samplers exist, the one with the lowest execution `order`
    wins (base model prompt over detailer/refiner prompt).
    """
    candidates = [n for n in workflow.get("nodes", []) if is_sampler_node(n)]
    if not candidates:
        return None

    primary = min(candidates, key=lambda n: (n.get("order", 999), n["id"]))
    inputs = primary.get("inputs", [])

    # --- Direct: sampler.positive → text-encode ---
    for i, inp in enumerate(inputs):
        if inp.get("name") == "positive" and inp.get("type") == "CONDITIONING":
            link_id = _input_link_id(primary, i)
            if link_id is not None:
                src_id, _ = link_idx.get(link_id, (None, None))
                if src_id is not None:
                    src = get_node_by_id(workflow, src_id)
                    if src is not None:
                        return src
            break

    # --- Indirect: sampler.guider → guider.positive → text-encode ---
    for i, inp in enumerate(inputs):
        if inp.get("name") == "guider" and inp.get("type") == "GUIDER":
            link_id = _input_link_id(primary, i)
            if link_id is not None:
                guid_id, _ = link_idx.get(link_id, (None, None))
                if guid_id is not None:
                    guid_node = get_node_by_id(workflow, guid_id)
                    if guid_node and guid_node.get("type") in GUIDER_TYPES:
                        g_inputs = guid_node.get("inputs", [])
                        for j, ginp in enumerate(g_inputs):
                            if ginp.get("name") == "positive" and \
                               ginp.get("type") == "CONDITIONING":
                                gl = _input_link_id(guid_node, j)
                                if gl is not None:
                                    sid, _ = link_idx.get(gl, (None, None))
                                    if sid is not None:
                                        src = get_node_by_id(workflow, sid)
                                        if src is not None:
                                            return src
            break

    return None


def extract_prompt_from_node(node: dict, workflow: dict,
                             link_idx: dict) -> str:
    """Given the positive-conditioning source node, pull out the prompt text.

    Handles:
      • CLIPTextEncode: text via widgets_values[0] or linked input
      • CLIPTextEncodeFlux: two channels (clip_l + t5xxl)
      • Custom CONDITIONING producers: first long string in widgets_values
      • Any other node: resolve_text as fallback
    """
    ntype = node.get("type", "")

    # Standard CLIPTextEncode
    if ntype == "CLIPTextEncode":
        return resolve_text(node, workflow, link_idx, set(), 0)

    # Flux dual-channel text encode
    if ntype == "CLIPTextEncodeFlux":
        return resolve_text(node, workflow, link_idx, set(), 0)

    # Custom CONDITIONING producer (Power Prompt, Prompt Expansion, etc.)
    wv = node.get("widgets_values", [])
    for v in wv:
        if isinstance(v, str) and len(v) > 3:
            return v.strip()

    # Fallback
    return resolve_text(node, workflow, link_idx, set(), 0)


def resolve_prompts(workflow: dict) -> str:
    """Return the positive prompt. Negatives are irrelevant for grouping."""
    link_idx = build_link_index(workflow)

    # Strategy 1: trace from primary sampler (direct or via guider)
    src = find_positive_source_node(workflow, link_idx)
    if src is not None:
        text = extract_prompt_from_node(src, workflow, link_idx)
        if text and len(text) > 3:
            return text.strip()

    # Strategy 2: any enabled CLIPTextEncode* whose title isn't "Negative"
    for node in workflow.get("nodes", []):
        ntype = node.get("type", "")
        if ntype not in ("CLIPTextEncode", "CLIPTextEncodeFlux"):
            continue
        if node.get("mode", 0) != 0:
            continue
        title = node.get("title", "").lower()
        if "negative" in title:
            continue
        text = extract_prompt_from_node(node, workflow, link_idx)
        if text and len(text) > 3:
            return text.strip()

    # Strategy 3: last resort — longest string widget value in enabled nodes
    best = ""
    for node in workflow.get("nodes", []):
        if node.get("mode", 0) != 0:
            continue
        for v in node.get("widgets_values", []):
            if isinstance(v, str) and len(v) > len(best):
                best = v
    return best.strip()


# ── Main sweep ───────────────────────────────────────────────────────

def main():
    exts = {".png", ".webp"}
    files = sorted(p for p in Path(IMG_DIR).rglob("*") if p.suffix.lower() in exts)
    print(f"Found {len(files)} images in {IMG_DIR}")

    out_path = Path(IMG_DIR) / "extract_results.jsonl"
    kept = 0
    failed = 0

    with open(out_path, "w") as out:
        for fp in files:
            raw = get_raw_metadata(fp)
            wf  = parse_workflow(raw)
            if wf is None:
                failed += 1
                continue

            pos = resolve_prompts(wf)
            resolved = bool(pos) and len(pos) > 3

            record = {
                "path": str(fp),
                "prompt": pos,
                "resolved": resolved,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            if resolved:
                kept += 1

    print(f"Done. {kept} resolved, {failed} unreadable/missing. → {out_path}")


if __name__ == "__main__":
    main()
