"""The interview outline in more than one version: one per language the project allows, and one per interview-format family.

Two families: voice / video (audio, video, video with screen sharing) share one outline; text interviews (text, text with voice transcription) share another. A version is
addressed by a key "<language>|<family>". The main version (the outline written first: its language and the voice / video family) stays in `Project.outline_text`, so everything that
already reads the outline keeps working; the others live in `Project.outline_versions`. Versions keep the same parts and questions in the same order, which is what lets a change
made in one version be carried to the others: wording follows the researcher's 同步到 choice (translated for another language), adding or deleting a question or part is
always carried to every version so that they stay lined up.
"""
import logging

from . import formats, guide_text, outline, outline_translate
from . import methods as methods_mod
from .models import Project
from .moderator.providers import ModeratorChatProvider
from .project_langs import NAMES
from .project_langs import of as project_languages

log = logging.getLogger(__name__)

FAMILIES = {"live": ("audio", "video", "video_screen"), "text": ("text", "text_voice")}
FAMILY_NAMES = {"live": "语音 / 视频", "text": "文字访谈"}
STRUCTURAL = ("add", "delete", "add_part", "del_part")  # changes to the shape of the outline: always carried to every version
TOPIC_MAX = 18  # the participant topic (对外标题) is at most 18 characters
WORDING = ("set", "rename", "add", "add_part")  # operations that bring new words, which another language needs translated


class VersionError(ValueError):
    """The version asked for does not exist or cannot be made; the message is plain Chinese."""


def families_of(p: Project) -> list[str]:
    """The families the project's interview formats need (voice / video when nothing was chosen), voice / video first."""
    allowed = set(formats.union(p.formats, methods_mod.of(p))) or set(formats.DEFAULT)
    return [f for f, codes in FAMILIES.items() if allowed & set(codes)] or ["live"]


def key_of(lang: str, family: str) -> str:
    return f"{lang}|{family}"


def split(key: str) -> tuple[str, str]:
    """(language, family) of a version key. Raises VersionError for anything else."""
    lang, _, fam = (key or "").partition("|")
    if lang not in NAMES or fam not in FAMILIES:
        raise VersionError("没有这个版本")
    return lang, fam


def base_key(p: Project) -> str:
    """The main version: the language the outline is written in (the project's default when there is no outline yet), voice / video family."""
    lang, _ = outline.settings_of(p.outline_text, p.language)
    return key_of(lang, "live")


def keys_of(p: Project) -> list[str]:
    """Every version the project can have, the main one first: its languages crossed with its families."""
    base = base_key(p)
    langs = [x for x in project_languages(p) if x in NAMES]
    fams = families_of(p)
    return [base] + [k for k in (key_of(lg, f) for lg in langs for f in fams) if k != base]


def resolve(p: Project, key: str | None) -> str:
    """The key to work on: the one asked for when the project has it, the main version when none was asked. Raises VersionError for a key the project does not have."""
    if not key:
        return base_key(p)
    split(key)
    if key not in keys_of(p):
        raise VersionError("项目没有这个版本")
    return key


def text_of(p: Project, key: str) -> str:
    return p.outline_text if key == base_key(p) else (p.outline_versions or {}).get(key, "")


def put_text(p: Project, key: str, text: str) -> None:
    """Store a version's text. The JSON column is replaced as a whole so the change is noticed."""
    if key == base_key(p):
        p.outline_text = text
    else:
        p.outline_versions = {**(p.outline_versions or {}), key: text}


def listing(p: Project) -> list[dict]:
    """What the version menu shows, one row per version."""
    base, many = base_key(p), len(families_of(p)) > 1
    out = []
    for k in keys_of(p):
        lang, fam = split(k)
        out.append({"key": k, "lang": lang, "family": fam, "base": k == base, "exists": bool(text_of(p, k).strip()),
                    "name": NAMES[lang] + (f" · {FAMILY_NAMES[fam]}" if many else "")})
    return out


def _guide_of(text: str) -> dict:
    try:
        return guide_text.parse_guide_text(text)
    except guide_text.GuideTextError as e:
        raise VersionError("这份大纲的文字还有问题，先改好再生成别的版本：" + str(e)) from e


