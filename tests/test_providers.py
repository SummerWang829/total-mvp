"""OpenAI-compatible providers: timeouts come from settings (a CPU-only local server is slow); when a model merges lines in a
translation batch, the batch is redone line by line, so a translation never lands on the wrong sentence."""
import json

import pytest

from app import providers
from app.config import settings


def test_asr_timeout_is_configurable(monkeypatch, tmp_path):
    seen = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"language": "english", "segments": [{"start": 0.0, "end": 1.5, "text": " Hello there."}]}

    def fake_post(url, **k):
        seen["url"], seen["timeout"] = url, k["timeout"]
        return R()

    monkeypatch.setattr(providers.httpx, "post", fake_post)
    monkeypatch.setattr(settings, "asr_timeout_s", 3600)
    monkeypatch.setattr(settings, "asr_base_url", "http://127.0.0.1:8178/v1")
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x" * 100)
    out = providers.OpenAICompatASR().transcribe_audio(str(audio), "en")
    assert seen == {"url": "http://127.0.0.1:8178/v1/audio/transcriptions", "timeout": 3600}
    assert out == [{"start_ms": 0, "end_ms": 1500, "text": "Hello there.", "lang": "english"}]


def _llm_answers(monkeypatch, answer):
    seen = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(answer(seen["src"]))}}]}

    def fake_post(url, **k):
        body = k["json"]["messages"][0]["content"].split("\n\n", 1)[1]
        try:
            seen["src"] = json.loads(body)  # translate sends a JSON array
        except ValueError:
            seen["src"] = body  # insights sends transcript lines
        seen["timeout"] = k["timeout"]
        return R()

    monkeypatch.setattr(providers.httpx, "post", fake_post)
    return seen


ZH = {"I first look at reviews": "我先看评价", "on Shopee.": "在虾皮上。", "Friends recommend too.": "朋友也推荐。"}


def test_translation_merged_batch_retries_line_by_line(monkeypatch):
    # in a batch the model merges lines 1+2 (2 answers for 3 lines); single lines are answered properly
    def answer(src):
        return {"translations": ["我先看评价，在虾皮上。", "朋友也推荐。"] if len(src) > 1 else [ZH[src[0]]]}

    _llm_answers(monkeypatch, answer)
    assert providers.OpenAICompatLLM().translate(list(ZH)) == ["我先看评价", "在虾皮上。", "朋友也推荐。"]


@pytest.mark.parametrize("single, expect", [(lambda s: [ZH[s]], list(ZH.values())), (lambda s: [], ["", "", ""])])
def test_translation_never_misaligns(monkeypatch, single, expect):
    _llm_answers(monkeypatch, lambda src: {"translations": ["合并"] if len(src) > 1 else single(src[0])})
    assert providers.OpenAICompatLLM().translate(list(ZH)) == expect


def test_complete_batch_is_one_call(monkeypatch):
    calls = []
    _llm_answers(monkeypatch, lambda src: calls.append(src) or {"translations": [ZH[t] for t in src]})
    assert providers.OpenAICompatLLM().translate(list(ZH)) == list(ZH.values()) and len(calls) == 1


def test_llm_timeout_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "llm_timeout_s", 1800)
    seen = _llm_answers(monkeypatch, lambda src: {"translations": ["译"] * len(src), "insights": []})
    providers.OpenAICompatLLM().translate(["x"])
    assert seen["timeout"] == 1800
    seen.clear()
    providers.OpenAICompatLLM().insights([{"id": "s1", "speaker": "participant", "text_orig": "x", "text_zh": ""}])
    assert seen["timeout"] == 1800


SEGS = [{"id": "seg-a", "speaker": "moderator", "text_orig": "Where do you buy?", "text_zh": "你在哪里买？"},
        {"id": "seg-b", "speaker": "participant", "text_orig": "I first look at reviews on Shopee.", "text_zh": "我先看虾皮上的评价。"},
        {"id": "seg-c", "speaker": "participant", "text_orig": "Price matters a lot to me.", "text_zh": "价格对我很重要。"}]


def test_insight_evidence_is_line_numbers_filled_with_the_real_line(monkeypatch):
    # decision 2026-10-10: the model only cites line numbers; the quote is the cited line's own text, never model-written
    seen = _llm_answers(monkeypatch, lambda src: {"insights": [
        {"theme": "评价", "statement": "先看评价", "evidence": [2, "[3]", {"line": 2}]},
        {"theme": "编造", "statement": "没有有效证据", "evidence": [0, -1, 9, "abc"]}]})
    out = providers.OpenAICompatLLM().insights(SEGS)
    assert "[2] participant: I first look at reviews on Shopee." in seen["src"] and "seg-b" not in seen["src"]
    assert out[0]["evidence"] == [
        {"segment_id": "seg-b", "quote": "I first look at reviews on Shopee.", "lang": "orig"},
        {"segment_id": "seg-c", "quote": "Price matters a lot to me.", "lang": "orig"}]  # line 2 cited twice -> once
    assert out[1]["evidence"] == []  # 0, negative, out-of-range and non-numbers never map to a line
