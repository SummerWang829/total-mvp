"""A stand-in for Tencent Cloud real-time speech recognition, speaking the same WebSocket protocol, for tests/e2e_room.py.

What it checks about the browser (the real thing would answer with an error): every query parameter the doc requires is present, audio
arrives as binary messages of exactly 200 ms of 16 kHz 16-bit mono PCM (6400 bytes), and the browser ends the session with {"type":"end"}.
What it answers: per connection a scripted conversation - a partial result (slice_type 1) that stays for a couple of seconds, then the
finished sentence (slice_type 2) - plus {"final":1} after the end message. The recogniser cannot tell speakers apart on its own
(neither can the real one: each browser captions its own microphone), so scripts are handed out in connection order.
"""
import asyncio
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import serve

REQUIRED = {"secretid", "timestamp", "expired", "nonce", "engine_model_type", "voice_id", "voice_format", "signature"}
FRAME_BYTES = 6400
SPEED = 5  # a result is due every SPEED frames (1 s) of audio


class FakeASR:
    def __init__(self, scripts: list[list[str]]):
        self.scripts = scripts
        self.connections: list[dict] = []  # one entry per accepted connection (in order): the query, frame counts, whether the end message came
        self.base = 0  # connections before this index belong to earlier test phases; scripts are handed out from here
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self.port = 0

    def start(self) -> int:
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(10)
        return self.port

    def mark(self) -> list[dict]:
        """Start a new test phase: the next connection gets the first script again. Returns the live list of this phase's connections."""
        self.base = len(self.connections)
        return self.phase

    @property
    def phase(self) -> list[dict]:
        return self.connections[self.base:]

    def stop(self) -> None:
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)  # lets boot() leave its `async with`, closing the server and every connection cleanly

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def boot():
            self._stop = asyncio.Event()
            async with serve(self._handle, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                self._ready.set()
                await self._stop.wait()

        self._loop.run_until_complete(boot())

    async def _handle(self, ws) -> None:
        split = urlsplit(ws.request.path)
        q = {k: v[0] for k, v in parse_qs(split.query).items()}
        info = {"path": split.path, "query": q, "frames": 0, "loud_frames": 0, "bad_frames": 0, "ended": False, "sent": [], "at": time.time()}
        if not REQUIRED <= set(q):
            await ws.send(json.dumps({"code": 4001, "message": f"missing {sorted(REQUIRED - set(q))}", "voice_id": q.get("voice_id", "")}))
            await ws.close()
            return
        script = self.scripts[(len(self.connections) - self.base) % len(self.scripts)]
        self.connections.append(info)
        index = 0
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    info["frames"] += 1
                    info["bad_frames"] += len(msg) != FRAME_BYTES
                    info["loud_frames"] += any(msg[i] or msg[i + 1] for i in range(0, min(len(msg), 400), 2))  # real samples, not digital silence
                    n = info["frames"]
                    if index < len(script) and n % (SPEED * 3) == SPEED:  # the sentence begins: first half, still being recognised
                        await self._send(ws, info, q, 1, index, script[index][: max(2, len(script[index]) // 2)])
                    elif index < len(script) and n % (SPEED * 3) == SPEED * 3 - 1:  # three seconds later the sentence is finished
                        await self._send(ws, info, q, 2, index, script[index])
                        index += 1
                else:
                    if json.loads(msg).get("type") == "end":
                        info["ended"] = True
                        await ws.send(json.dumps({"code": 0, "message": "success", "voice_id": q["voice_id"], "message_id": "end", "final": 1}))
                        await ws.close()
                        return
        except Exception:  # noqa: BLE001 - a browser that vanishes mid-stream is an ordinary event for a test double
            return

    async def _send(self, ws, info, q, slice_type: int, index: int, text: str) -> None:
        info["sent"].append((slice_type, text))
        await ws.send(json.dumps({"code": 0, "message": "success", "voice_id": q["voice_id"], "message_id": f"{index}-{slice_type}",
                                  "result": {"slice_type": slice_type, "index": index, "start_time": index * 3000, "end_time": index * 3000 + 2500,
                                             "voice_text_str": text, "word_size": 0, "word_list": []}}))
