"""Tencent recorded-file recognition (ASR_PROVIDER=tencent): signing, submit + poll, result parsing, errors, unsupported languages, long recordings."""
import hashlib
import hmac
import json

import pytest

from app import asr_file, providers
from app.config import settings


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(settings, "tencent_asr_secret_id", "AKIDexample")
    monkeypatch.setattr(settings, "tencent_asr_secret_key", "secret-key-example")
    monkeypatch.setattr(asr_file, "POLL_S", 0)


def test_the_signature_follows_the_documented_tc3_algorithm_step_by_step():
    payload = '{"TaskId":1}'
    h = asr_file.tc3_headers("DescribeTaskStatus", payload, "AKIDexample", "secret-key-example", now=1_700_000_000)  # 2023-11-14 UTC
    date = "2023-11-14"
    canonical = ("POST\n/\n\ncontent-type:application/json; charset=utf-8\nhost:asr.tencentcloudapi.com\nx-tc-action:describetaskstatus\n\n"
                 "content-type;host;x-tc-action\n" + hashlib.sha256(payload.encode()).hexdigest())
    to_sign = f"TC3-HMAC-SHA256\n1700000000\n{date}/asr/tc3_request\n" + hashlib.sha256(canonical.encode()).hexdigest()
    k = hmac.new(b"TC3secret-key-example", date.encode(), hashlib.sha256).digest()
    k = hmac.new(k, b"asr", hashlib.sha256).digest()
    k = hmac.new(k, b"tc3_request", hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    assert h["Authorization"] == f"TC3-HMAC-SHA256 Credential=AKIDexample/{date}/asr/tc3_request, SignedHeaders=content-type;host;x-tc-action, Signature={sig}"
    assert h["X-TC-Action"] == "DescribeTaskStatus" and h["X-TC-Version"] == "2019-06-14" and h["X-TC-Timestamp"] == "1700000000" and h["Host"] == "asr.tencentcloudapi.com"


def test_a_secret_key_never_appears_in_the_headers():
    h = asr_file.tc3_headers("CreateRecTask", "{}", "AKIDexample", "secret-key-example")
    assert "secret-key-example" not in json.dumps(h)


def _fake_service(monkeypatch, statuses, detail=None, result=""):
    """Replaces the network call: CreateRecTask -> task 42, DescribeTaskStatus walks through `statuses`."""
    calls = []
    left = list(statuses)

    def fake(action, body, client=None):
        calls.append((action, body))
        if action == "CreateRecTask":
            return {"Data": {"TaskId": 42}}
        st = left.pop(0)
        return {"Data": {"TaskId": 42, "Status": st, "ErrorMsg": "Failed to download audio file!" if st == 3 else "", "ResultDetail": detail if st == 2 else None, "Result": result if st == 2 else ""}}

    monkeypatch.setattr(asr_file, "call", fake)
    return calls


def test_it_submits_the_audio_polls_until_done_and_returns_sentences(keys, monkeypatch, tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"\x00" * 1000)
    calls = _fake_service(monkeypatch, [0, 1, 2], detail=[{"FinalSentence": "我平时会先看成分。", "StartMs": 380, "EndMs": 3140}, {"FinalSentence": " ", "StartMs": 4000, "EndMs": 4100},
                                                          {"FinalSentence": "再看价格。", "StartMs": 3140, "EndMs": 4600}])
    out = asr_file.TencentFileASR().transcribe_audio(str(f), "zh", "participant")
    assert [(r["start_ms"], r["end_ms"], r["text"]) for r in out] == [(380, 3140, "我平时会先看成分。"), (3140, 4600, "再看价格。")]  # a blank sentence is dropped
    create = calls[0][1]
    assert calls[0][0] == "CreateRecTask" and create["EngineModelType"] == "16k_zh" and create["ChannelNum"] == 1 and create["SourceType"] == 1 and create["DataLen"] == 1000
    assert [c[0] for c in calls[1:]] == ["DescribeTaskStatus"] * 3 and all(c[1] == {"TaskId": 42} for c in calls[1:])


def test_english_uses_the_english_engine(keys, monkeypatch, tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"\x00" * 10)
    calls = _fake_service(monkeypatch, [2], detail=[{"FinalSentence": "Hello.", "StartMs": 0, "EndMs": 900}])
    asr_file.TencentFileASR().transcribe_audio(str(f), "en")
    assert calls[0][1]["EngineModelType"] == "16k_en"


def test_the_timestamped_result_text_is_used_when_there_is_no_detail():
    rows = asr_file.parse_result({"Result": "[0:0.020,0:2.380]  你好。\n[1:03.500,1:05.000]  再见。\n[1:06.000,1:07.000]  "})
    assert [(r["start_ms"], r["end_ms"], r["text"]) for r in rows] == [(20, 2380, "你好。"), (63500, 65000, "再见。")]


def test_a_failed_task_and_a_service_error_become_readable_messages(keys, monkeypatch, tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"\x00" * 10)
    _fake_service(monkeypatch, [3])
    with pytest.raises(asr_file.ASRError, match="Failed to download audio file"):
        asr_file.TencentFileASR().transcribe_audio(str(f), "zh")
    def boom(action, body, client=None):
        raise asr_file.ASRError("腾讯云语音识别返回错误 AuthFailure.SignatureFailure：签名失败")
    monkeypatch.setattr(asr_file, "call", boom)
    with pytest.raises(asr_file.ASRError, match="AuthFailure"):
        asr_file.TencentFileASR().transcribe_audio(str(f), "zh")


@pytest.mark.parametrize("lang", ["ms", "ta"])
def test_malay_and_tamil_are_refused_with_a_plain_message_not_a_wrong_transcript(keys, tmp_path, lang):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"\x00")
    with pytest.raises(asr_file.ASRUnsupported, match="只支持中文和英文"):
        asr_file.TencentFileASR().transcribe_audio(str(f), lang)


def test_a_long_track_is_cut_into_pieces_and_each_piece_is_shifted_onto_the_timeline(keys, monkeypatch, tmp_path):
    f = tmp_path / "long.mp3"
    f.write_bytes(b"\x00" * (3 * 1024 * 1024 + 1))

    def fake_run(cmd, timeout=600):  # stands in for ffmpeg: writes two pieces next to the track
        base = cmd[-1]
        for i in range(2):
            open(base % i, "wb").write(b"\x00" * 100)

    monkeypatch.setattr("app.recording.run", fake_run)
    seen = []
    monkeypatch.setattr(asr_file.TencentFileASR, "_recognise", lambda self, p, engine: (seen.append(p), [dict(start_ms=1000, end_ms=2000, text="第" + str(len(seen)) + "段")])[1])
    out = asr_file.TencentFileASR().transcribe_audio(str(f), "zh")
    assert [(r["start_ms"], r["text"]) for r in out] == [(1000, "第1段"), (1000 + asr_file.PIECE_S * 1000, "第2段")] and len(seen) == 2


def test_the_provider_is_wired_and_needs_its_keys(monkeypatch):
    monkeypatch.setattr(settings, "asr_provider", "tencent")
    monkeypatch.setattr(settings, "tencent_asr_secret_id", "")
    monkeypatch.setattr(settings, "tencent_asr_secret_key", "")
    with pytest.raises(NotImplementedError, match="TENCENT_ASR_SECRET"):
        providers.get_asr()
    monkeypatch.setattr(settings, "tencent_asr_secret_id", "id")
    monkeypatch.setattr(settings, "tencent_asr_secret_key", "key")
    assert isinstance(providers.get_asr(), asr_file.TencentFileASR)
