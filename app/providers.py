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
    if settings.asr_provider == "tencent":
        from .asr_file import TencentFileASR, file_asr_configured

        if not file_asr_configured():
            raise NotImplementedError("ASR_PROVIDER=tencent 但没有设置 TENCENT_ASR_SECRET_ID / TENCENT_ASR_SECRET_KEY")
        return TencentFileASR()
    raise NotImplementedError(f"ASR provider {settings.asr_provider!r} not wired yet (week-1 spike)")


class LLM(Protocol):
    def insights(self, segments: list[dict]) -> list[dict]: ...
    def translate(self, texts: list[str]) -> list[str]: ...  # into Chinese; same length and order; "" where it cannot
    def extract_claims(self, lines: list[dict]) -> list[dict]: ...  # focus group: numbered lines -> [{"speaker","topic","stance": pro|con|unsure,"reason","lines":[numbers]}]
    def classify_mentions(self, items: list[dict]) -> list[dict]: ...  # public posts: [{"n","text"}] -> [{"n","sentiment": neg|neu|pos,"topics":[short names]}]
    def answer(self, question: str, sources: list[dict]) -> dict: ...  # research library: {"claims": [{"text", "evidence": [source numbers]}], "insufficient": bool}
    def suggest_codes(self, statements: list[str], codebook: list[str]) -> list[list[str]]: ...  # per statement: 1-3 short code names (reuse the codebook where it fits)


