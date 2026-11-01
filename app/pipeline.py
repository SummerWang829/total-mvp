"""Recording -> transcript. Each speaker's own track is transcribed separately (so 'who spoke' is known), shifted onto the
shared timeline, merged, and translated to Chinese where the ASR did not already."""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .asr_file import ASRError, ASRUnsupported
from .config import settings
from .models import Interview, Recording
from .providers import get_asr, get_llm
from .recording import audio_parts, has_tracks


def _fit_to_duration(segs: list[dict], duration_ms: int) -> None:
    """Mock transcripts are a fixed sample; squeeze them into the real recording length so clips stay in range."""
    end = max((s["end_ms"] for s in segs), default=0)
    if duration_ms and end > duration_ms:
        k = duration_ms * 0.98 / end
        for s in segs:
            s["start_ms"], s["end_ms"] = int(s["start_ms"] * k), max(int(s["end_ms"] * k), int(s["start_ms"] * k) + 200)


def _transcribe_part(asr, part: dict, language: str) -> list[dict]:
    """One speaker's track through the ASR service; provider errors become HTTP errors that never carry a secret."""
    try:
        return asr.transcribe_audio(part["path"], language, part["role"])
    except ASRUnsupported as e:
        raise HTTPException(422, str(e))
    except ASRError as e:  # the message names the service's error code, never a secret
        raise HTTPException(502, str(e))
    except Exception as e:  # provider failure must not look like "nothing was said"
        raise HTTPException(502, f"转写服务调用失败：{type(e).__name__}")


def voice_segments(db: Session, itv: Interview, rec: Recording) -> list[dict]:
    """An AI interview's own speech: what the researcher said while in control (their recorded track), placed on the
    interview's timeline. The recording starts at its own moment, so its offsets are shifted by that gap.

    Returns:
        Transcript lines with speaker ``moderator``; empty when nobody took over with a microphone.
    """
    parts = [p for p in audio_parts(db, rec) if p["role"] == "moderator"]
    if not parts:
        return []
    try:
        asr = get_asr()
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    base = int(((rec.started_at - (itv.started_at or rec.started_at)).total_seconds()) * 1000)
    out = []
    for part in parts:
        for s in _transcribe_part(asr, part, itv.language):
            start = max(0, s["start_ms"] + part["offset_ms"] + base)
            out.append(dict(speaker="moderator", start_ms=start, end_ms=max(start, s["end_ms"] + part["offset_ms"] + base), text_orig=s["text"],
                            text_zh=s.get("text_zh", ""), lang=s.get("lang", "")))
    if itv.language != "zh":
        todo = [s for s in out if not s["text_zh"]]
        if todo:
            for s, zh in zip(todo, get_llm().translate([s["text_orig"] for s in todo])):
                s["text_zh"] = zh
    return out


def build_segments(db: Session, itv: Interview, rec: Recording) -> list[dict]:
    try:
        asr = get_asr()
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    segs: list[dict] = []
    if has_tracks(rec):
        parts = audio_parts(db, rec)
        if settings.asr_provider == "mock":  # the mock "hears" a fixed script per speaker: use it once per speaker, not once per page reload
            parts = [p for i, p in enumerate(parts) if p["role"] not in {q["role"] for q in parts[:i]}]
        for part in parts:
            for s in _transcribe_part(asr, part, itv.language):
                segs.append(dict(speaker=part["role"], start_ms=s["start_ms"] + part["offset_ms"], end_ms=s["end_ms"] + part["offset_ms"], text_orig=s["text"], text_zh=s.get("text_zh", ""), lang=s.get("lang", "")))
    elif hasattr(asr, "transcribe"):  # dev: no recording file exists, use the whole-conversation sample
        segs = [{k: v for k, v in s.items() if k != "idx"} for s in asr.transcribe(rec.storage_key, itv.language)]
    else:
        raise HTTPException(409, "没有找到录像文件，无法转写")
    if not segs:
        raise HTTPException(409, "录音里没有识别到说话内容")
    if settings.asr_provider == "mock":
        _fit_to_duration(segs, rec.duration_ms or 0)
    segs.sort(key=lambda s: (s["start_ms"], s["end_ms"]))
    for i, s in enumerate(segs):
        s["idx"] = i
        if rec.duration_ms:
            s["end_ms"] = min(s["end_ms"], rec.duration_ms)
            s["start_ms"] = min(s["start_ms"], s["end_ms"])
    if itv.language != "zh":
        todo = [s for s in segs if not s.get("text_zh")]
        if todo:
            for s, zh in zip(todo, get_llm().translate([s["text_orig"] for s in todo])):
                s["text_zh"] = zh
    return segs
