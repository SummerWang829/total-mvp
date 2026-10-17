"""Who has said how much, who has been quiet, who talks over whom: the arithmetic behind the room's speaking-fairness panel.

Input is the finished caption sentences (``CaptionLine``): speaker, text, the moment the sentence ended on OUR clock, and how long it took to say.
Because every browser captions only its own microphone, the speaker of each sentence is known for certain (no voice matching is needed).
Everything is a pure function of the sentences and the current time, so it can be recomputed at any moment, also after the interview.
"""
import re
from dataclasses import dataclass

from . import constants as K

_NOISE = re.compile(r"[\s\W_]+", re.UNICODE)
_HAN = re.compile(r"[一-鿿]")


@dataclass(frozen=True)
class Sentence:
    """One finished caption sentence."""

    speaker: str       # "moderator" or a seat key "p1" … "p10"
    text: str
    end_ms: int        # when it ended, ms since the interview started (our clock)
    dur_ms: int = 0    # how long it took to say (0 = unknown)

    @property
    def length_ms(self) -> int:
        """The duration, or an estimate from the text (never below K.MIN_SENTENCE_MS)."""
        if self.dur_ms > 0:
            return max(self.dur_ms, K.MIN_SENTENCE_MS)
        if _HAN.search(self.text):
            per_s = K.UNKNOWN_DURATION_CHARS_PER_S
            units = len(_NOISE.sub("", self.text))
        else:
            per_s = K.UNKNOWN_DURATION_WORDS_PER_S
            units = len(self.text.split())
        return max(int(units / per_s * 1000), K.MIN_SENTENCE_MS)

    @property
    def start_ms(self) -> int:
        """When it began: the end minus the length."""
        return max(0, self.end_ms - self.length_ms)


def is_informative(text: str) -> bool:
    """A sentence long enough, and not only a nod ("嗯", "对", "yeah", "I agree")."""
    flat = _NOISE.sub("", text.lower())
    trimmed = text.strip().lower().strip(".!?。！？，, ")
    if not flat or trimmed in K.FILLERS or flat in K.FILLERS:
        return False
    if _HAN.search(text):
        return len(flat) >= K.INFORMATIVE_MIN_CHARS
    return len(text.split()) >= K.INFORMATIVE_MIN_WORDS


def _merged_ms(spans: list[tuple[int, int]]) -> int:
    """Total length of the spans, counting stretches where they overlap only once."""
    total, current_start, current_end = 0, 0, -1
    for start, end in sorted(spans):
        if current_end < 0:
            current_start, current_end = start, end
        elif start > current_end:
            total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    return total + (current_end - current_start if current_end >= 0 else 0)


def _turns(sentences: list[Sentence]) -> int:
    """Number of turns: sentences of one speaker closer together than K.TURN_GAP_MS count as one."""
    turns, last_end = 0, None
    for s in sorted(sentences, key=lambda s: s.start_ms):
        if last_end is None or s.start_ms - last_end > K.TURN_GAP_MS:
            turns += 1
        last_end = max(last_end or 0, s.end_ms)
    return turns


def gini(values: list[float]) -> float:
    """Gini coefficient 0 (everyone equal) … 1 (one person has everything), rescaled by n/(n-1) so that a group of two or three can reach 1."""
    n = len(values)
    total = sum(values)
    if n < 2 or total <= 0:
        return 0.0
    ordered = sorted(values)
    raw = sum((2 * (i + 1) - n - 1) * v for i, v in enumerate(ordered)) / (n * total)
    return min(1.0, max(0.0, raw * n / (n - 1)))


def fairness(talk_ms: list[int]) -> float | None:
    """1 - gini of the participants' talk time, or None while there is too little talk to mean anything."""
    if len(talk_ms) < 2 or sum(talk_ms) < K.FAIRNESS_MIN_TALK_MS:
        return None
    return round(1 - gini([float(t) for t in talk_ms]), 3)


def fairness_word(value: float | None) -> str:
    """均衡 / 偏集中 / 严重偏集中 (or 数据还少)."""
    if value is None:
        return "数据还少"
    return "均衡" if value >= K.FAIRNESS_GOOD else "偏集中" if value >= K.FAIRNESS_FAIR else "严重偏集中"


def overlaps(sentences: list[Sentence]) -> list[tuple[str, str, int]]:
    """Stretches where two different people talk at once, for at least K.OVERLAP_MIN_MS.

    Returns:
        (interrupter, interrupted, overlap_ms): the one who began later is the interrupter. Each pair of sentences counts once.
    """
    found = []
    ordered = sorted(sentences, key=lambda s: s.start_ms)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if b.start_ms >= a.end_ms:
                break  # sorted by start: every later sentence starts even later, so none of them overlaps this one
            overlap = min(a.end_ms, b.end_ms) - b.start_ms
            if a.speaker != b.speaker and overlap >= K.OVERLAP_MIN_MS:
                found.append((b.speaker, a.speaker, overlap))
    return found


