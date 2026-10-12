"""Command-line sandbox: you play the participant and watch how the AI moderator reacts (task doc Sprint 4).

    python -m eval.sandbox                       # model from .env (DeepSeek), guide sg_skincare_zh
    python -m eval.sandbox --brain mock          # offline, scripted brain
    python -m eval.sandbox --guide sg_food_delivery_en

Shortcuts (typed instead of an answer):
    /whisper <text>   an observer's private note to the AI (the participant never sees it)
    /angry            say an annoyed line        /terse   give a very short answer
    /stop             say you want to stop       /crisis  say a crisis line (the AI must stop and alert)
    /ai               ask "are you an AI?"       /takeover  a human takes over (AI goes silent)   /handback  hand back
    /state            show phase, clock, coverage and the last turn's internal note               /quit    leave
"""
import argparse
import logging
from collections.abc import Callable

from app.config import settings
from app.moderator.constants import AlertLevel, ControlMode, Phase
from app.moderator.engine import ModeratorEngine
from app.moderator.providers import ModeratorChatProvider, OpenAICompatModeratorChat
from app.moderator.schemas import ModeratorTurnPayload

from .mock_brain import MockBrain
from .models import load_guide

# What the shortcuts make "you" say, per language.
CANNED: dict[str, dict[str, str]] = {
    "zh": {"/angry": "你们问这些到底有什么用？真烦人。", "/terse": "嗯。", "/stop": "我不想继续了。", "/crisis": "说实话我最近不想活了。", "/ai": "你是真人吗？"},
    "en": {"/angry": "What is the point of all these questions? This is annoying.", "/terse": "Hm.", "/stop": "I want to stop.",
           "/crisis": "Honestly, I don't want to live anymore.", "/ai": "Are you a real person?"},
}
NO_REPLY = "（AI 没有回应：有人工在接管）"
ALERT_LABEL = {AlertLevel.YELLOW: "🟡 告警", AlertLevel.RED: "🔴 红色告警"}

Output = Callable[[str], None]
Input = Callable[[str], str]


def _show_turn(payload: ModeratorTurnPayload, out: Output) -> None:
    """Print what the AI said, and anything staff would see (alerts, rejected drafts, fallback)."""
    out(f"AI  > {payload.spoken_response}" if payload.spoken_response else f"AI  > {NO_REPLY}")
    if payload.alert is not AlertLevel.NONE:
        out(f"      {ALERT_LABEL[payload.alert]}：{payload.alert_reason}")
    if payload.rejected:
        out(f"      [红线拦下 {len(payload.rejected)} 次：{'; '.join(','.join(v.red_line.value for v in vs) for vs in payload.rejected)}]")
    if payload.used_fallback:
        out("      [用了安全兜底句]")
    if payload.plan:
        out(f"      [{payload.plan.operator.value} · 阶段 {payload.plan.decision.phase.value} · {payload.latency_s:.1f} 秒]")


def _show_state(engine: ModeratorEngine, out: Output) -> None:
    """Print the clock, position and coverage."""
    snap = engine.fsm.snapshot()
    out(f"阶段 {snap.phase.value} · 已用 {snap.elapsed_s / 60:.1f}/{snap.total_s / 60:.0f} 分 · 主题 {snap.topic_title or '-'} · 饱和度 {snap.saturation:.2f}"
        f" · 追问 {snap.followups_used}/{snap.followups_used + snap.followups_left} · 控制 {engine.control.value}")
    out(f"未覆盖：{'；'.join(i.text for i in engine.fsm.missed_items()) or '无'}")
    if engine.turn_log and engine.turn_log[-1].note.research_purpose:
        note = engine.turn_log[-1].note
        out(f"内部记录（受访者看不到）：目的={note.research_purpose} 预期={note.expected_information} 逻辑={note.probe_logic}")


def _whisper(engine: ModeratorEngine, text: str, out: Output) -> None:
    """/whisper: queue an observer note for the AI's next turn."""
    if not text:
        out("用法：/whisper 想让 AI 注意的话")
        return
    engine.add_whisper(text)
    out("（悄悄话已记下，下一轮 AI 会参考，受访者看不到）")


def _switch_control(engine: ModeratorEngine, to: ControlMode, out: Output) -> None:
    """/takeover and /handback: change who speaks for the team, saying so if nothing changes."""
    if engine.control is to:
        out("已经在接管中" if to is ControlMode.HUMAN else "现在是 AI 在主持")
    elif to is ControlMode.HUMAN:
        engine.take_over()
    else:
        engine.hand_back()


def run_sandbox(engine: ModeratorEngine, read: Input, out: Output) -> None:
    """The interactive loop. Returns when the interview ends or you type /quit.

    Args:
        engine: an engine that has not been opened yet.
        read: reads one line (input() in the terminal; a scripted function in tests).
        out: prints one line.
    """
    canned = CANNED[engine.guide.language]
    out(f"AI  > {engine.open().spoken_response}")
    while engine.fsm.phase is not Phase.ENDED:
        try:
            line = read("你 > ").strip()
        except EOFError:
            return
        if not line:
            continue
        command, _, rest = line.partition(" ")
        if command == "/quit":
            return
        if command == "/state":
            _show_state(engine, out)
        elif command == "/whisper":
            _whisper(engine, rest, out)
        elif command == "/takeover":
            _switch_control(engine, ControlMode.HUMAN, out)
        elif command == "/handback":
            _switch_control(engine, ControlMode.AI, out)
        elif command in canned or not command.startswith("/"):
            text = canned.get(command, line)
            if command in canned:
                out(f"你 > {text}")
            _show_turn(engine.handle_participant(text), out)
        else:
            out(f"没有这个快捷操作：{command}")
    out(f"访谈结束：{engine.fsm.end_reason.value if engine.fsm.end_reason else '-'}")


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Play the participant against the AI moderator")
    parser.add_argument("--guide", default="sg_skincare_zh")
    parser.add_argument("--brain", choices=["mock", "model"], default="model" if settings.moderator_provider != "mock" else "mock")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    chat: ModeratorChatProvider = MockBrain() if args.brain == "mock" else OpenAICompatModeratorChat(settings)
    print(f"模型：{chat.name}；输入 /quit 退出；快捷操作见文件开头说明。")
    run_sandbox(ModeratorEngine(load_guide(args.guide), chat), input, print)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
