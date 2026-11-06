"""Shared helpers for the ComfyUI image organizer pipeline."""

import re
# This is just in case the server passes chain of thought. It's likely useless code, and using reasoning for this project adds considerable time to negible improvement anyway, so shouldn't be used.
# Ordered by specificity. First match wins.
_THINK_PATTERNS = [
    re.compile(r"<think>\s*(.*?)\s*</think>", re.DOTALL),
    re.compile(r"<thinking>\s*(.*?)</thinking>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<thought>\s*(.*?)</thought>", re.DOTALL | re.IGNORECASE),
    re.compile(r"/think\s*(.*?)/think", re.DOTALL),
    # Gemma 4 (and likely future Gemma)
    re.compile(r"<\|channel>thought\s*\n?(.*?)\n?\s*<channel\|>", re.DOTALL),
]

def strip_thinking(text: str) -> str:
    """Remove chain-of-thought from a model response."""
    for pat in _THINK_PATTERNS:
        m = pat.search(text)
        if m:
            return (text[:m.start()] + text[m.end():]).strip()

    for opener in ("<think>", "<thinking>", "<thought>", "/think",
                   "<|channel>thought"):
        idx = text.find(opener)
        if idx != -1:
            after = text[idx + len(opener):].strip()
            before = text[:idx].strip()
            return (before + " " + after).strip() if before else after

    return text.strip()
