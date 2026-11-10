"""The stages of creating an interview study and the state that goes with them (Project.creation_json).

Eleven stages in a fixed order; the "one-stop" way (questionnaire and interview in one link, AI host only) has no invitation stage. The state also keeps the
conversation, so the same chat shows on the questionnaire page and the outline page. Nothing here calls a model.
"""
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from ..models import Project, uid

MAX_MESSAGES = 200
MAX_SNAPSHOT_CHARS = 60000


class Stage(str, Enum):
    GOAL = "goal"
    MODE = "mode"
    AUDIENCE = "audience"
    SCREEN = "screen"
    CONSENT = "consent"
    FORMAT = "format"
    OUTLINE = "outline"
    FOCUS = "focus"
    INVITE = "invite"
    TRIAL = "trial"
    PUBLISH = "publish"


LABELS = {Stage.GOAL: "目标", Stage.MODE: "主持与招募", Stage.AUDIENCE: "受访者", Stage.SCREEN: "甄别", Stage.CONSENT: "同意", Stage.FORMAT: "形式",
          Stage.OUTLINE: "大纲", Stage.FOCUS: "关注点", Stage.INVITE: "邀请信", Stage.TRIAL: "试访", Stage.PUBLISH: "发布"}
TASKS = {
    Stage.GOAL: "Settle the research background (what is being studied and why) and 1-8 research goals. Take them from the project's brief; ask only what is missing: the decision the study feeds, the target people, the time frame.",
    Stage.MODE: "Settle two things: recruit_mode ('two' = screen first and invite the qualified later; 'one' = questionnaire and interview in one link) and host_mode ('ai' or 'human'). 'one' only works with an AI host. Recommend one and say why.",
    Stage.AUDIENCE: "Settle the total number of interviews, the groups and their targets, a backup number and the calibre of every vague word (region, income, period). Offer 2-3 ways to split the groups when the user has not decided.",
    Stage.SCREEN: "Write the screening questionnaire that selects the target people (follow the screening spec). Use the audience and calibre already settled. Say the expected pass rate and flag anything too strict.",
    Stage.CONSENT: "The legal consent and recording notices are added by the platform. Only check that nothing in the study asks for personal data and tell the user what the participant will be asked to agree to.",
    Stage.FORMAT: "Settle the interview format (audio, text, text with voice transcription, video, video with screen sharing) and the length in minutes. Recommend one from the research goals and say why; check that every question in the outline fits it.",
    Stage.OUTLINE: "Write the interview outline (follow the outline spec): sections with a section guide, questions with follow-up depth and question guides. Every research goal needs at least one question.",
    Stage.FOCUS: "Help the team mark the questions or topics they care about most (a star and a reason). The page stores the marks; you only suggest which ones.",
    Stage.INVITE: "Draft nothing yet: the invitation letter is made by the page. Explain what the invitation will contain (topic, length, room link, consent) and what is still missing.",
    Stage.TRIAL: "The page runs a simulated trial and shows its report. Help the team read it and decide what to change.",
    Stage.PUBLISH: "Check that nothing is left open (the page lists the checks). Tell the user what publishing does and what is locked afterwards.",
}


def stages_for(mode: str) -> list[Stage]:
    """The stages of a study in order. The invitation stage only exists when people are screened first ("two") or the way is not chosen yet."""
    return [s for s in Stage if not (s is Stage.INVITE and mode == "one")]


@dataclass
class State:
    mode: str = ""          # "" (not chosen) | "two" | "one"
    host: str = ""          # "" | "ai" | "human"
    stage: Stage = Stage.GOAL
    done: list[str] = None  # stage values the team has moved past
    decisions: dict = None  # what was confirmed: audience numbers, calibre, ...
    msgs: list[dict] = None

    def __post_init__(self) -> None:
        self.done = self.done or []
        self.decisions = self.decisions or {}
        self.msgs = self.msgs or []

    def dump(self) -> str:
        return json.dumps({"v": 1, "mode": self.mode, "host": self.host, "stage": self.stage.value, "done": self.done, "decisions": self.decisions,
                           "msgs": self.msgs[-MAX_MESSAGES:]}, ensure_ascii=False)


def load(project: Project) -> State:
    """The project's creation state (a fresh one at the first stage when there is none yet or it cannot be read)."""
    try:
        d = json.loads(project.creation_json or "{}")
        stage = Stage(d.get("stage", "goal"))
    except (ValueError, TypeError):
        return State()
    return State(mode=d.get("mode", ""), host=d.get("host", ""), stage=stage, done=list(d.get("done", [])), decisions=dict(d.get("decisions", {})), msgs=list(d.get("msgs", [])))


def save(project: Project, state: State) -> None:
    project.creation_json = state.dump()


def message(role: str, stage: Stage, text: str, **extra: object) -> dict:
    """One chat message as it is stored."""
    return {"id": uid(), "role": role, "stage": stage.value, "text": text, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}


class StageError(ValueError):
    """The move is not possible; the message is plain Chinese."""


def can_go(state: State, target: Stage) -> Stage:
    """The stage to move to, or StageError. Going back is always free; going forward only to a stage this study has (no invitation stage in the one-stop way)."""
    if target not in stages_for(state.mode):
        raise StageError("这个研究方式没有这一阶段")
    return target


def next_after(state: State) -> Stage | None:
    order = stages_for(state.mode)
    i = order.index(state.stage) if state.stage in order else 0
    return order[i + 1] if i + 1 < len(order) else None


def progress(state: State) -> list[dict]:
    """The list the side menu draws: every stage of this study with its label and whether it is done / current / still ahead."""
    return [{"stage": s.value, "label": LABELS[s], "state": "current" if s is state.stage else "done" if s.value in state.done else "todo"} for s in stages_for(state.mode)]
