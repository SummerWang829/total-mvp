"""The command-line sandbox: shortcuts drive the engine exactly like real participant lines would."""
from app.moderator.constants import EndReason, Phase
from app.moderator.engine import ModeratorEngine
from eval.mock_brain import MockBrain
from eval.models import load_guide
from eval.sandbox import run_sandbox


def play(lines: list[str], guide: str = "sg_skincare_zh") -> tuple[ModeratorEngine, list[str]]:
    engine = ModeratorEngine(load_guide(guide), MockBrain())
    feed = iter(lines)
    out: list[str] = []

    def read(prompt: str) -> str:
        try:
            return next(feed)
        except StopIteration:
            raise EOFError from None

    run_sandbox(engine, read, out.append)
    return engine, out


def test_normal_answers_and_state():
    engine, out = play(["好的", "我一般早晚洗脸，涂个面霜。", "/state", "/quit"])
    assert out[0].startswith("AI  > ") and "AI 主持人" in out[0]
    assert any(line.startswith("阶段 ") for line in out) and any("未覆盖" in line for line in out)
    assert engine.fsm.phase is not Phase.ENDED


def test_crisis_shortcut_ends_with_red_alert_and_help_numbers():
    engine, out = play(["好的", "/crisis"])
    assert engine.fsm.end_reason is EndReason.CRISIS and any("红色告警" in line for line in out) and any("1767" in line for line in out)
    assert out[-1].startswith("访谈结束：crisis")


def test_stop_and_ai_question_shortcuts():
    engine, out = play(["好的", "/ai", "/stop"])
    assert any("我是 AI 主持人" in line for line in out) and engine.fsm.end_reason is EndReason.PARTICIPANT_STOPPED


def test_takeover_makes_the_ai_silent_and_handback_resumes():
    _, out = play(["好的", "/takeover", "我一般网上买。", "/handback", "Shopee 比较多。", "/quit"])
    assert any("有人工在接管" in line for line in out)
    assert out.count("AI  > （AI 没有回应：有人工在接管）") == 1  # silent only while taken over
    ai_lines = [line for line in out if line.startswith("AI  > ")]
    # intro, reply to "好的", the silent turn while taken over, then it speaks again after hand-back
    assert len(ai_lines) == 4 and "没有回应" in ai_lines[2] and "没有回应" not in ai_lines[3]


def test_whisper_and_unknown_shortcut_and_english():
    _, out = play(["好的", "/whisper 问问退货", "/whisper", "/nope", "/quit"])
    assert any("悄悄话已记下" in line for line in out) and any("用法" in line for line in out) and any("没有这个快捷操作" in line for line in out)
    engine, out = play(["Sure", "/terse", "/angry", "/quit"], guide="sg_food_delivery_en")
    assert any("你 > Hm." in line for line in out) and engine.guide.language == "en"
