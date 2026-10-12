"""Pluggable ASR / LLM. `mock` works offline for development; real providers are the week-1 spikes."""
import json
import os
from typing import Protocol

import httpx

from .config import settings


class ASR(Protocol):
    def transcribe_audio(self, path: str, language: str, role: str = "") -> list[dict]: ...  # [{start_ms,end_ms,text,lang,(text_zh)}]


_SAMPLE = [
            ("moderator", "Could you tell me how you usually buy skincare products?", "能说说你平时是怎么买护肤品的吗？"),
            ("participant", "I usually watch reviews on TikTok first, then compare prices on Shopee and Lazada before I decide.", "我通常先在 TikTok 上看评价，再去 Shopee 和 Lazada 比价，然后才决定。"),
            ("moderator", "What makes you hesitate to try a new brand from overseas?", "买来自海外的新品牌，最让你犹豫的是什么？"),
            ("participant", "I worry it might be fake, and I want clear halal certification and a proper return policy.", "我担心是假货，而且我要看到清楚的清真认证和正规的退货政策。"),
            ("participant", "If delivery is fast and returns are easy, I'm willing to try, but the price must make sense.", "如果能快速送达、退货方便，我愿意试试，不过价格要合理。"),
            ("moderator", "How important is the packaging to you?", "包装对你有多重要？"),
            ("participant", "Neat packaging feels safe, especially when I'm buying it as a gift.", "整洁的包装让人觉得安全，尤其是送礼的时候。"),
]


def _sample_rows() -> list[dict]:
    out, t = [], 0
    for i, (spk, orig, zh) in enumerate(_SAMPLE):
        d = 4000 + 150 * len(orig)
        out.append(dict(idx=i, speaker=spk, start_ms=t, end_ms=t + d, text_orig=orig, text_zh=zh, lang="en"))
        t += d + 600
    return out


class MockASR:
    """Fixed sample conversation so the rest of the pipeline can be built and tested (no audio is actually listened to)."""

    def transcribe(self, storage_key: str, language: str) -> list[dict]:  # whole conversation, used when there is no recording file at all
        return _sample_rows()

    def transcribe_audio(self, path: str, language: str, role: str = "") -> list[dict]:  # one speaker's lines, so per-track mixing is testable
        return [dict(start_ms=r["start_ms"], end_ms=r["end_ms"], text=r["text_orig"], text_zh=r["text_zh"], lang="en") for r in _sample_rows() if r["speaker"] == role]


class OpenAICompatASR:
    """OpenAI-compatible /audio/transcriptions (OpenAI whisper-1, or any server that returns verbose_json segments)."""

    LANG = {"en": "en", "zh": "zh", "ms": "ms", "ta": "ta"}
    PIECE_S = 20 * 60

    def _post(self, path: str, language: str) -> dict:
        with open(path, "rb") as f:
            data = {"model": settings.asr_model, "response_format": "verbose_json", "timestamp_granularities[]": "segment"}
            if self.LANG.get(language):
                data["language"] = self.LANG[language]
            r = httpx.post(settings.asr_base_url.rstrip("/") + "/audio/transcriptions", headers={"Authorization": f"Bearer {settings.asr_api_key}"},
                           data=data, files={"file": (os.path.basename(path), f, "audio/mpeg")}, timeout=settings.asr_timeout_s)
        r.raise_for_status()
        return r.json()

    def transcribe_audio(self, path: str, language: str, role: str = "") -> list[dict]:
        from .recording import run

        pieces = [(path, 0)]
        if os.path.getsize(path) > 20 * 1024 * 1024:  # provider upload limit is ~25 MB: cut into 20-minute pieces
            base = path.rsplit(".", 1)[0] + ".piece%03d.mp3"
            run([settings.ffmpeg, "-y", "-v", "error", "-i", path, "-f", "segment", "-segment_time", str(self.PIECE_S), "-c", "copy", base], timeout=600)
            pieces = [(base % i, i * self.PIECE_S * 1000) for i in range(1000) if os.path.isfile(base % i)]
        out = []
        for p, off in pieces:
            j = self._post(p, language)
            segs = j.get("segments") or ([{"start": 0, "end": 0, "text": j.get("text", "")}] if j.get("text") else [])
            for sg in segs:
                text = (sg.get("text") or "").strip()
                if not text or (sg.get("no_speech_prob", 0) > 0.6 and sg.get("avg_logprob", 0) < -1.0):  # silence hallucinations
                    continue
                out.append(dict(start_ms=off + int(sg["start"] * 1000), end_ms=off + max(int(sg["end"] * 1000), int(sg["start"] * 1000) + 500), text=text, lang=j.get("language", "")[:10]))
        return out