class MockLLM:
    """Extractive stub: picks the longest participant statements. Real quotes, no real analysis."""

    def suggest_codes(self, statements: list[str], codebook: list[str]) -> list[list[str]]:
        return [[codebook[0]] if codebook else ["待命名"] for _ in statements]

    def translate(self, texts: list[str]) -> list[str]:
        return ["" for _ in texts]

    _CLAIM_TOPICS = {"香味": ("香味", "香精", "fragrance", "scent"), "价格": ("价格", "太贵", "便宜", "price"), "包装": ("包装", "packaging"), "功效": ("功效", "效果", "有效", "work")}
    _CLAIM_CON = ("不喜欢", "过敏", "讨厌", "太贵", "不放心", "不好", "难用", "dislike", "hate", "expensive", "allerg")
    _CLAIM_PRO = ("喜欢", "放心", "不错", "推荐", "便宜", "love", "great", "like")
    _CLAIM_UNSURE = ("不确定", "也许", "说不好", "maybe", "not sure")

    def extract_claims(self, lines: list[dict]) -> list[dict]:
        """Offline stand-in: keywords for the topic and the stance of each participant line. Says nothing about real reading quality."""
        out = []
        for ln in lines:
            if not (ln["speaker"].startswith("p") and ln["speaker"][1:].isdigit()):
                continue
            t = ln["text"].lower()
            topic = next((name for name, words in self._CLAIM_TOPICS.items() if any(w in t for w in words)), None)
            stance = "unsure" if any(w in t for w in self._CLAIM_UNSURE) else "con" if any(w in t for w in self._CLAIM_CON) else "pro" if any(w in t for w in self._CLAIM_PRO) else None
            if topic and stance:
                out.append({"speaker": ln["speaker"], "topic": topic, "stance": stance, "reason": ln["text"][:60], "lines": [ln["n"]]})
        return out

    _POS = ("love", "great", "good", "easy", "fair", "saved", "喜欢", "满意", "推荐", "方便", "不错", "省")
    _NEG = ("slow", "cold", "never", "disappoint", "crash", "bug", "expensive", "refund", "慢", "凉", "失望", "闪退", "贵", "差", "退款", "没回复")
    _TOPICS = {"配送": ("delivery", "slow", "cold", "配送", "物流", "送到"), "价格": ("price", "expensive", "cheap", "promotion", "saved", "价格", "贵", "便宜", "优惠"),
               "客服": ("customer service", "support", "refund", "客服", "退款"), "App 体验": ("app", "crash", "bug", "order", "闪退", "下单"), "品质": ("quality", "品质", "质量")}

    def classify_mentions(self, items: list[dict]) -> list[dict]:
        """Offline stand-in: word lists. Says nothing about real sentiment quality."""
        out = []
        for it in items:
            t = it["text"].lower()
            pos, neg = sum(w in t for w in self._POS), sum(w in t for w in self._NEG)
            out.append({"n": it["n"], "sentiment": "pos" if pos > neg else "neg" if neg > pos else "neu",
                        "topics": [name for name, words in self._TOPICS.items() if any(w in t for w in words)][:3]})
        return out

    def answer(self, question: str, sources: list[dict]) -> dict:
        """Offline stand-in: one claim per best passage, quoting its opening. No real reading comprehension."""
        return {"insufficient": not sources, "claims": [{"text": "资料里有这样的说法：" + s["text"][:50], "evidence": [s["n"]]} for s in sources[:3]]}

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

    def extract_claims(self, lines: list[dict]) -> list[dict]:
        """Ask the model for the positions people took, citing line numbers only. The reply is untrusted: app/group_analysis.py checks every claim against the transcript."""
        prompt = ("你是座谈会分析员。下面是一场座谈会的逐字稿，每行有编号、说话人（p1、p2……是受访者，moderator 是主持人）和内容。找出每位受访者对各个议题明确表达的立场："
                  "stance 只能是 pro（赞成/喜欢/支持）、con（反对/不喜欢/担心）、unsure（犹豫/不确定）；topic 用 2 到 6 个字的议题名，同一议题全场用同一个名字；reason 用一句话概括理由。"
                  "lines 只写支持这条立场的行编号（数字），不要抄写原话，行必须是该受访者本人说的。同一个人立场改变时，前后各写一条。不要推测性格或情绪。"
                  '只输出 JSON：{"claims":[{"speaker":"p1","topic":str,"stance":"pro|con|unsure","reason":str,"lines":[int,...]}]}\n\n' + json.dumps(lines, ensure_ascii=False))
        got = self._chat(prompt).get("claims", [])
        return [c for c in got if isinstance(c, dict)] if isinstance(got, list) else []

    def classify_mentions(self, items: list[dict]) -> list[dict]:
        """Ask the model for the sentiment (neg / neu / pos) and 1-3 short topic names of each public post. The reply is untrusted: callers check it."""
        prompt = ("你是舆情分析员。对下面每条公开帖子或评论，判断整体情感（neg 负面 / neu 中性 / pos 正面），并给出 1-3 个简短的话题名（每个不超过 6 个字，用帖子本身的语言或中文，"
                  "例如 配送、价格、客服、App 体验）。只输出 JSON：" + '{"results":[{"n":int,"sentiment":"neg|neu|pos","topics":[str,...]}]}\n\n' + json.dumps(items, ensure_ascii=False))
        got = self._chat(prompt).get("results", [])
        return [r for r in got if isinstance(r, dict)] if isinstance(got, list) else []

    def answer(self, question: str, sources: list[dict]) -> dict:
        """Ask the model to answer from the numbered passages only. The reply is untrusted: app/repo/ask.py checks every number and figure."""
        prompt = ("你是研究资料库的问答助手。只能根据下面编号的资料回答问题，不要使用资料之外的知识，不要编造数字。"
                  "把答案拆成几条结论，每条结论必须附上支持它的资料编号（只写数字编号，不要抄写原文）。用提问所用的语言回答。"
                  "如果资料不足以回答，返回 insufficient 为 true。只输出 JSON："
                  '{"claims":[{"text":str,"evidence":[int,...]}],"insufficient":bool}\n\n问题：' + question + "\n\n资料：\n" + json.dumps(sources, ensure_ascii=False))
        return self._chat(prompt)

    def suggest_codes(self, statements: list[str], codebook: list[str]) -> list[list[str]]:
        """Ask the model for 1-3 short code names per insight; the answer is untrusted, so it is trimmed and padded to the right length."""
        prompt = ("你是定性研究的编码员。为下面每条洞察各给 1-3 个简短的主题编码（每个不超过 8 个字）。已有编码本里合适的优先沿用，不合适再新建。"
                  '只输出 JSON：{"codes":[[str,...],...]}，外层数组长度必须恰好等于 %d，顺序一致。\n已有编码本：%s\n\n' % (len(statements), json.dumps(codebook, ensure_ascii=False))
                  + json.dumps(statements, ensure_ascii=False))
        got = self._chat(prompt).get("codes", [])
        out: list[list[str]] = []
        for i in range(len(statements)):
            row = got[i] if isinstance(got, list) and i < len(got) and isinstance(got[i], list) else []
            out.append([str(c).strip()[:60] for c in row if str(c).strip()][:3])
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
