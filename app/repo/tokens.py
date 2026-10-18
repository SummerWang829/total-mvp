"""Turning text into search terms without a language-specific dictionary: Latin words as they are, and every run of Chinese characters as overlapping
pairs of characters (so "价格太贵" is found by "太贵" or "价格"). Enough for ranking inside one company's own research."""
import re

_RUN = re.compile(r"[a-z0-9]+|[㐀-鿿]+")
STOP_WORDS = frozenset({"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "were", "it", "this", "that", "what", "why", "how",
                        "do", "does", "did", "they", "their", "with", "about", "from", "be", "can", "which", "who", "when", "where", "i", "we", "you"})
STOP_PAIRS = frozenset({"为什", "什么", "如何", "怎么", "怎样", "是否", "有没", "没有", "哪些", "哪个", "一些", "这些", "那些", "这个", "那个", "我们", "他们", "她们", "它们", "的话", "是不"})


def tokens(text: str) -> list[str]:
    """The search terms of a text, in order (repeats kept: they carry frequency)."""
    out: list[str] = []
    for run in _RUN.findall(text.lower()):
        if run[0] < "㐀":
            if len(run) >= 2 and run not in STOP_WORDS:
                out.append(run)
        elif len(run) == 1:
            out.append(run)
        else:
            out.extend(p for p in (run[i:i + 2] for i in range(len(run) - 1)) if p not in STOP_PAIRS)
    return out
