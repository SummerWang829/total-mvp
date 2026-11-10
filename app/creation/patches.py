"""Turning the model's "patch" of one stage into changes of the study. The code decides what changed; the page's change list comes from here, never from the model's words.

Every patch is checked strictly and a bad one is refused as a whole (PatchError), so the study is never half changed. `snapshot` / `restore` make each change undoable.
"""
import json

from sqlalchemy.orm import Session

from .. import formats as formats_mod
from .. import guide_text, outline_versions
from ..models import Project, RecruitForm, utcnow
from .. import recruit_form
from .stages import MAX_SNAPSHOT_CHARS, Stage, State

MAX_GOALS, MAX_GROUPS, MAX_TERMS = 8, 8, 12


class PatchError(ValueError):
    """The model's patch cannot be applied; the message says why (in English, for the retry; the page shows a plain Chinese sentence instead)."""


def _text(v: object, limit: int) -> str:
    return " ".join(str(v or "").split())[:limit]


def _int(v: object, lo: int, hi: int, what: str) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError) as e:
        raise PatchError(f"{what} must be a whole number") from e
    if not lo <= n <= hi:
        raise PatchError(f"{what} must be between {lo} and {hi}")
    return n


def _form(db: Session, project: Project) -> RecruitForm:
    f = db.get(RecruitForm, project.id)
    if f is None:
        f = RecruitForm(project_id=project.id, org_id=project.org_id)
        db.add(f)
        db.flush()
    return f


def snapshot(db: Session, project: Project, state: State) -> str:
    """The parts of the study a stage can change, as JSON (stored on the AI message so the change can be undone)."""
    f = db.get(RecruitForm, project.id)
    snap = {"brief": project.brief or "", "formats": project.formats or {}, "outline_text": project.outline_text or "", "mode": state.mode, "host": state.host, "recruit_mode": project.recruit_mode, "host_mode": project.host_mode,
            "decisions": state.decisions, "goals": list(f.goals or []) if f else [], "need": f.need if f else "", "questions": list(f.questions or []) if f else [],
            "has_form": bool(f)}
    out = json.dumps(snap, ensure_ascii=False)
    return out if len(out) <= MAX_SNAPSHOT_CHARS else ""


def restore(db: Session, project: Project, state: State, raw: str) -> None:
    """Put back what `snapshot` saved."""
    snap = json.loads(raw)
    project.brief, project.formats, project.outline_text = snap["brief"], snap["formats"], snap["outline_text"]
    state.mode, state.host, state.decisions = snap["mode"], snap["host"], snap["decisions"]
    project.recruit_mode, project.host_mode = snap.get("recruit_mode", project.recruit_mode), snap.get("host_mode", project.host_mode)
    f = _form(db, project) if snap.get("has_form") else db.get(RecruitForm, project.id)   # a form made by the undone change is emptied, not deleted
    if f is not None:
        f.goals, f.need, f.questions, f.updated_at = snap["goals"], snap["need"], snap["questions"], utcnow()


def apply_goal(db: Session, project: Project, state: State, patch: dict) -> list[str]:
    done = []
    if "brief" in patch:
        brief = _text(patch["brief"], 600)
        if not brief:
            raise PatchError("brief is empty")
        project.brief = brief
        done.append("已更新研究背景")
    if "goals" in patch:
        goals = [_text(g, 200) for g in (patch["goals"] if isinstance(patch["goals"], list) else []) if _text(g, 200)][:MAX_GOALS]
        if not goals:
            raise PatchError("goals must be a list of 1-8 sentences")
        _form(db, project).goals = goals
        done.append(f"已更新研究目标（{len(goals)} 条）")
    return done


def apply_mode(db: Session, project: Project, state: State, patch: dict) -> list[str]:
    mode, host = patch.get("recruit_mode", state.mode), patch.get("host_mode", state.host)
    if mode not in ("two", "one") or host not in ("ai", "human"):
        raise PatchError("recruit_mode must be 'two' or 'one' and host_mode 'ai' or 'human'")
    if mode == "one" and host != "ai":
        raise PatchError("the one-stop way only works with an AI host")
    state.mode, state.host = mode, host
    project.recruit_mode, project.host_mode = ("onelink" if mode == "one" else "screen_then_invite"), host   # the same two fields the 招募方式 page sets
    return ["已设为：" + ("一站式（问卷和访谈一个链接）" if mode == "one" else "先问卷筛选、再邀请") + "，" + ("AI 主持" if host == "ai" else "真人主持")]


