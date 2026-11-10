"""One source of truth for the project facts shown across the platform.

The wizard stores the real country / interview language in ProjectPlan.subs[].  The legacy
Project.market and Participant.country columns can still hold an outdated code (e.g. "SG"), and
the project's purpose / objectives live in brief + outline_text.  Every human-readable label in a
report, the report header and the respondent hover card is resolved here, so no raw code or
placeholder ever leaks into a client-facing document.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

from .models import Project, ProjectPlan

log = logging.getLogger(__name__)

# Country / region codes -> the name a Chinese research report should show.
MARKETS = {
    "CN": "中国", "HK": "中国香港", "MO": "中国澳门", "TW": "中国台湾",
    "SG": "新加坡", "MY": "马来西亚", "ID": "印尼", "TH": "泰国", "VN": "越南",
    "JP": "日本", "KR": "韩国", "US": "美国", "GB": "英国", "UK": "英国",
    "AU": "澳大利亚", "CA": "加拿大", "DE": "德国", "FR": "法国",
}
# The wizard stores names, not codes; normalise them to the same wording as MARKETS.
MARKET_NAMES = {
    "香港": "中国香港", "中國香港": "中国香港", "Hong Kong": "中国香港",
    "澳门": "中国澳门", "台湾": "中国台湾", "大陆": "中国", "中国大陆": "中国", "中国": "中国",
    "新加坡": "新加坡", "英国": "英国", "United Kingdom": "英国",
}
LANGS = {
    "zh": "中文", "zh-CN": "中文（普通话 · 简体）", "zh-HK": "中文（香港繁体）",
    "en": "英语", "ms": "马来语", "ta": "泰米尔语", "ja": "日语", "ko": "韩语",
    "中文": "中文", "简体中文": "中文（普通话 · 简体）", "繁体中文": "中文（香港繁体）",
    "英语": "英语", "English": "英语", "马来语": "马来语", "泰米尔语": "泰米尔语",
}


def market_label(value: str | None) -> str:
    """A country code / wizard name -> the readable Chinese name shown to researchers."""
    raw = str(value or "").strip()
    if not raw:
        return "—"
    if raw in MARKET_NAMES:
        return MARKET_NAMES[raw]
    return MARKETS.get(raw.upper(), raw)


def lang_label(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    return LANGS.get(raw, LANGS.get(raw.lower(), raw))


def _live_config(plan: ProjectPlan | None) -> dict:
    """The published plan keeps running while a new draft is being edited (same rule as audience.py)."""
    if plan is None:
        return {}
    raw = (plan.published_json or plan.config_json) if plan.version > 0 else plan.config_json
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}") or {}
    except (ValueError, TypeError) as exc:
        log.warning("project_context: cannot read plan config: %s", exc)
        return {}


def _outline_field(text: str, name: str) -> str:
    m = re.search(rf"{re.escape(name)}\s*[:：]\s*(.+?)(?=\n|$)", text or "")
    return (m.group(1).strip() if m else "")


def objectives(proj: Project) -> list[str]:
    """The 研究目标 list the wizard wrote into outline_text, in order."""
    raw = _outline_field(proj.outline_text or "", "研究目标")
    return [x.strip() for x in re.split(r"[；;]+", raw) if x.strip()] if raw else []


def purpose(proj: Project) -> str:
    """The 研究目的 sentence from the project brief."""
    m = re.search(r"(此次研究是为了[^。；;\n]*)", (proj.brief or ""))
    return m.group(1).strip() if m else ""


def criteria(proj: Project) -> list[list[str]]:
    """The respondent screening conditions from the brief, as [label, verbatim condition]."""
    out: list[list[str]] = []
    for line in (proj.brief or "").splitlines():
        for part in re.split(r"[；;]", line):
            t = part.strip().rstrip("；;，, ")
            if not t or t.startswith("此次研究是为了"):
                continue
            if "居民" in t or "配额" in t:
                label = "研究对象 / 配额"
            elif "岁" in t:
                label = "年龄"
            elif "到访" in t or "商圈" in t:
                label = "到访经历"
            elif "收入" in t:
                label = "家庭收入"
            else:
                label = "筛选条件"
            out.append([label, t])
    return out


LANG_CODES = {"中文": "zh", "英语": "en", "English": "en", "马来语": "ms", "泰米尔语": "ta",
              "zh": "zh", "en": "en", "ms": "ms", "ta": "ta"}
CODE_NAMES = {"zh": "中文", "en": "英语", "ms": "马来语", "ta": "泰米尔语"}
# Only these can actually be used to RUN an interview: the consent / invitation wording has been
# approved by legal and the AI moderator speaks them (project_langs.CONSENT_REVIEWED / AI_MODERATION).
# The wizard's language checklist defaults to every language, so 马来语 / 泰米尔语 get ticked even
# for a Chinese-only project. They are never real interview-outline versions.
INTERVIEW_LANGS = {"zh", "en"}


def language_codes(db: Session, proj: Project) -> list[str]:
    """The interview languages this project actually has, not the platform's full checklist.

    Real evidence, in this order: the wizard sub-project's default language, the outline header,
    the project default, and the languages of interviews that have already run. Languages ticked
    in the plan are kept only if the platform can really interview in them.
    """
    from sqlalchemy import select
    from .models import Interview

    plan = db.get(ProjectPlan, proj.id)
    cfg = _live_config(plan) if plan is not None and plan.org_id == proj.org_id else {}
    subs = [s for s in (cfg.get("subs") or []) if isinstance(s, dict)]
    raw = cfg.get("langs")
    names = [k for k, v in raw.items() if v] if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    if not names and subs:
        names = subs[0].get("langs") or []

    default = ""
    for candidate in ([str(subs[0].get("lang") or "")] if subs else []) + [
            _outline_field(proj.outline_text or "", "语言"), str(proj.language or "")]:
        code = LANG_CODES.get(candidate.strip(), candidate.strip())
        if code in INTERVIEW_LANGS:
            default = code
            break

    picked = [LANG_CODES[str(x).strip()] for x in names if str(x).strip() in LANG_CODES]
    picked = [c for c in picked if c in INTERVIEW_LANGS]
    used = [c for c in db.scalars(
        select(Interview.language).where(Interview.project_id == proj.id).distinct()).all()
        if c in INTERVIEW_LANGS]
    codes = [c for c in dict.fromkeys([default, *used, *picked]) if c]
    return codes or ["zh"]


FORMAT_NAMES = {"audio": "纯音频", "text": "文字访谈", "text_voice": "文字+语音转录",
                "video": "视频", "video_screen": "视频+屏幕共享"}
FAMILY_CODES = {"live": ("audio", "video", "video_screen"), "text": ("text", "text_voice")}


def formats_of(db: Session, proj: Project) -> dict:
    """The interview formats THIS project actually runs, from the published plan.

    Project.formats can hold an older set of codes; the wizard's subs[].fmt / fmt is what the
    project overview shows, so it wins here too.
    """
    plan = db.get(ProjectPlan, proj.id)
    cfg = _live_config(plan) if plan is not None and plan.org_id == proj.org_id else {}
    raw: dict = {}
    subs = [s for s in (cfg.get("subs") or []) if isinstance(s, dict)]
    if subs and isinstance(subs[0].get("fmt"), dict):
        raw = subs[0]["fmt"]
    elif isinstance(cfg.get("fmt"), dict):
        raw = cfg["fmt"]
    elif isinstance(proj.formats, dict):
        raw = proj.formats
    codes: list[str] = []
    for v in raw.values():
        for c in (v if isinstance(v, list) else [v]):
            c = str(c)
            if c in FORMAT_NAMES and c not in codes:
                codes.append(c)
    names = [FORMAT_NAMES[c] for c in codes]
    family = {}
    for fam, members in FAMILY_CODES.items():
        picked = [FORMAT_NAMES[c] for c in codes if c in members]
        if picked:
            family[fam] = " / ".join(picked)
    return {"codes": codes, "names": names, "label": " / ".join(names),
            "family_labels": family, "source": "项目总览（立项配置）" if subs else "项目基本信息"}


def meta(db: Session, proj: Project) -> dict:
    """Everything a report needs about THIS project, never a raw code or a placeholder."""
    plan = db.get(ProjectPlan, proj.id)
    cfg = _live_config(plan) if plan is not None and plan.org_id == proj.org_id else {}
    subs = [s for s in (cfg.get("subs") or []) if isinstance(s, dict) and (s.get("name") or "").strip()]
    sub = subs[0] if subs else {}
    if sub:
        country = market_label(sub.get("name"))
        language = lang_label(sub.get("lang") or (sub.get("langs") or [""])[0])
        source = "项目总览（立项配置）"
    else:
        country = market_label(proj.market)
        language = lang_label(proj.language)
        source = "项目基本信息"
    codes = language_codes(db, proj)
    languages = [CODE_NAMES[c] for c in codes]
    outline = proj.outline_text or ""
    topic = _outline_field(outline, "受访者话题")
    dur = _outline_field(outline, "时长")
    return {
        "country": country, "language": language, "languages": languages or [language],
        "source": source, "sub_name": str(sub.get("name") or "").strip(),
        "purpose": purpose(proj), "objectives": objectives(proj), "criteria": criteria(proj),
        "topic": topic, "duration": int(dur) if dur.isdigit() else None,
        "formats": formats_of(db, proj), "language_codes": codes,
        "brief": (proj.brief or "").strip(),
    }


def participant_market(p, m: dict) -> str:
    """Respondents belong to this project's market; legacy country codes must not leak."""
    return m.get("country") or market_label(getattr(p, "country", ""))


def participant_language(p, m: dict, fallback: str = "") -> str:
    raw = getattr(p, "language", "") or fallback
    if raw and lang_label(raw) in (m.get("languages") or []):
        return lang_label(raw)
    return m.get("language") or lang_label(raw)
