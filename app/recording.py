"""Recording pipeline, no LiveKit Egress needed.

Each side (moderator, participant) records its OWN camera+microphone in the browser (MediaRecorder) while the server says
"recording" and uploads 4-second chunks. Observers never publish anything, so they are never recorded. When recording
stops, `finalize()` stitches each speaker's chunks, then mixes them into one MP4 (side-by-side video, mixed audio).
Bonus: we know who said what from the track, so no speaker-diarization is needed for the transcript.
Production can swap this for LiveKit Egress without touching anything downstream (transcript, clips, sharing).
"""
import glob
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import audit
from .config import settings
from .models import Recording, RecordingTrack, utcnow

ROLES = ("moderator", "participant")
TILE_W, TILE_H, BG = 640, 360, "0x0B382C"
_locks: dict[str, threading.Lock] = {}


def rec_dir(rec: Recording) -> str:
    return os.path.join(settings.media_dir, rec.storage_key.rsplit(".", 1)[0])


def rec_file(rec: Recording) -> str:
    return os.path.join(settings.media_dir, rec.storage_key)


def run(cmd: list[str], timeout: int = 600) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "")[-400:])
    return p.stdout + p.stderr


def probe(path: str) -> dict:
    out = subprocess.run([settings.ffprobe, "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json", path],
                         capture_output=True, text=True, timeout=60).stdout
    d = json.loads(out or "{}")
    kinds = {s.get("codec_type") for s in d.get("streams", [])}
    try:
        dur = float(d.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        dur = 0.0
    return {"video": "video" in kinds, "audio": "audio" in kinds, "duration_ms": int(dur * 1000)}


# ---------- chunk upload ----------
def elapsed_ms(rec: Recording) -> int:
    return int(((rec.stopped_at or utcnow()) - rec.started_at).total_seconds() * 1000)


def clean_part(part: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (part or "0").lower())[:16] or "0"


def save_chunk(db: Session, rec: Recording, role: str, part: str, seq: int, offset_ms: int, ext: str, data: bytes, last: bool) -> None:
    if role not in ROLES:
        raise ValueError(role)
    part, ext = clean_part(part), (ext if ext in ("webm", "mp4", "ogg") else "webm")
    d = os.path.join(rec_dir(rec), role, part)
    os.makedirs(d, exist_ok=True)
    if data:
        tmp = os.path.join(d, f"{seq:06d}.tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, os.path.join(d, f"{seq:06d}.part"))
    t = db.scalar(select(RecordingTrack).where(RecordingTrack.recording_id == rec.id, RecordingTrack.role == role, RecordingTrack.part == part))
    if not t:
        t = RecordingTrack(recording_id=rec.id, role=role, part=part, ext=ext, offset_ms=max(0, offset_ms))
        db.add(t)
    if seq == 0:
        t.offset_ms, t.ext = max(0, offset_ms), ext
    t.chunks = max(t.chunks or 0, seq + (1 if data else 0))
    if last:
        t.last_seq = seq
    db.commit()


# ---------- finalize: stitch + mix ----------
def _stitch(rec: Recording, t: RecordingTrack) -> dict | None:
    pdir = os.path.join(rec_dir(rec), t.role, t.part)
    parts = sorted(glob.glob(os.path.join(pdir, "*.part")))
    if not parts:
        return None
    raw = os.path.join(rec_dir(rec), f"{t.role}.{t.part}.raw.{t.ext}")
    with open(raw, "wb") as out:
        for p in parts:
            with open(p, "rb") as f:
                out.write(f.read())
    fixed = os.path.join(rec_dir(rec), f"{t.role}.{t.part}.mkv")  # remux so the file knows its duration
    try:
        run([settings.ffmpeg, "-y", "-v", "error", "-i", raw, "-c", "copy", fixed], timeout=300)
    except RuntimeError:
        return None
    info = probe(fixed)
    if not info["duration_ms"] or not (info["audio"] or info["video"]):
        return None
    os.remove(raw)
    shutil.rmtree(pdir, ignore_errors=True)
    return {"role": t.role, "part": t.part, "path": fixed, "offset_ms": t.offset_ms, **info}


def _mix(built: list[dict], out: str) -> int:
    """One tile per speaker (side by side), all audio mixed. A speaker who reloaded the page has several parts: they never
    overlap in time, so their pictures are laid onto the speaker's tile one after another. Returns total duration (ms)."""
    total = max(b["offset_ms"] + b["duration_ms"] for b in built)
    cmd, fc, tiles, alabels = [settings.ffmpeg, "-y", "-v", "error"], [], [], []
    for b in built:
        cmd += ["-i", b["path"]]
    n_in = len(built)
    for r_i, role in enumerate(r for r in ROLES if any(b["role"] == r for b in built)):
        cmd += ["-f", "lavfi", "-i", f"color=c={BG}:s={TILE_W}x{TILE_H}:r=25"]  # the tile background (also what shows when a camera is off)
        cur, n_in = f"{n_in}:v", n_in + 1
        for i, b in enumerate(built):
            if b["role"] != role:
                continue
            if b["video"]:
                fc.append(f"[{i}:v:0]fps=25,scale={TILE_W}:{TILE_H}:force_original_aspect_ratio=decrease,pad={TILE_W}:{TILE_H}:(ow-iw)/2:(oh-ih)/2:color={BG},setsar=1,"
                          f"setpts=PTS-STARTPTS+{b['offset_ms'] / 1000:.3f}/TB[pv{i}]")
                fc.append(f"[{cur}][pv{i}]overlay=eof_action=pass:repeatlast=0[ov{i}]")
                cur = f"ov{i}"
            if b["audio"]:
                fc.append(f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo,adelay=delays={int(b['offset_ms'])}:all=1[a{i}]")
                alabels.append(f"[a{i}]")
        fc.append(f"[{cur}]setsar=1[tile{r_i}]")
        tiles.append(f"[tile{r_i}]")
    fc.append(("".join(tiles) + f"hstack=inputs={len(tiles)}[v]") if len(tiles) > 1 else f"{tiles[0]}null[v]")
    if not alabels:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        fc.append(f"[{n_in}:a]anull[a]")
    elif len(alabels) == 1:
        fc.append(f"{alabels[0]}anull[a]")
    else:
        fc.append("".join(alabels) + f"amix=inputs={len(alabels)}:duration=longest:normalize=0[a]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]", "-t", f"{total / 1000:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out]
    run(cmd, timeout=int(total / 1000 * 3) + 180)
    return int(probe(out)["duration_ms"] or total)


def finalize(db: Session, rec: Recording) -> None:
    built = [b for t in db.scalars(select(RecordingTrack).where(RecordingTrack.recording_id == rec.id)) if (b := _stitch(rec, t))]
    if not built:
        rec.status, rec.error = "failed", "没有收到可用的录像数据（浏览器没有上传，或文件损坏）"
        audit(db, rec.org_id, "system", "recording.failed", rec.interview_id, reason="no_data")
        db.commit()
        return
    built.sort(key=lambda b: (ROLES.index(b["role"]), b["offset_ms"]))
    rec.duration_ms = _mix(built, rec_file(rec))
    rec.status, rec.error = "ready", ""
    audit(db, rec.org_id, "system", "recording.ready", rec.interview_id, speakers=sorted({b["role"] for b in built}), parts=len(built), duration_ms=rec.duration_ms)
    db.commit()


def audio_parts(db: Session, rec: Recording) -> list[dict]:
    """Every speaker audio stream as mono 16 kHz mp3 (small enough for upload limits), with its start offset on the shared timeline."""
    out = []
    for t in db.scalars(select(RecordingTrack).where(RecordingTrack.recording_id == rec.id)):
        src = os.path.join(rec_dir(rec), f"{t.role}.{t.part}.mkv")
        if not os.path.isfile(src) or not probe(src)["audio"]:
            continue
        mp3 = os.path.join(rec_dir(rec), f"{t.role}.{t.part}.mp3")
        if not os.path.isfile(mp3):
            run([settings.ffmpeg, "-y", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", mp3], timeout=600)
        out.append({"role": t.role, "path": mp3, "offset_ms": t.offset_ms})
    return sorted(out, key=lambda x: x["offset_ms"])


def has_tracks(rec: Recording) -> bool:
    return bool(glob.glob(os.path.join(rec_dir(rec), "*.mkv")))


# ---------- background worker ----------
def process_pending(db: Session, now=None) -> int:
    now = now or utcnow()
    done = 0
    for rec in db.scalars(select(Recording).where(Recording.status == "processing")).all():
        tracks = db.scalars(select(RecordingTrack).where(RecordingTrack.recording_id == rec.id)).all()
        waited = rec.stopped_at is not None and now >= rec.stopped_at + timedelta(seconds=settings.recording_grace_s)
        everyone = len(tracks) >= len(ROLES) and all(t.last_seq is not None for t in tracks)
        if not (waited or everyone):
            continue
        try:
            finalize(db, rec)
        except Exception as e:  # never loop forever on a bad file
            db.rollback()
            rec = db.get(Recording, rec.id)
            rec.status, rec.error = "failed", f"合成失败：{str(e)[-150:]}"
            db.commit()
        done += 1
    return done


def start_worker() -> None:
    from .db import SessionLocal

    def loop():
        while True:
            try:
                with SessionLocal() as s:
                    process_pending(s)
            except Exception:
                pass
            time.sleep(2)

    threading.Thread(target=loop, daemon=True, name="recording-worker").start()
