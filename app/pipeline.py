"""Recording -> transcript. Each speaker's own track is transcribed separately (so 'who spoke' is known), shifted onto the
shared timeline, merged, and translated to Chinese where the ASR did not already."""
from fastapi import HTTPException
from sqlalchemy.orm import Session

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
            try:
                got = asr.transcribe_audio(part["path"], itv.language, part["role"])
            except Exception as e:  # provider failure must not look like "nothing was said"
                raise HTTPException(502, f"转写服务调用失败：{type(e).__name__}")
            for s in got:
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
