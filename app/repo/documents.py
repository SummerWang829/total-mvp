"""Imported documents (earlier reports, notes, desk research) are cut into paragraph-sized pieces so a hit points at a passage, not a whole file."""
import re

MAX_CHARS = 200_000
PIECE_CHARS = 500


def pieces(text: str) -> list[str]:
    """Split on blank lines, merge tiny paragraphs, and cut over-long ones at sentence ends so each piece is about PIECE_CHARS or less."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r", "")) if p.strip()]
    out: list[str] = []
    buf = ""
    for para in paras:
        for part in _cut(para):
            if buf and len(buf) + len(part) + 1 > PIECE_CHARS:
                out.append(buf)
                buf = ""
            buf = f"{buf}\n{part}".strip()
    if buf:
        out.append(buf)
    return out


def _cut(para: str) -> list[str]:
    if len(para) <= PIECE_CHARS:
        return [para]
    sentences = [s for s in re.split(r"(?<=[。！？.!?])\s*", para) if s]
    out, cur = [], ""
    for s in sentences:
        while len(s) > PIECE_CHARS:  # a sentence with no stop at all: cut it hard
            if cur:
                out.append(cur)
                cur = ""
            out.append(s[:PIECE_CHARS])
            s = s[PIECE_CHARS:]
        if cur and len(cur) + len(s) > PIECE_CHARS:
            out.append(cur)
            cur = ""
        cur += s
    if cur:
        out.append(cur)
    return out
