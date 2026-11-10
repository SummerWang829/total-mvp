"""The invitation letter to a participant: checking the address, writing the email (in the participant's language), putting it in the outbox, and
telling the page how it went.

Nothing here raises for a bad address or a missing mail service: the interview is created either way and the page shows a notice, so one failed
email never blocks the team's other work.
"""
import logging
import re
import socket
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import mailer
from .models import EmailOutbox, Interview, Participant, User

log = logging.getLogger(__name__)

SGT = timedelta(hours=8)  # the only market open so far is Singapore; every time is written in Singapore time
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
WEEKDAY = {"zh": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"], "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
MIN_DURATION, MAX_DURATION = 5, 240


def domain_resolves(domain: str) -> bool:
    """Does the domain exist on the internet at all? (Tests replace this function.)"""
    try:
        socket.getaddrinfo(domain, None)
    except OSError:
        return False
    return True


def check_email(address: str) -> str:
    """Why this address cannot work, in plain Chinese; "" when it looks usable."""
    a = (address or "").strip()
    if not a:
        return "没有邮箱"
    if "@" not in a:
        return "缺少 @，邮箱应该像 name@example.com"
    if not EMAIL_RE.match(a):
        return "@ 后面的域名不完整，比如缺少 .com"
    if not domain_resolves(a.split("@", 1)[1]):
        return f"域名“{a.split('@', 1)[1]}”收不到邮件"
    return ""


def local_time(dt: datetime | None, lang: str) -> str:
    """A stored UTC time written in Singapore time, e.g. "10/28 周三 14:00"; "" when there is no time."""
    if dt is None:
        return ""
    t = dt + SGT
    names = WEEKDAY["zh" if lang == "zh" else "en"]
    return f"{t.month}/{t.day} {names[t.weekday()]} {t:%H:%M}"


def _words(lang: str) -> dict:
    zh = {
        "subject": "访谈邀请：{when}（新加坡时间）", "subject_any": "访谈邀请", "subject_update": "访谈安排有更新：{when}（新加坡时间）", "subject_update_any": "访谈安排有更新",
        "hi": "您好，{name}：", "intro": "感谢您参加我们的调研。我们诚挚地邀请您参加一次一对一的访谈，所有内容将严格保密。",
        "topic": "访谈主题",
        "intro_update": "您的访谈安排有更新，最新的信息如下。",
        "when": "时间", "anytime": "随时可进入（我们会再和您确认具体时间）", "sgt": "新加坡时间，SGT", "moderator": "主持人", "later": "稍后通知",
        "human": "真人主持", "ai": "AI 主持（文字聊天，有同事在旁盯着）", "format": "访谈方式", "video": "在线视频访谈，用电脑或手机的浏览器进入，无需安装软件",
        "text": "在线文字访谈，用电脑或手机的浏览器进入", "length": "大概时长", "minutes": "约 {n} 分钟", "link": "进入访谈", "same_link": "链接不变，请继续用之前邮件里的链接进入",
        "n1": "请在访谈开始前 10 分钟打开链接，完成设备检测（麦克风必须正常，摄像头可以不开）。", "n2": "访谈会录音录像，仅用于研究，您的信息会按约定的期限删除。",
        "n3": "如果这个时间不合适，直接回复这封邮件告诉我们，我们会为您调整。", "bye": "谢谢！\n星桐研究团队",
    }
    en = {
        "subject": "Interview invitation: {when} (Singapore time)", "subject_any": "Interview invitation", "subject_update": "Your interview details changed: {when} (Singapore time)",
        "subject_update_any": "Your interview details changed", "hi": "Hello {name},",
        "topic": "Topic",
        "intro": "Thank you for taking part in our research. We would like to invite you to a one-to-one interview. Everything you say is kept confidential.",
        "intro_update": "The details of your interview have changed. The latest are below.",
        "when": "When", "anytime": "Any time (we will confirm the exact time with you)", "sgt": "Singapore time, SGT", "moderator": "Moderator", "later": "to be announced",
        "human": "human moderator", "ai": "AI moderator (text chat, a colleague watches along)", "format": "Format",
        "video": "Online video interview, in your browser on a computer or phone, nothing to install", "text": "Online text interview, in your browser on a computer or phone",
        "length": "Length", "minutes": "About {n} minutes", "link": "Join the interview", "same_link": "The link has not changed: keep using the one in the earlier email",
        "n1": "Please open the link 10 minutes before the start and do the device check (the microphone must work, the camera is optional).",
        "n2": "The interview is audio and video recorded for research only, and your data is deleted after the agreed period.",
        "n3": "If this time does not suit you, just reply to this email and we will find another.", "bye": "Thank you!\nThe TOTAL research team",
    }
    return zh if lang == "zh" else en


def formats_text(code: str, lang: str, fallback: str) -> str:
    """The interview format in words for the invitation (the stored code, else the default text)."""
    from . import formats
    return (formats.FORMATS if lang == "zh" else formats.EN).get(code or "", fallback)


def compose(itv: Interview, part: Participant, moderator_name: str, link: str | None, update: bool = False) -> tuple[str, str]:
    """(subject, plain-text body) for the invitation, or for a change of plan when `update`. `link` is None when the link is not repeated."""
    lang = part.language if part.language in ("zh", "en") else "en"  # ms / ta use the English text until a native speaker has checked a translation
    w = _words(lang)
    when = local_time(itv.scheduled_at, lang)
    subject = (w["subject_update" if update else "subject"].format(when=when) if when else w["subject_update_any" if update else "subject_any"])
    ai = itv.moderation == "ai"
    topic = (itv.participant_topic or "").strip() or (itv.title or "").strip()
    rows = [
        (w["topic"], topic) if topic else None,
        (w["when"], f"{when}（{w['sgt']}）" if when else w["anytime"]),
        (w["moderator"], f"{moderator_name or w['later']}（{w['ai'] if ai else w['human']}）" if moderator_name or ai else f"{w['later']}（{w['human']}）"),
        (w["format"], w["text"] if ai else formats_text(itv.format, lang, w["video"])),
        (w["length"], w["minutes"].format(n=itv.duration_min or 45)),
    ]
    rows = [r for r in rows if r]
    lines = [w["hi"].format(name=part.name), "", w["intro_update" if update else "intro"], ""] + [f"{k}：{v}" if lang == "zh" else f"{k}: {v}" for k, v in rows]
    lines += ["", (f"{w['link']}：{link}" if lang == "zh" else f"{w['link']}: {link}") if link else w["same_link"], "", f"① {w['n1']}", f"② {w['n2']}", f"③ {w['n3']}", "", w["bye"], ""]
    return subject, "\n".join(lines)


def moderator_name(db: Session, itv: Interview) -> str:
    """The name of the moderator the session was given to ("" when nobody is named)."""
    u = db.get(User, itv.moderator_id) if itv.moderator_id else None
    return u.name if u else ""


def send(db: Session, itv: Interview, part: Participant, link: str | None, org_id: str, update: bool = False) -> dict:
    """Put the invitation (or the change notice) in the outbox. Returns {"status": queued | no_email | bad_email | no_mailer, "reason": text}; never raises for those."""
    why = check_email(part.email)
    if why:
        return {"status": "no_email" if not (part.email or "").strip() else "bad_email", "reason": why}
    if not mailer.configured():
        return {"status": "no_mailer", "reason": "还没有配置发件邮箱，请改用“创建并生成邀请函”复制链接发给对方"}
    subject, body = compose(itv, part, moderator_name(db, itv), link, update)
    mailer.queue(db, part.email.strip(), "invite_update" if update else "invite", body, org_id=org_id, subject=subject, ref_id=itv.id)
    return {"status": "queued", "reason": ""}


def mail_status(db: Session, interview_id: str) -> str:
    """How the latest invitation email for this interview stands: "" (none sent), pending, sent or failed."""
    row = db.scalars(select(EmailOutbox).where(EmailOutbox.ref_id == interview_id).order_by(EmailOutbox.created_at.desc()).limit(1)).first()
    return row.status if row else ""
