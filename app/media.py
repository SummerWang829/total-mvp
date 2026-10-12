"""Server-side clip cutting with a burned-in watermark: clients only ever receive the short, watermarked cut,
never the raw recording. Cuts are cached per (recording, range, watermark)."""
import hashlib
import os
import shutil
import subprocess
import threading

from .config import settings
from .recording import run

_FONTS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc", "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def find_font() -> str:
    if settings.watermark_font and os.path.isfile(settings.watermark_font):
        return settings.watermark_font
    for f in _FONTS:
        if os.path.isfile(f):
            return f
    try:  # last resort: ask fontconfig
        out = subprocess.run(["fc-match", "-f", "%{file}", "sans:lang=zh"], capture_output=True, text=True, timeout=5).stdout.strip()
        if out and os.path.isfile(out):
            return out
    except Exception:
        pass
    raise RuntimeError("找不到可用字体，无法烧录水印。请设置 WATERMARK_FONT 指向一个字体文件。")


def _esc_path(p: str) -> str:
    return p.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def cut_clip(src: str, rec_id: str, start_ms: int, end_ms: int, watermark: str) -> str:
    key = hashlib.sha256(f"{rec_id}|{start_ms}|{end_ms}|{watermark}".encode()).hexdigest()[:24]
    out_dir = os.path.join(settings.media_dir, "cache", rec_id)
    out = os.path.join(out_dir, key + ".mp4")
    if os.path.isfile(out):
        return out
    with _guard:
        lock = _locks.setdefault(out, threading.Lock())
    with lock:
        if os.path.isfile(out):
            return out
        os.makedirs(out_dir, exist_ok=True)
        wm_file = os.path.join(out_dir, key + ".txt")
        with open(wm_file, "w", encoding="utf-8") as f:
            f.write(watermark)
        font = _esc_path(find_font())
        tf = _esc_path(wm_file)
        vf = (f"drawtext=fontfile='{font}':textfile='{tf}':fontcolor=white@0.30:fontsize=h/13:x=(w-text_w)/2:y=(h-text_h)/2:shadowcolor=black@0.25:shadowx=1:shadowy=1,"
              f"drawtext=fontfile='{font}':textfile='{tf}':fontcolor=white@0.70:fontsize=h/28:x=16:y=h-text_h-14:box=1:boxcolor=black@0.35:boxborderw=6")
        tmp = out + ".part.mp4"
        run([settings.ffmpeg, "-y", "-v", "error", "-ss", f"{start_ms / 1000:.3f}", "-i", src, "-t", f"{(end_ms - start_ms) / 1000:.3f}", "-vf", vf,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", tmp],
            timeout=300)
        os.replace(tmp, out)
        os.remove(wm_file)
        return out


def purge_cache(rec_id: str) -> None:
    shutil.rmtree(os.path.join(settings.media_dir, "cache", rec_id), ignore_errors=True)
