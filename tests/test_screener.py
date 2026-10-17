"""The screening questionnaire answers: stored with the participant, team-only, deleted with the participant, and read by the AI moderator."""
import json

import pytest

from app import ai_room, screener
from app.consent import AI_PROFILE, consent_for
from app.db import SessionLocal
from app.models import ScreenerAnswer
from conftest import Org
from test_ai_room import _start, _walk_into_the_room
from test_moderator_engine import GUIDE, PROMPTS, engine, started

TEXT = "多久用一次护肤品：每天\n主要购买渠道：屈臣氏\nHow often do you shop: twice a week"


# ---------- reading the pasted text ----------
def test_pasted_lines_are_split_at_the_first_colon():
    assert screener.parse_text("时间：晚上 10:30 以后\n\n  Q: yes  ") == [("时间", "晚上 10:30 以后"), ("Q", "yes")]


@pytest.mark.parametrize("text, line", [("没有分隔符", 1), ("好：好\n只有题目：", 2), ("：只有答案", 1)])
def test_a_bad_line_names_its_line_number(text, line):
    with pytest.raises(screener.ScreenerError, match=f"第 {line} 行"):
        screener.parse_text(text)


def test_too_long_or_too_many_lines_are_refused():
    with pytest.raises(screener.ScreenerError):
        screener.parse_text("题：" + "答" * (screener.MAX_ANSWER_CHARS + 1))
    with pytest.raises(screener.ScreenerError, match="最多"):
        screener.parse_text("\n".join(f"题{i}：答" for i in range(screener.MAX_ITEMS + 1)))


# ---------- the API ----------
def _add(org, pid, **extra):
    return org.c.post(f"/api/projects/{pid}/participants", headers=org.h(), json={"name": "Mei", "country": "SG", "language": "zh", **extra})


def test_answers_are_stored_listed_and_replaced(org):
    pid = org.project()
    r = _add(org, pid, screener_text=TEXT)
    assert r.status_code == 201 and r.json()["screener"] == 3
    part = r.json()["id"]
    assert [p["screener"] for p in org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()] == [3]
    got = org.c.get(f"/api/participants/{part}/screener", headers=org.h()).json()
    assert [i["question"] for i in got["items"]] == ["多久用一次护肤品", "主要购买渠道", "How often do you shop"] and "每天" in got["text"]
    assert org.c.put(f"/api/participants/{part}/screener", headers=org.h(), json={"text": "肤质：油性"}).json() == {"count": 1}
    assert [i["answer"] for i in org.c.get(f"/api/participants/{part}/screener", headers=org.h()).json()["items"]] == ["油性"]


def test_a_bad_line_is_refused_with_its_number_and_nothing_is_saved(org):
    pid = org.project()
    r = _add(org, pid, screener_text="好：好\n坏了")
    assert r.status_code == 422 and "第 2 行" in r.json()["detail"]
    assert org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json() == []


def test_observers_and_other_companies_cannot_read_or_change_answers(org, client):
    part = _add(org, org.project(), screener_text=TEXT).json()["id"]
    url = f"/api/participants/{part}/screener"
    assert org.c.get(url, headers=org.h("observer")).status_code == 403
    assert org.c.put(url, headers=org.h("observer"), json={"text": "a：b"}).status_code == 403
    other = Org(client, "Other", "b@other.com")
    assert other.c.get(url, headers=other.h()).status_code == 404
    assert other.c.put(url, headers=other.h(), json={"text": "a：b"}).status_code == 404


def test_erasing_the_participant_deletes_the_answers(org):
    part = _add(org, org.project(), screener_text=TEXT).json()["id"]
    assert org.c.delete(f"/api/participants/{part}/data", headers=org.h()).status_code == 200
    with SessionLocal() as db:
        assert db.query(ScreenerAnswer).count() == 0
    assert org.c.put(f"/api/participants/{part}/screener", headers=org.h(), json={"text": "a：b"}).status_code == 409


def test_the_profile_lines_are_capped_for_the_model(org):
    pid = org.project()
    big = "\n".join(f"第{i}题：" + "答" * 400 for i in range(screener.MAX_ITEMS))
    part = _add(org, pid, screener_text=big).json()["id"]
    with SessionLocal() as db:
        lines = screener.profile_lines(db, part)
    assert 0 < len(lines) < screener.MAX_ITEMS and sum(len(x) for x in lines) <= screener.PROFILE_CONTEXT_CHARS and lines[0].startswith("第0题 → ")


# ---------- the AI moderator reads them ----------
def test_the_ai_room_gives_the_engine_this_participants_answers(org):
    pid = org.project()
    part = _add(org, pid, screener_text=TEXT).json()
    itv = org.c.post(f"/api/projects/{pid}/interviews", headers=org.h(), json={"participant_id": part["id"], "title": "x", "moderation": "ai",
                                                                              "moderator_id": org.users["moderator"]}).json()
    t = itv["invite_token"]
    org.c.post(f"/api/public/join/{t}/device", json={"camera": True, "microphone": True, "speaker": True})
    org.c.post(f"/api/public/join/{t}/consent", json={"country": "SG", "recording": True, "observers": True, "cross_border": True, "ai": True})
    org.c.post(f"/api/interviews/{itv['id']}/start", headers=org.h("moderator"))
    org.c.post(f"/api/interviews/{itv['id']}/admit", headers=org.h("moderator"), json={"admit": True})
    org.c.post(f"/api/public/join/{t}/enter")
    assert _start(org, itv).status_code == 200
    assert ai_room._ENGINES[itv["id"]].memory.profile[0] == "多久用一次护肤品 → 每天"


def test_the_model_is_shown_the_profile_in_both_calls():
    e, model = engine([{"category": "answered", "ready_to_start": True}], [{"spoken_response": "好的，我们开始，你平时怎么选护肤品？"}])
    e.memory.profile.extend(["肤质 → 油性", "对香精的态度 → 尽量避开"])
    e.open()
    e.handle_participant("好的，可以开始")
    for call in (model.analysis_calls[-1], model.draft_calls[-1]):
        assert json.loads(call[-1]["content"])["PARTICIPANT_PROFILE"] == ["肤质 → 油性", "对香精的态度 → 尽量避开"]


def test_the_prompt_tells_the_model_how_to_use_the_profile():
    assert "PARTICIPANT_PROFILE" in PROMPTS.speak and "PARTICIPANT_PROFILE" in PROMPTS.analyze
    assert "Never ask something it already answers" in PROMPTS.speak and "reveal HYPOTHESES" in PROMPTS.speak


@pytest.mark.parametrize("lang", ["en", "zh", "ms", "ta"])
def test_the_ai_consent_says_the_screening_answers_are_read(lang):
    assert AI_PROFILE[lang] in consent_for(lang, "Acme", ai=True)["text"]
    assert AI_PROFILE[lang] not in consent_for(lang, "Acme")["text"]
