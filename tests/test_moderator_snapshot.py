"""A running AI moderator can be saved and loaded again (a participant leaves and returns, or the server restarts), and interview time only runs while they are there."""
import pickle

import pytest

from app.moderator import snapshot
from app.moderator.clock import ActiveClock
from app.moderator.engine import ModeratorEngine
from app.moderator.knowledge import InterviewGuide
from eval.mock_brain import MockBrain
import json
from pathlib import Path

GUIDE = InterviewGuide.model_validate(json.loads((Path(__file__).resolve().parent.parent / "eval" / "guides" / "sg_skincare_zh.json").read_text(encoding="utf-8")))
ANSWERS = ["我平时会先看成分，因为皮肤比较敏感，之前用过一款换季就过敏。", "主要是在小红书和朋友推荐，我比较相信真实用户的体验。", "价格大概两百以内吧，太贵的我不会买。"]


def running_engine():
    engine = ModeratorEngine(GUIDE, MockBrain(), clock=ActiveClock())
    engine.open()
    return engine


def test_a_saved_engine_carries_on_where_it_stopped(monkeypatch):
    engine = running_engine()
    for a in ANSWERS[:2]:
        engine.handle_participant(a)
    turns, phase = len(engine.memory.turns), engine.fsm.phase
    again = snapshot.load(snapshot.dump(engine), MockBrain())
    assert len(again.memory.turns) == turns and again.fsm.phase is phase and again.turn_log == []
    reply = again.handle_participant(ANSWERS[2])
    assert reply.spoken_response and len(again.memory.turns) == turns + 2
    assert again.fsm.snapshot().elapsed_s >= 0


def test_a_blob_that_was_changed_or_is_not_ours_is_refused_before_loading():
    data = bytearray(snapshot.dump(running_engine()))
    data[-5] ^= 1
    with pytest.raises(snapshot.SnapshotError):
        snapshot.load(bytes(data), MockBrain())
    forged = pickle.dumps({"not": "an engine"})
    with pytest.raises(snapshot.SnapshotError):
        snapshot.load(b"\x00" * 32 + forged, MockBrain())


def test_the_active_clock_counts_a_long_absence_as_at_most_the_cap(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr("app.moderator.clock.time.time", lambda: t[0])
    clock = ActiveClock(idle_cap_s=180)
    t[0] += 60
    assert clock.now() == 60
    t[0] += 3600 * 5   # the participant left for five hours
    assert clock.now() == 60 + 180
    saved = pickle.loads(pickle.dumps(clock))
    t[0] += 3600 * 24
    saved.resume()   # loaded a day later: the day away does not count
    t[0] += 30
    assert saved.now() == 60 + 180 + 30