def generate(p: Project, key: str, chat: ModeratorChatProvider | None) -> str:
    """Make a version that does not exist yet from the best version there is (the same language in another family first, else the main version), translated or rewritten as needed.

    Raises:
        VersionError: no source outline to start from, or the source text is not valid.
        outline_translate.TranslateError: the AI could not translate.
        ChatFatalError: the model service refused.
    """
    lang, fam = split(key)
    others = [k for k in keys_of(p) if k != key and text_of(p, k).strip()]
    src = next((k for k in others if split(k)[0] == lang), None) or (others[0] if others else None)
    if not src:
        raise VersionError("还没有大纲，先让 AI 起草一份")
    g = _guide_of(text_of(p, src))
    slots = outline_translate.slots_of(g)
    rewrite = fam == "text" and split(src)[1] != "text"
    if split(src)[0] != lang or rewrite:
        done = outline_translate.translate(chat, [s for _, s in slots], lang, rewrite=split(src)[0] == lang and rewrite)
        outline_translate.fill(g, [pth for pth, _ in slots], done)
    g["language"] = lang
    g["participant_topic"] = (g.get("participant_topic") or "")[:TOPIC_MAX]
    text = guide_text.guide_to_text(g)
    try:
        guide_text.parse_guide_text(text)
    except guide_text.GuideTextError as e:
        raise VersionError("译出来的大纲没有通过检查，请再试一次：" + str(e)) from e
    put_text(p, key, text)
    return text


def targets_for(p: Project, cur: str, sync: list[str], op: str) -> list[str]:
    """The other versions a change goes to: every existing version for a change of shape, the ones chosen in 同步到 for a change of words. Versions not made yet are never touched."""
    chosen = set(sync or [])
    return [k for k in keys_of(p) if k != cur and text_of(p, k).strip() and (op in STRUCTURAL or k in chosen)]


def edit(p: Project, cur: str, sync: list[str], op: str, kind: str, ref: int, index: int, value: str, chat: ModeratorChatProvider | None) -> dict:
    """Change one question or part in the current version and carry it to the other versions. Nothing is saved unless every translation worked.

    Returns:
        {"synced": [keys that got the change], "skipped": [{"key", "reason"}]}: a version whose parts do not line up any more is left alone and reported.

    Raises:
        outline.EditError: the change cannot be made in the current version.
        outline_translate.TranslateError / ChatFatalError: the translation failed (nothing changed).
    """
    results = {cur: outline.edit(text_of(p, cur), op, kind, ref, index, value)}
    targets = targets_for(p, cur, sync, op)
    cur_lang = split(cur)[0]
    words = [x.strip() for x in (value.partition("|")[::2] if op == "add_part" else [value])]
    made: dict[str, list[str]] = {}
    if op in WORDING:
        for lang in {split(t)[0] for t in targets} - {cur_lang}:
            made[lang] = outline_translate.translate(chat, words, lang)
    skipped = []
    for t in targets:
        lang = split(t)[0]
        w = words if lang == cur_lang or op not in WORDING else made[lang]
        try:
            results[t] = outline.edit(text_of(p, t), op, kind, ref, index, "|".join(w) if op == "add_part" else (w[0] if w else value))
        except outline.EditError as e:
            skipped.append({"key": t, "reason": str(e)})
    for k, txt in results.items():
        put_text(p, k, txt)
    return {"synced": [k for k in results if k != cur], "skipped": skipped}


def set_header(p: Project, cur: str, sync: list[str], title: str | None, minutes: int | None, chat: ModeratorChatProvider | None) -> dict:
    """Set the participant topic (对外标题) and / or the length in the current version and the versions chosen in 同步到 (the topic translated for another language).

    Raises:
        outline.EditError: the current version has no valid outline yet.
        outline_translate.TranslateError / ChatFatalError: the translation failed (nothing changed).
    """
    targets = [k for k in keys_of(p) if k != cur and k in set(sync or []) and text_of(p, k).strip()]
    cur_lang = split(cur)[0]
    made = {lang: outline_translate.translate(chat, [title], lang)[0] for lang in {split(t)[0] for t in targets} - {cur_lang}} if title else {}
    results = {cur: outline.set_headers(text_of(p, cur), title, minutes)}
    skipped = []
    for t in targets:
        try:
            results[t] = outline.set_headers(text_of(p, t), made.get(split(t)[0], title), minutes)
        except outline.EditError as e:
            skipped.append({"key": t, "reason": str(e)})
    for k, txt in results.items():
        put_text(p, k, txt)
    return {"synced": [k for k in results if k != cur], "skipped": skipped}