def apply_audience(db: Session, project: Project, state: State, patch: dict) -> list[str]:
    total = _int(patch.get("total"), 1, 100000, "total")
    backup = _int(patch.get("backup", 0), 0, 100000, "backup")
    groups = []
    for g in (patch.get("groups") if isinstance(patch.get("groups"), list) else [])[:MAX_GROUPS]:
        name = _text(g.get("name") if isinstance(g, dict) else "", 40)
        if not name:
            raise PatchError("every group needs a name")
        groups.append({"name": name, "target": _int(g.get("target"), 0, 100000, "group target"), "rule": _text(g.get("rule"), 200)})
    if groups and sum(g["target"] for g in groups) != total:
        raise PatchError(f"the group targets add up to {sum(g['target'] for g in groups)}, not the total {total}")
    caliber = [{"term": _text(c.get("term"), 40), "definition": _text(c.get("definition"), 200)} for c in (patch.get("caliber") if isinstance(patch.get("caliber"), list) else [])[:MAX_TERMS]
               if isinstance(c, dict) and _text(c.get("term"), 40) and _text(c.get("definition"), 200)]
    state.decisions["audience"] = {"total": total, "backup": backup, "groups": groups, "caliber": caliber}
    need = f"共 {total} 人" + (f"，备份 {backup} 人" if backup else "") + "；" + "；".join(f"{g['name']} {g['target']} 人（{g['rule']}）" if g["rule"] else f"{g['name']} {g['target']} 人" for g in groups)
    f = _form(db, project)
    f.need = need[:1000]
    return [f"已更新招募需求：共 {total} 人" + (f"，{len(groups)} 组" if groups else "")] + ([f"已记录 {len(caliber)} 个口径"] if caliber else [])


def apply_screen(db: Session, project: Project, state: State, patch: dict) -> list[str]:
    raw = patch.get("questions")
    if not isinstance(raw, list) or not raw:
        raise PatchError("questions must be a non-empty list")
    f = _form(db, project)
    try:
        body = recruit_form.normalize({"source": "ai", "questions": raw, "goals": list(f.goals or []), "need": f.need or "", "idea": f.idea or ""})
    except recruit_form.FormError as e:
        raise PatchError(f"the questionnaire is not valid: {e}") from e
    f.source, f.source_confirmed, f.html_url = "ai", "ai", ""
    f.questions = [q.model_dump() for q in body.questions]
    f.version, f.updated_at = (f.version or 0) + 1, utcnow()
    return [f"已更新甄别问卷（{len(body.questions)} 题）"]


def apply_format(db: Session, project: Project, state: State, patch: dict) -> list[str]:
    done = []
    if "formats" in patch:
        try:
            codes = formats_mod.clean(patch["formats"] if isinstance(patch["formats"], list) else [])
        except formats_mod.FormatError as e:
            raise PatchError(str(e)) from e
        if not codes:
            raise PatchError("at least one format")
        project.formats = {**(project.formats or {}), "depth": codes}
        done.append("已更新访谈形式：" + "、".join(formats_mod.FORMATS[c] for c in codes))
    if "minutes" in patch:
        minutes = _int(patch["minutes"], 10, 180, "minutes")
        text = project.outline_text or ""
        if text.strip():
            try:
                from ..outline import set_headers
                project.outline_text = set_headers(text, None, minutes)
            except Exception as e:  # noqa: BLE001 - an unreadable outline text cannot take the new length yet; the length is kept in the decisions
                raise PatchError(f"the outline text cannot take the length yet: {type(e).__name__}") from e
        state.decisions["minutes"] = minutes
        done.append(f"已设定时长：约 {minutes} 分钟")
    return done


MAX_SECTIONS = 10


