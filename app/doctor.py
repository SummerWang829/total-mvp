"""`python -m app.doctor`: is this computer ready to run the platform? Plain-Chinese checklist."""
import os
import shutil
import subprocess
import sys

import httpx

from .config import settings
from .media import find_font

bad = 0


def line(kind: str, text: str) -> None:
    global bad
    mark = {"ok": "✓", "warn": "!", "bad": "✗"}[kind]
    bad += kind == "bad"
    print(f"  {mark} {text}")


def check_provider(name: str, provider: str, base: str, key: str | None, key_var: str = "LLM_API_KEY") -> None:
    if provider == "mock":
        line("warn", f"{name}：演示模式（结果是固定示例，不是真的识别/分析）。要真实效果，在 .env 里配置，见 README")
        return
    if not key:
        line("bad", f"{name}：选了 {provider} 但没有填密钥（.env 里的 {key_var}）")
        return
    try:
        r = httpx.get(base.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if r.status_code == 200:
            line("ok", f"{name}：服务可连接")
        elif r.status_code == 404:  # local servers (e.g. whisper.cpp) answer but have no /models endpoint
            line("ok", f"{name}：服务可连接（{base}，没有 /models 接口，跳过密钥检查）")
        else:
            line("bad", f"{name}：服务返回 {r.status_code}，请检查密钥和地址")
    except Exception as e:
        line("bad", f"{name}：连不上 {base}（{type(e).__name__}）")


def check_tencent_file_asr() -> None:
    """Recorded-file recognition (ASR_PROVIDER=tencent): credentials present, and one harmless read-only call proves they are accepted."""
    from .asr_file import ASRError, call, file_asr_configured

    if not file_asr_configured():
        line("bad", "转写：选了 tencent 但没有填 TENCENT_ASR_SECRET_ID / TENCENT_ASR_SECRET_KEY")
        return
    try:
        call("DescribeTaskStatus", {"TaskId": 1})  # an unknown task id: the service answers "no such task", which still proves the keys work
    except ASRError as e:
        if "AuthFailure" in str(e) or "UnauthorizedOperation" in str(e):
            line("bad", f"转写：腾讯云不认这对密钥或这个子用户没有语音识别权限（{str(e)[:90]}）")
        else:
            line("ok", "转写：腾讯云录音文件识别可用（只支持中文和英文）")
        return
    line("ok", "转写：腾讯云录音文件识别可用（只支持中文和英文）")


def check_moderator() -> None:
    """AI moderator model (MODERATOR_* in .env, falls back to LLM_*). A cloud model must be named for the consent text.

    Prints one line per finding; a missing vendor is a warning because only AI-moderated interviews are blocked by it.
    """
    from .moderator.compliance import ai_moderation_blocker, moderator_runs_locally
    from .moderator.constants import ModeratorProviderName

    check_provider("AI 主持人", settings.moderator_provider, settings.moderator_base_url, settings.moderator_api_key, "MODERATOR_API_KEY")
    if settings.moderator_provider == ModeratorProviderName.MOCK.value:
        return
    where = "本机模型，对话不出这台电脑" if moderator_runs_locally() else f"云端模型（{settings.moderator_vendor or '未填写供应商'}），受访者的回答会发给它"
    line("ok", f"AI 主持人用的模型：{settings.moderator_model}，{where}")
    if why := ai_moderation_blocker():
        line("warn", why + "。填好之前不能新建 AI 主持的场次")


def check_backup() -> None:
    """Newest database backup and how old it is (only meaningful where backups run, i.e. a file database in the deployed image)."""
    import time
    from pathlib import Path

    folder = Path(settings.backup_dir)
    if not folder.exists():
        if os.path.exists("/.dockerenv"):
            line("warn", f"备份：还没有任何备份（{folder} 不存在）；deploy/backup.sh 每天跑一次")
        return
    newest = max(folder.glob("total-*.db.gz"), default=None, key=lambda f: f.name)
    if not newest:
        line("warn", "备份：目录里还没有备份文件")
        return
    hours = (time.time() - newest.stat().st_mtime) / 3600
    line("ok" if hours < 36 else "warn", f"备份：最近一次 {hours:.0f} 小时前（{newest.name}）" + ("" if hours < 36 else "，超过 36 小时，检查定时任务"))


def main() -> int:
    print("环境检查")
    line("ok" if sys.version_info >= (3, 10) else "bad", f"Python {sys.version.split()[0]}" + ("" if sys.version_info >= (3, 10) else "（需要 3.10 以上）"))
    ff = shutil.which(settings.ffmpeg)
    if not ff:
        line("bad", "没找到 ffmpeg（合成录像、剪片段要用）。Mac：brew install ffmpeg；Windows：winget install ffmpeg")
    else:
        enc = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        line("ok" if "libx264" in enc else "bad", "ffmpeg 可用（含 H.264 编码）" if "libx264" in enc else "ffmpeg 缺少 libx264 编码，请换完整版 ffmpeg")
    line("ok" if shutil.which(settings.ffprobe) else "bad", "ffprobe 可用" if shutil.which(settings.ffprobe) else "没找到 ffprobe（随 ffmpeg 一起安装）")
    try:
        line("ok", f"水印字体：{os.path.basename(find_font())}")
    except RuntimeError as e:
        line("bad", str(e))
    try:
        os.makedirs(settings.media_dir, exist_ok=True)
        t = os.path.join(settings.media_dir, ".write_test"); open(t, "w").write("x"); os.remove(t)
        line("ok", f"录像存放目录可写：{settings.media_dir}")
    except OSError:
        line("bad", f"录像存放目录不可写：{settings.media_dir}")
    if settings.livekit_url:
        try:
            ok = httpx.get(settings.livekit_url.replace("ws://", "http://").replace("wss://", "https://"), timeout=3).status_code == 200
            line("ok" if ok else "warn", "视频服务器在线" if ok else "视频服务器没有响应（scripts/dev.sh 会自动启动它）")
        except Exception:
            line("warn", "视频服务器还没启动（scripts/dev.sh 会自动启动它）")
    else:
        line("warn", "没配置 LIVEKIT_URL：没有视频，只能看权限演示（scripts/dev.sh 会自动配置）")
    if settings.asr_provider == "tencent":
        check_tencent_file_asr()
    else:
        check_provider("转写", settings.asr_provider, settings.asr_base_url, settings.asr_api_key, "ASR_API_KEY")
    check_provider("洞察/翻译", settings.llm_provider, settings.llm_base_url, settings.llm_api_key)
    check_moderator()
    from .asr_live import live_captions_configured
    if live_captions_configured():
        line("ok", "实时字幕：已配置腾讯云实时语音识别（密钥只在服务器上，浏览器拿到的是短时效的签名地址）")
    else:
        line("warn", "实时字幕：没配置（TENCENT_ASR_APPID / TENCENT_ASR_SECRET_ID / TENCENT_ASR_SECRET_KEY），字幕栏会提示“未开启”")
    line("ok" if settings.rate_limit else "warn", "限流：已开启（登录失败、乱猜链接、注册洪水）" if settings.rate_limit else "限流：没开（RATE_LIMIT=1 才开启）；线上必须开")
    check_backup()
    if settings.allow_signup and os.path.exists("/.dockerenv"):
        line("warn", "ALLOW_SIGNUP 还是 true：线上建好第一个管理员后要改成 false")
    if "example.com" in settings.dpo_email:
        line("warn", "DPO_EMAIL 还是示例邮箱：同意书里显示的联系邮箱要换成真实的")
    if settings.secret_key.startswith(("dev-secret", "dev-only", "change-me")):
        line("warn", "SECRET_KEY 还是开发默认值：只能在自己电脑上用，上线前必须换")
    print("没有必须解决的问题。" if not bad else f"有 {bad} 项必须先解决。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