def get_asr() -> ASR:
    if settings.asr_provider == "mock":
        return MockASR()
    if settings.asr_provider == "openai":
        if not settings.asr_api_key:
            raise NotImplementedError("ASR_PROVIDER=openai 但没有设置 ASR_API_KEY（或 LLM_API_KEY）")
        return OpenAICompatASR()
    raise NotImplementedError(f"ASR provider {settings.asr_provider!r} not wired yet (week-1 spike)")


class LLM(Protocol):
    def insights(self, segments: list[dict]) -> list[dict]: ...
    def translate(self, texts: list[str]) -> list[str]: ...  # into Chinese; same length and order; "" where it cannot


class MockLLM:
    """Extractive stub: picks the longest participant statements. Real quotes, no real analysis."""

    def translate(self, texts: list[str]) -> list[str]:
        return ["" for _ in texts]

    def insights(self, segments: list[dict]) -> list[dict]:
        part = sorted((s for s in segments if s["speaker"] == "participant"), key=lambda s: -len(s["text_zh"] or s["text_orig"]))[:3]
        return [
            {"theme": "待命名主题", "statement": "受访者表示：" + (s["text_zh"] or s["text_orig"])[:60],
             "evidence": [{"segment_id": s["id"], "quote": s["text_zh"] or s["text_orig"], "lang": "zh" if s["text_zh"] else "orig"}]}
            for s in part
        ]


class OpenAICompatLLM:
    """Any OpenAI-compatible chat endpoint. Output is untrusted: app.evidence checks every quote afterwards."""

    def _chat(self, prompt: str) -> dict:
        r = httpx.post(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={"model": settings.llm_model, "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2},
            timeout=settings.llm_timeout_s,
        )
        r.raise_for_status()
        return json.loads(r.json()["choices"][0]["message"]["content"])

    def _translate_batch(self, batch: list[str]) -> list[str] | None:
        prompt = ("把下面访谈逐字稿的每一句翻译成自然、准确的简体中文，保留品牌名和平台名原文。只输出 JSON："
                  '{"translations":[str,...]}，数组长度必须恰好等于 %d，顺序一致。\n\n' % len(batch)) + json.dumps(batch, ensure_ascii=False)
        try:
            got = self._chat(prompt).get("translations", [])
        except Exception:
            return None
        return [str(x) for x in got] if isinstance(got, list) and len(got) == len(batch) else None

    def translate(self, texts: list[str]) -> list[str]:
        out: list[str] = []
        for i in range(0, len(texts), 25):
            batch = texts[i:i + 25]
            got = self._translate_batch(batch)
            if got is None:  # length mismatch (small local models merge half-sentences): retry line by line so nothing misaligns
                got = [(self._translate_batch([t]) or [""])[0] for t in batch]
            out += got
        return out

    def insights(self, segments: list[dict]) -> list[dict]:
        # Evidence = line numbers only (decision 2026-10-10): models paraphrase when asked to copy quotes, so we fill in each
        # cited line's full text ourselves. app.evidence still checks it against the transcript before anything is saved.
        lines = "\n".join(f'[{n}] {s["speaker"]}: {s["text_orig"]} || {s["text_zh"]}' for n, s in enumerate(segments, 1))
        prompt = (
            "你是市场研究分析师。根据访谈逐字稿提炼 3-6 条洞察。每条必须附 1-3 条证据，证据只写支持它的句子编号（方括号里的数字），"
            "不要抄写或改写原文。只输出 JSON：{\"insights\":[{\"theme\":str,\"statement\":str,\"evidence\":[int,...]}]}\n\n" + lines
        )
        r = httpx.post(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={"model": settings.llm_model, "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2},
            timeout=settings.llm_timeout_s,
        )
        r.raise_for_status()
        out = []
        for ins in json.loads(r.json()["choices"][0]["message"]["content"]).get("insights", []):
            ev = []
            for ref in ins.get("evidence", []) if isinstance(ins, dict) else []:
                if isinstance(ref, dict):  # tolerate {"line": n} / {"segment": n} shapes
                    ref = next((v for v in ref.values() if isinstance(v, (int, str))), None)
                try:
                    n = int(str(ref).strip("[] "))
                except ValueError:
                    continue
                if not 1 <= n <= len(segments):
                    continue  # unknown line number: dropped here, insight survives only with valid evidence
                s = segments[n - 1]
                if any(e["segment_id"] == s["id"] for e in ev):
                    continue  # the same line cited twice is one piece of evidence
                ev.append({"segment_id": s["id"], "quote": s["text_orig"], "lang": "orig"})
            if isinstance(ins, dict):
                out.append({**ins, "evidence": ev})
        return out


def get_llm() -> LLM:
    return OpenAICompatLLM() if settings.llm_provider == "openai" else MockLLM()