def build_guide(raw: dict, goal: str, language: str, minutes: int) -> tuple[dict, dict]:
    """(guide dict, meta) from the model's outline: sections with a section guide, questions with follow-up depth and a question guide.

    Raises:
        PatchError: the outline is not usable (shape, empty parts, a red line).
    """
    from pydantic import ValidationError

    from ..moderator.knowledge import InterviewGuide
    from ..study_draft import _red_line_problem  # noqa: PLC2701 - the engine's own red-line check of prepared questions

    topics_raw = raw.get("topics") if isinstance(raw.get("topics"), list) else []
    if not topics_raw or not isinstance(raw.get("warmup_questions"), list) or not raw["warmup_questions"]:
        raise PatchError("the outline needs warmup_questions and at least one topic")
    topics, meta_topics = [], []
    for i, t in enumerate(topics_raw[:MAX_SECTIONS], 1):
        if not isinstance(t, dict) or not isinstance(t.get("questions"), list) or not t["questions"]:
            raise PatchError(f"topic {i} has no questions")
        qs = [_text(q, 300) for q in t["questions"]]
        topics.append({"id": f"t{i}", "title": _text(t.get("title"), 120) or f"环节 {i}", "questions": qs, "section_guide": _text(t.get("section_guide"), guide_text.MAX_NOTE),
                       "must_cover": [{"id": f"t{i}c{j}", "text": _text(m, 300)} for j, m in enumerate(t.get("must_cover") or [], 1) if _text(m, 300)]})
        meta_topics.append(_meta_list(qs, t.get("depth"), t.get("guide")))
    warm = [_text(q, 300) for q in raw["warmup_questions"]]
    try:
        guide = InterviewGuide.model_validate({"research_goal": goal[:1000] or "研究", "participant_topic": _text(raw.get("participant_topic"), 18), "language": language,
                                               "duration_min": max(10, min(180, minutes)), "warmup_questions": warm, "topics": topics})
    except ValidationError as e:
        raise PatchError(f"not a valid outline: {e.errors()[0]['msg']}") from e
    if why := _red_line_problem(guide):
        raise PatchError(why)
    return guide.model_dump(mode="json"), {"warmup": _meta_list(warm, raw.get("warmup_depth"), raw.get("warmup_guide")), "topics": meta_topics, "stimuli": []}


def _meta_list(questions: list[str], depths: object, guides: object) -> list[dict]:
    depths, guides = depths if isinstance(depths, list) else [], guides if isinstance(guides, list) else []
    out = []
    for i in range(len(questions)):
        d = str(depths[i]).lower() if i < len(depths) else guide_text.DEFAULT_DEPTH
        out.append({"depth": d if d in guide_text.DEPTHS else guide_text.DEFAULT_DEPTH, "guide": _text(guides[i] if i < len(guides) else "", guide_text.MAX_NOTE)})
    return out


def apply_outline(db: Session, project: Project, state: State, patch: dict) -> list[str]:
    raw = patch.get("outline")
    if not isinstance(raw, dict):
        raise PatchError("outline must be an object")
    f = db.get(RecruitForm, project.id)
    joined = "；".join(list(f.goals or [])) if f else ""
    goal = " ".join((joined or project.brief or "").split())[:1000]
    guide, meta = build_guide(raw, goal, project.language, int(state.decisions.get("minutes") or 45))
    text = guide_text.guide_to_text({**guide, "meta": meta})
    guide_text.parse_guide_text(text)  # must read back
    project.outline_text = text
    outline_versions.put_text(project, outline_versions.base_key(project), text)
    return [f"已更新访谈大纲：{len(guide['topics'])} 个环节、{sum(len(t['questions']) for t in guide['topics']) + len(guide['warmup_questions'])} 个问题"]


APPLY = {Stage.GOAL: apply_goal, Stage.MODE: apply_mode, Stage.AUDIENCE: apply_audience, Stage.SCREEN: apply_screen, Stage.FORMAT: apply_format, Stage.OUTLINE: apply_outline}


def apply(db: Session, project: Project, state: State, patch: dict) -> list[str]:
    """Apply the patch of the current stage and return the list of changes (empty when the stage has nothing to patch or the patch is empty).

    Raises:
        PatchError: the patch is not valid; nothing was changed.
    """
    fn = APPLY.get(state.stage)
    if not patch or fn is None:
        return []
    return fn(db, project, state, patch)
