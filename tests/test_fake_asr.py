"""The test double for Tencent's real-time speech recognition must itself follow the documented protocol, or the browser test proves nothing."""
import json

from websockets.sync.client import connect

from app import asr_live
from app.config import settings
from fake_asr import FRAME_BYTES, FakeASR


def _url(port: int) -> str:
    settings.tencent_asr_appid, settings.tencent_asr_secret_id, settings.tencent_asr_secret_key = "1", "id", "key"
    settings.asr_live_ws_base = f"ws://127.0.0.1:{port}"
    try:
        return asr_live.signed_url("zh")
    finally:
        settings.tencent_asr_appid = settings.tencent_asr_secret_id = settings.tencent_asr_secret_key = ""
        settings.asr_live_ws_base = "wss://asr.cloud.tencent.com"


def test_a_session_gets_a_partial_then_the_finished_sentence_and_the_end_message_is_answered():
    fake = FakeASR([["第一句话说完了。"]])
    port = fake.start()
    try:
        with connect(_url(port)) as ws:
            got = []
            for i in range(15):
                ws.send(bytes([1, 2]) * (FRAME_BYTES // 2))  # one 200 ms frame of non-silent PCM
                try:
                    got.append(json.loads(ws.recv(timeout=0.05)))
                except TimeoutError:
                    pass
            ws.send(json.dumps({"type": "end"}))
            got.append(json.loads(ws.recv(timeout=3)))
        results = [m["result"] for m in got if m.get("result")]
        assert [r["slice_type"] for r in results] == [1, 2] and results[-1]["voice_text_str"] == "第一句话说完了。"
        assert got[-1]["final"] == 1
        info = fake.connections[0]
        assert info["frames"] == 15 and info["bad_frames"] == 0 and info["loud_frames"] == 15 and info["ended"] is True
    finally:
        fake.stop()


def test_a_connection_without_the_documented_parameters_is_refused_and_a_short_frame_is_counted():
    fake = FakeASR([["x"]])
    port = fake.start()
    try:
        with connect(f"ws://127.0.0.1:{port}/asr/v2/1?secretid=id") as ws:
            assert json.loads(ws.recv(timeout=3))["code"] == 4001
        assert fake.connections == []
        with connect(_url(port)) as ws:
            ws.send(b"\0" * 100)
            ws.send(json.dumps({"type": "end"}))
            ws.recv(timeout=3)
        assert fake.connections[0]["bad_frames"] == 1
    finally:
        fake.stop()
