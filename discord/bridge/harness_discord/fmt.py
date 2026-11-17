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
        room = max(200, limit - (len(fence) + 1 if fence else 0) - 4)     # room for the reopened/closing fence
        if len(text) <= room:
            piece, text = text, ""
        else:
            k = _cut_point(text, room)
            piece, text = text[:k].rstrip("\n"), text[k:].lstrip("\n")
        if fence:
            piece = fence + "\n" + piece
        opens = [m.group(0) for m in re.finditer(r"^```[^\n]*", piece, re.M) if "```" not in m.group(0)[3:]]   # ```code``` on one line is closed
        if len(opens) % 2 == 1:
            fence = opens[-1][:40]
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


def cut_lines(text: str, n: int) -> str:
    """Cut at n characters on a whole line (no half bold mark or timestamp), with a mark that something is missing."""
    if len(text) <= n:
        return text
    cut = text[: max(0, n - 2)]
    nl = cut.rfind("\n")
    return (cut[:nl] if nl > n // 2 else cut) + "\n…"


def embed_size(e: dict) -> int:
    """What Discord counts against the 6000 characters of all the embeds of a message."""
    size = len(e.get("title") or "") + len(e.get("description") or "")
    size += len((e.get("footer") or {}).get("text") or "") + len((e.get("author") or {}).get("name") or "")
    for f in e.get("fields") or []:
        size += len(f.get("name") or "") + len(f.get("value") or "")
    return size


def fit_embed(e: dict, limit: int = 5900) -> dict:
    """The embed within Discord's limits (title 256, description 4096, 25 fields of 256 / 1024, 6000 in all): the
    description is shortened first, then the last fields go. A copy; the original is not changed."""
    e = dict(e)
    if e.get("title"):
        e["title"] = clip(e["title"], 256)
    if e.get("description"):
        e["description"] = cut_lines(e["description"], 4096)
    fields = [dict(f, name=clip(str(f.get("name") or "–"), 256) or "–", value=cut_lines(str(f.get("value") or "–"), 1024) or "–")
              for f in (e.get("fields") or [])[:25]]
    if fields:
        e["fields"] = fields
    over = embed_size(e) - limit
    if over > 0 and e.get("description"):
        e["description"] = cut_lines(e["description"], max(0, len(e["description"]) - over))
    while embed_size(e) > limit and e.get("fields"):
        e["fields"] = e["fields"][:-1]
    return e
