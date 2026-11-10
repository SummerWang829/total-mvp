"""One turn of the creation dialog: build the stage prompt from the study as it is, ask the model, check and apply its patch, and keep the conversation.

The model only proposes; the code decides. A patch is applied all-or-nothing (a bad one is sent back to the model with the reason, three tries), the list of changes the
page shows comes from what was really applied, and every change can be undone from the snapshot kept with the message.
"""
import json
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .. import ai_prompts, assistant_identity, library_context
from ..models import Project, RecruitForm
from ..moderator.providers import ChatError, ChatFatalError, ChatFormatError, ModeratorChatProvider
from . import patches, specs
from .stages import LABELS, TASKS, Stage, State, message

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
HISTORY = 8
MAX_NEEDS, MAX_OPTIONS, MAX_SUGGESTIONS = 3, 4, 3
MSG_FAIL = "这一步没有成功，请换个说法再试，或直接在右边修改"
START = "[stage start]"


class CreationError(Exception):
    """The turn could not be completed; the message is plain Chinese for the page."""


@dataclass
class Reply:
    reply: str
    advice: str = ""
    needs: list[dict] = field(default_factory=list)
    patch: dict = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    ready: bool = False


def system_prompt() -> str:
    """The stage prompt with the two specs filled in (each can be edited in the AI console)."""
    return (ai_prompts.get("creation.chat", specs.SYSTEM).replace("{outline_spec}", ai_prompts.get("creation.outline_spec", specs.OUTLINE_SPEC))
            .replace("{recruit_spec}", ai_prompts.get("creation.recruit_spec", specs.RECRUIT_SPEC)))


def document(db: Session, project: Project, state: State) -> dict:
    """The study as it is now, small enough to send with every turn."""
    f = db.get(RecruitForm, project.id)
    questions = [{"id": q.get("id"), "type": q.get("type"), "text": q.get("text"), "options": [{"id": o.get("id"), "text": o.get("text"), "goto": o.get("goto")} for o in q.get("options", [])]}
                 for q in (f.questions if f else []) or []]
    return {"brief": project.brief or "", "goals": list(f.goals or []) if f else [], "mode": state.mode or "not chosen", "host": state.host or "not chosen", "audience": state.decisions.get("audience"),
            "questionnaire": questions, "formats": (project.formats or {}).get("depth", []), "minutes": state.decisions.get("minutes"), "language": project.language,
            "outline": (project.outline_text or "")[:6000]}


def _clean(raw: object) -> Reply:
    """The model's JSON as a Reply, or ValueError."""
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    text = " ".join(str(raw.get("reply", "")).split())[:600]
    if not text:
        raise ValueError("no reply")
    needs = []
    for n in (raw.get("needs") if isinstance(raw.get("needs"), list) else [])[:MAX_NEEDS]:
        if isinstance(n, dict) and str(n.get("question", "")).strip():
            opts = [" ".join(str(o).split())[:60] for o in (n.get("options") if isinstance(n.get("options"), list) else []) if str(o).strip()][:MAX_OPTIONS]
            needs.append({"id": str(n.get("id") or f"n{len(needs) + 1}")[:30], "question": " ".join(str(n["question"]).split())[:240], "options": opts})
    sugg = [" ".join(str(s).split())[:80] for s in (raw.get("suggestions") if isinstance(raw.get("suggestions"), list) else []) if str(s).strip()][:MAX_SUGGESTIONS]
    return Reply(reply=text, advice=" ".join(str(raw.get("advice", "")).split())[:240], needs=needs, patch=raw.get("patch") if isinstance(raw.get("patch"), dict) else {},
                 suggestions=sugg, ready=bool(raw.get("ready")) and not needs)


def converse(chat: ModeratorChatProvider, db: Session, project: Project, state: State, text: str, material: str, problem: str) -> Reply:
    """Ask the model once for this stage.

    Raises:
        ChatFatalError: the service refused (bad key, no balance...).
        ChatError: no usable answer.
        ValueError: the answer is not shaped like a reply.
    """
    history = [{"role": m["role"], "text": m["text"]} for m in state.msgs if m.get("stage") == state.stage.value][-HISTORY:]
    payload = {"CURRENT_STAGE": state.stage.value, "STAGE_TASK": TASKS[state.stage], "DOCUMENT": document(db, project, state), "DECISIONS": state.decisions, "HISTORY": history,
               "USER": text, "MATERIAL": material[:30000], "LIBRARY": library_context.library_text(db, project.id)[0], "LANGUAGE": project.language}
    if problem:
        payload["YOUR_PREVIOUS_ANSWER_WAS_REJECTED"] = problem
    raw = chat.complete([{"role": "system", "content": system_prompt()}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], temperature=0.4)
    return _clean(raw)