def _spark(sentences: list[Sentence], now_ms: int) -> list[int]:
    """Seconds of talk in each of the last K.SPARK_BUCKETS minutes, oldest first."""
    buckets = [0] * K.SPARK_BUCKETS
    first = now_ms - K.SPARK_BUCKETS * K.SPARK_BUCKET_MS
    for s in sentences:
        for index in range(K.SPARK_BUCKETS):
            lo, hi = first + index * K.SPARK_BUCKET_MS, first + (index + 1) * K.SPARK_BUCKET_MS
            buckets[index] += max(0, min(s.end_ms, hi) - max(s.start_ms, lo))
    return [round(b / 1000) for b in buckets]


def _state(share: float, people: int, talk_total: int, silent_s: int | None, said_anything: bool, now_ms: int) -> str:
    """ok / quiet / high / silent for one participant (silent beats the others)."""
    judged = now_ms >= K.JUDGE_AFTER_ELAPSED_MS
    if judged and (not said_anything or (silent_s is not None and silent_s >= K.SILENT_RED_S)):
        return "silent"
    if talk_total >= K.JUDGE_AFTER_TALK_MS:
        if share > max(K.HIGH_SHARE_MIN, K.HIGH_SHARE_TIMES / people):
            return "high"
        if share < K.LOW_SHARE_TIMES / people:
            return "quiet"
    if judged and silent_s is not None and silent_s >= K.SILENT_YELLOW_S:
        return "quiet"
    return "ok"


def _hints(rows: list[dict], moderator_interrupts: int) -> list[str]:
    """A few plain sentences for the moderator, most urgent first (silent, then too much, then being talked over)."""
    out = []
    for r in rows:
        if r["state"] == "silent":
            out.append(f"{r['label']} " + (f"已 {r['silent_s'] // 60} 分钟没发言" if r["silent_s"] is not None else "还没发过言"))
    for r in rows:
        if r["state"] == "high":
            out.append(f"{r['label']} 已占 {round(r['share'] * 100)}% 的发言时间")
    for r in rows:
        if r["interrupted"] >= 2:
            out.append(f"{r['label']} 被打断了 {r['interrupted']} 次")
    if moderator_interrupts >= 2:
        out.append(f"主持人打断参与者 {moderator_interrupts} 次")
    return out[:K.MAX_HINTS]


def compute(sentences: list[Sentence], seats: list[int], now_ms: int) -> dict:
    """The whole dashboard for one moment.

    Args:
        sentences: every finished caption sentence so far (moderator and seats).
        seats: the seat numbers of the group (a seat that never spoke still gets a row).
        now_ms: milliseconds since the interview started, on our clock.

    Returns:
        {"participants": [...], "moderator": {...}, "fairness": float | None, "fairness_word", "fairness_series": [[minute, value], ...],
         "expected_share", "hints": [...]}. Seat rows are in seat order; the moderator is not part of the fairness measure.
    """
    by_speaker: dict[str, list[Sentence]] = {}
    for s in sentences:
        by_speaker.setdefault(s.speaker, []).append(s)
    keys = [f"p{n}" for n in seats]
    talk = {k: _merged_ms([(s.start_ms, s.end_ms) for s in by_speaker.get(k, [])]) for k in keys}
    talk_total = sum(talk.values())
    pair_overlaps = overlaps(sentences)
    people = max(1, len(keys))
    minutes = max(now_ms / 60000, 1 / 60)
    rows = []
    for n, key in zip(seats, keys):
        mine = by_speaker.get(key, [])
        share = talk[key] / talk_total if talk_total else 0.0
        last_end = max((s.end_ms for s in mine), default=None)
        silent_s = None if last_end is None else max(0, (now_ms - last_end) // 1000)
        informative = sum(1 for s in mine if is_informative(s.text))
        rows.append({
            "seat": n, "label": f"参与者 ·{n:02d}", "talk_s": round(talk[key] / 1000), "share": round(share, 3),
            "turns": _turns(mine), "sentences": len(mine), "informative": informative, "density": round(informative / minutes, 2),
            "silent_s": silent_s, "spark": _spark(mine, now_ms),
            "interrupts": sum(1 for who, _, _ in pair_overlaps if who == key), "interrupted": sum(1 for _, victim, _ in pair_overlaps if victim == key),
            "state": _state(share, people, talk_total, silent_s, bool(mine), now_ms),
        })
    mod = by_speaker.get(K.MODERATOR_KEY, [])
    mod_talk = _merged_ms([(s.start_ms, s.end_ms) for s in mod])
    all_talk = talk_total + mod_talk
    moderator = {"talk_s": round(mod_talk / 1000), "share_of_all": round(mod_talk / all_talk, 3) if all_talk else 0.0,
                 "interrupts": sum(1 for who, _, _ in pair_overlaps if who == K.MODERATOR_KEY)}
    series = []
    for at in range(K.FAIRNESS_SERIES_STEP_MS, now_ms + 1, K.FAIRNESS_SERIES_STEP_MS):
        cumulative = [_merged_ms([(s.start_ms, min(s.end_ms, at)) for s in by_speaker.get(k, []) if s.start_ms < at]) for k in keys]
        point = fairness(cumulative)
        if point is not None:
            series.append([at // 60000, point])
    value = fairness([talk[k] for k in keys])
    return {"participants": rows, "moderator": moderator, "fairness": value, "fairness_word": fairness_word(value), "fairness_series": series,
            "expected_share": round(1 / people, 3), "hints": _hints(rows, moderator["interrupts"])}
