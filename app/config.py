"""Runtime settings. Read from environment once; tests may mutate the `settings` object."""
import os
from dataclasses import dataclass, field


def _load_dotenv(path: str = ".env") -> None:
    """Tiny .env reader (no extra dependency). Real environment variables win over the file."""
    if not os.path.isfile(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.split(" #")[0].strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

EU27 = {"AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR","HU","IE","IT","LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE"}
# First market excludes the EU. We also block EEA + UK by default (separate regimes); override with BLOCKED_COUNTRIES.
DEFAULT_BLOCKED = EU27 | {"IS","LI","NO","GB"}


def _csv(name: str, default: set[str]) -> set[str]:
    v = os.getenv(name)
    return {x.strip().upper() for x in v.split(",") if x.strip()} if v else set(default)


@dataclass
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./total.db")
    secret_key: str = os.getenv("SECRET_KEY", "dev-secret-change-me-please-32bytes!!")
    token_ttl_min: int = int(os.getenv("TOKEN_TTL_MIN", "720"))
    allow_signup: bool = os.getenv("ALLOW_SIGNUP", "true").lower() == "true"

    livekit_url: str | None = os.getenv("LIVEKIT_URL")  # e.g. wss://xxx.livekit.cloud
    livekit_api_key: str = os.getenv("LIVEKIT_API_KEY", "devkey")
    livekit_api_secret: str = os.getenv("LIVEKIT_API_SECRET", "devsecret-devsecret-devsecret-0000")
    room_token_ttl_min: int = int(os.getenv("ROOM_TOKEN_TTL_MIN", "120"))
    join_early_min: int = int(os.getenv("JOIN_EARLY_MIN", "60"))
    join_late_min: int = int(os.getenv("JOIN_LATE_MIN", "60"))

    mail_host: str = os.getenv("MAIL_HOST", "")  # SMTP server of the mail-sending service; empty = no account emails (links are handed over by hand)
    mail_port: int = int(os.getenv("MAIL_PORT", "587"))
    mail_user: str = os.getenv("MAIL_USER", "")
    mail_password: str = os.getenv("MAIL_PASSWORD", "")
    mail_from: str = os.getenv("MAIL_FROM", "")  # e.g. 星桐 <no-reply@morningstarsystem.tech>; the domain must carry the service's SPF/DKIM records
    mail_security: str = os.getenv("MAIL_SECURITY", "starttls")  # starttls | ssl | none
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")  # the address links in emails point to, e.g. https://deepresearch.morningstarsystem.tech
    rate_limit: bool = os.getenv("RATE_LIMIT", "") == "1"  # failed-login and link-probing limits (app/ratelimit.py); on in the deployed image
    cos_bucket: str = os.getenv("COS_BUCKET", "")      # off-site copy of the database backups (Tencent COS); empty = off
    cos_region: str = os.getenv("COS_REGION", "ap-singapore")
    cos_secret_id: str = os.getenv("COS_SECRET_ID", "")  # a sub-account that may only write to that one bucket
    cos_secret_key: str = os.getenv("COS_SECRET_KEY", "")
    backup_dir: str = os.getenv("BACKUP_DIR", "/data/backups")  # python -m app.backup writes here (a folder next to the database in the deployed image)
    backup_keep_days: int = int(os.getenv("BACKUP_KEEP_DAYS", "14"))  # copies expire so erased personal data does not live on in them
    retention_days: int = int(os.getenv("RETENTION_DAYS", "180"))  # counted from PROJECT close (decision 2026-10-10)
    retention_backstop_days: int = int(os.getenv("RETENTION_BACKSTOP_DAYS", "730"))  # projects never closed: purge this long after the interview ended
    dpo_email: str = os.getenv("DPO_EMAIL", "summer.wang@topvisiontechnology.com")  # Singapore PDPA: a business contact for data-protection queries
    media_dir: str = os.getenv("MEDIA_DIR", "./media")
    media_url_ttl_s: int = int(os.getenv("MEDIA_URL_TTL_S", "300"))

    llm_provider: str = os.getenv("LLM_PROVIDER", "mock")  # mock | openai
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_api_key: str | None = os.getenv("LLM_API_KEY")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm_timeout_s: int = int(os.getenv("LLM_TIMEOUT_S", "180"))  # per call; raise it for a slow local CPU model
    asr_provider: str = os.getenv("ASR_PROVIDER", "mock")  # mock | openai (any OpenAI-compatible /audio/transcriptions) | tencent (recorded-file recognition, zh/en only)
    asr_base_url: str = os.getenv("ASR_BASE_URL", "") or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    asr_api_key: str | None = os.getenv("ASR_API_KEY") or os.getenv("LLM_API_KEY")
    asr_model: str = os.getenv("ASR_MODEL", "whisper-1")  # needs segment timestamps (verbose_json)
    asr_timeout_s: int = int(os.getenv("ASR_TIMEOUT_S", "600"))  # per upload; raise it for a slow local CPU server

    # Live captions while the interview is running: each browser streams its own microphone to Tencent Cloud real-time speech recognition
    # (wss://asr.cloud.tencent.com/asr/v2/<appid>). The secret key stays on this server: the browser only gets a short-lived signed address.
    # All three empty = captions are off (the panel says so). ASR_LIVE_WS_BASE exists so the tests can point browsers at a local fake server.
    tencent_asr_appid: str = os.getenv("TENCENT_ASR_APPID", "")
    tencent_asr_secret_id: str = os.getenv("TENCENT_ASR_SECRET_ID", "")
    tencent_asr_secret_key: str = os.getenv("TENCENT_ASR_SECRET_KEY", "")
    asr_live_ws_base: str = os.getenv("ASR_LIVE_WS_BASE", "wss://asr.cloud.tencent.com")

    # AI moderator (docs/AI主持人_工程与训练任务说明_v1.md). Any OpenAI-compatible chat API; switching model = changing these lines only.
    # Empty values fall back to the LLM_* settings above. A local server (Ollama on 127.0.0.1) keeps transcripts on this computer;
    # anything else is a cloud provider: MODERATOR_VENDOR must name it, because the consent text tells participants who receives their answers.
    moderator_provider: str = os.getenv("MODERATOR_PROVIDER", "") or os.getenv("LLM_PROVIDER", "mock")  # mock | openai
    moderator_base_url: str = os.getenv("MODERATOR_BASE_URL", "") or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    moderator_api_key: str | None = os.getenv("MODERATOR_API_KEY") or os.getenv("LLM_API_KEY")
    moderator_model: str = os.getenv("MODERATOR_MODEL", "") or os.getenv("LLM_MODEL", "gpt-4o-mini")
    moderator_timeout_s: int = int(os.getenv("MODERATOR_TIMEOUT_S", "60"))
    moderator_extra_body: str = os.getenv("MODERATOR_EXTRA_BODY", "")  # JSON object merged into every request (e.g. {"thinking": {"type": "disabled"}})
    # The model host is a fixed vendor, so by default its calls ignore the computer's system proxy: a local proxy measured 5-30x slower.
    moderator_use_system_proxy: bool = os.getenv("MODERATOR_USE_SYSTEM_PROXY", "").lower() == "true"
    moderator_vendor: str = os.getenv("MODERATOR_VENDOR", "")  # e.g. "DeepSeek"; required for a cloud model (app/moderator/compliance.py)

    # recording: each browser records its own camera+mic and uploads chunks; the server mixes them (see app/recording.py)
    recording_grace_s: int = int(os.getenv("RECORDING_GRACE_S", "20"))  # after "stop", wait this long for late chunks
    max_chunk_bytes: int = int(os.getenv("MAX_CHUNK_BYTES", str(40 * 1024 * 1024)))
    ffmpeg: str = os.getenv("FFMPEG", "ffmpeg")
    ffprobe: str = os.getenv("FFPROBE", "ffprobe")
    watermark_font: str = os.getenv("WATERMARK_FONT", "")  # a .ttf/.ttc/.otf with Latin + Chinese glyphs; auto-detected if empty
    run_worker: bool = os.getenv("DISABLE_WORKER", "") == ""
    loop_watch_s: float = float(os.getenv("LOOP_WATCH_S", "0") or 0)  # >0: log a stack trace whenever the event loop is blocked longer than this

    blocked_countries: set[str] = field(default_factory=lambda: _csv("BLOCKED_COUNTRIES", DEFAULT_BLOCKED))
    # First market: Singapore only (decision 2026-10-10). Anyone declaring another country is politely turned away.
    allowed_countries: set[str] = field(default_factory=lambda: _csv("ALLOWED_COUNTRIES", {"SG"}))
    languages: tuple[str, ...] = ("en", "zh", "ms", "ta")  # English, Chinese, Malay, Tamil

    def region_open(self, country: str) -> bool:
        c = (country or "").upper()
        return c in self.allowed_countries and c not in self.blocked_countries


settings = Settings()