def run_turn(db: Session, chat: ModeratorChatProvider | None, project: Project, state: State, text: str, material: str = "") -> dict:
    """Do one turn of the current stage: the model's reply, its patch applied (all or nothing) and both messages stored. Returns the AI message.

    Raises:
        CreationError: no usable answer after MAX_ATTEMPTS.
        ChatFatalError: the service refused; nothing was changed.
    """
    if text != START and assistant_identity.asks_identity(text):   # "你是什么模型": 小星星助手 answers here, no model is asked and the study stays as it is
        state.msgs.append(message("user", state.stage, text))
        ai = message("ai", state.stage, assistant_identity.REPLY, actions=[], advice="", needs=[], suggestions=[], ready=False, snapshot="")
        state.msgs.append(ai)
        return ai
    problem, reply, actions, snap = "", None, [], ""
    for attempt in range(MAX_ATTEMPTS):
        try:
            reply = mock_reply(db, project, state, text) if chat is None else converse(chat, db, project, state, text, material, problem)
        except ChatFatalError:
            raise
        except ChatFormatError:
            problem = "not valid JSON (a bracket or quote was wrong)"
            continue
        except ChatError as e:
            raise CreationError("小星星助手暂时无法回答，请稍后再试，或直接在右边修改") from e
        except (ValueError, KeyError, TypeError) as e:
            problem = f"not a usable reply ({e})"
            continue
        snap = patches.snapshot(db, project, state)
        try:
            actions = patches.apply(db, project, state, reply.patch)
            break
        except patches.PatchError as e:
            if snap:
                patches.restore(db, project, state, snap)
            problem, reply = str(e), None
            log.warning("creation turn %d (stage %s): patch refused", attempt + 1, state.stage.value)
    if reply is None:
        raise CreationError(MSG_FAIL)
    if text != START:
        state.msgs.append(message("user", state.stage, text))
    ai = message("ai", state.stage, reply.reply, actions=actions, advice=reply.advice, needs=reply.needs, suggestions=reply.suggestions, ready=reply.ready,
                 snapshot=snap if actions else "")
    state.msgs.append(ai)
    return ai


# ---- the offline assistant (no model configured): fixed, plausible answers so every stage can be walked through ----
def mock_reply(db: Session, project: Project, state: State, text: str) -> Reply:
    from .. import recruit_form, study_draft
    brief = " ".join((project.brief or "").split())
    opening = text == START
    stage = state.stage
    head = f"（演示模式）{LABELS[stage]}阶段：" + ("我先按现有内容整理。" if opening else "我已按你说的更新。")
    if stage is Stage.GOAL:
        parts = [p.strip() for p in brief.replace("；", "。").split("。") if p.strip()][:3]
        return Reply(head, patch={"goals": parts or ["了解目标人群的真实经历和看法"]} if parts or not brief else {}, suggestions=["下一步：选择招募和主持方式"], ready=True)
    if stage is Stage.MODE:
        return Reply(head, advice="两步走更适合需要先筛选再邀请的研究。", needs=[] if not opening else [{"id": "mode", "question": "用哪种方式招募？", "options": ["先问卷筛选、再邀请", "一站式"]}],
                     patch={} if opening else {"recruit_mode": state.mode or "two", "host_mode": state.host or "human"}, suggestions=["先问卷筛选、再邀请，真人主持"], ready=not opening)
    if stage is Stage.AUDIENCE:
        return Reply(head, patch={} if opening else {"total": 30, "backup": 4, "groups": [], "caliber": []}, suggestions=["共 30 人，备份 4 人"], ready=not opening)
    if stage is Stage.SCREEN:
        qs = recruit_form.mock_questions(brief or "研究", project.language)
        return Reply(head, patch={} if opening else {"questions": qs}, suggestions=["起草甄别问卷"], ready=not opening)
    if stage is Stage.FORMAT:
        return Reply(head, patch={} if opening else {"formats": ["video"], "minutes": 45}, suggestions=["用视频，45 分钟"], ready=not opening)
    if stage is Stage.OUTLINE:
        raw = study_draft.mock_draft(brief or "研究", project.language, 45)
        raw["topics"] = [{**t, "section_guide": "（演示）先问具体经历，再问看法。", "depth": ["shallow"] * len(t["questions"]), "guide": [""] * len(t["questions"])} for t in raw["topics"]]
        return Reply(head, patch={} if opening else {"outline": raw}, suggestions=["按研究目标起草大纲"], ready=not opening)
    return Reply(head + "这一阶段由页面完成。", ready=True)
