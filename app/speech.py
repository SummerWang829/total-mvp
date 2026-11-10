"""Voice input for the team's text boxes: a short recording goes in, plain text comes out. Nothing is stored.

The page records up to MAX_SECONDS with the browser's recorder and sends the clip here; it is converted to a small mono mp3, recognised by the configured
speech service (the same one that transcribes interviews), and the text goes back to the page, which puts it into the box. The recording is deleted at once;
neither the audio nor the text is logged or audited (only that a clip was read, and how big it was).
"""
import logging
import os
import subprocess
import tempfile

from .config import settings
from .providers import MockASR, get_asr

log = logging.getLogger(__name__)

MAX_BYTES = 4 * 1024 * 1024  # about 3 minutes of browser audio; the page stops at MAX_SECONDS long before this
MAX_SECONDS = 90
LANGUAGES = ("zh", "en")
MOCK_TEXT = "（演示模式）这是语音输入的示例文字。"
CONVERT_TIMEOUT_S = 60


class SpeechError(ValueError):
    """The clip could not be turned into text; the message is plain Chinese for the page. `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _to_mp3(src: str, dst: str) -> None:
    """Convert the browser's clip (webm / ogg / mp4 / wav ...) to mono 16 kHz mp3 and cap its length.

    Raises:
        SpeechError: the file is not audio ffmpeg can read, or ffmpeg is missing.
    """
    cmd = [settings.ffmpeg, "-y", "-v", "error", "-i", src, "-t", str(MAX_SECONDS), "-ac", "1", "-ar", "16000", "-b:a", "32k", dst]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=CONVERT_TIMEOUT_S, check=False)
    except FileNotFoundError as e:
        raise SpeechError("服务器上没有音频转换工具，暂时不能用语音输入", 503) from e
    except subprocess.TimeoutExpired as e:
        raise SpeechError("这段录音处理太久了，请说短一点再试", 422) from e
    if done.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        raise SpeechError("没有录到可识别的声音，请再试一次")


def transcribe_clip(data: bytes, language: str) -> str:
    """The words in one short recording, as one line of text ("" when nothing was said).

    Args:
        data: the recording as the browser made it.
        language: "zh" or "en".

    Raises:
        SpeechError: too big, unsupported language, not audio, or the speech service failed.
    """
    if language not in LANGUAGES:
        raise SpeechError("语音输入目前只支持中文和英文")
    if not data:
        raise SpeechError("没有收到录音")
    if len(data) > MAX_BYTES:
        raise SpeechError("录音太长了，请分几次说", 413)
    if isinstance(get_asr(), MockASR):
        return MOCK_TEXT  # development and tests: the mock recogniser has no real ears
    with tempfile.TemporaryDirectory(prefix="voice-") as tmp:
        src, dst = os.path.join(tmp, "clip.bin"), os.path.join(tmp, "clip.mp3")
        with open(src, "wb") as f:
            f.write(data)
        _to_mp3(src, dst)
        try:
            rows = get_asr().transcribe_audio(dst, language, "")
        except Exception as e:  # noqa: BLE001 - any recogniser failure becomes one plain message; the cause is logged without the content
            log.warning("voice input: the speech service failed (%s)", type(e).__name__, exc_info=True)
            raise SpeechError("语音识别暂时不可用，请稍后再试或直接打字", 502) from e
    return "".join(r.get("text", "") for r in rows).strip()
