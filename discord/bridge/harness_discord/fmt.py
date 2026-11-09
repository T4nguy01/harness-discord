"""Text for Discord: tables -> bullets, split in messages of at most 2000 characters (code fences stay balanced)."""
from __future__ import annotations

import re

LIMIT = 2000


def tables_to_bullets(text: str) -> str:
    """Discord does not draw markdown tables: one bullet per row, with the header names."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    in_code = False
    while i < len(lines):
        ln = lines[i]
        if ln.lstrip().startswith("```"):
            in_code = not in_code
        is_sep = i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", lines[i + 1] or "")
        if not in_code and "|" in ln and is_sep:
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                parts = [f"**{h}**: {c}" if h else c for h, c in zip(head, cells) if c]
                out.append("- " + " · ".join(parts))
                i += 1
            continue
        out.append(ln)
        i += 1
    return "\n".join(out)


def _cut_point(s: str, limit: int) -> int:
    """Best place to cut s[:limit]: paragraph, then line, then space, else hard."""
    for sep in ("\n\n", "\n", " "):
        k = s.rfind(sep, 0, limit)
        if k > limit * 0.4:
            return k + len(sep)
    return limit


def chunk(text: str, limit: int = LIMIT) -> list[str]:
    text = tables_to_bullets(text).strip()
    if not text:
        return []
    parts: list[str] = []
    fence = ""            # "```lang" while a code block is open across a cut
    while text:
        room = limit - (len(fence) + 1 if fence else 0) - 4     # room for the reopened/closing fence
        if len(text) <= room:
            piece, text = text, ""
        else:
            k = _cut_point(text, room)
            piece, text = text[:k].rstrip("\n"), text[k:].lstrip("\n")
        if fence:
            piece = fence + "\n" + piece
        opens = [m.group(0) for m in re.finditer(r"^```[^\n]*", piece, re.M)]
        if len(opens) % 2 == 1:
            fence = opens[-1] if opens[-1] != "```" else "```"
            piece += "\n```"
        else:
            fence = ""
        parts.append(piece)
    return parts


def clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def clip_block(s: str, n: int) -> str:
    """Keep line breaks (an alert body is a few lines), cut at n characters."""
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"
