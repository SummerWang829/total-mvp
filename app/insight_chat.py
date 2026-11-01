"""The research assistant of the 洞察与片段 page: a real conversation with the configured chat model about one project's interviews.

What it may say is split in three, so the team can always tell evidence from opinion:
  * the answer: an analysis that points at numbered passages ([1], [2]); only numbers that were really given to the model survive;
  * the quotes: the cited passages, copied word for word by the server (the model never writes them) with who said it and when;
  * suggestions and follow-up questions: the model's own judgement, shown apart and marked as such.
Only passages of this project that the person may open are ever sent to the model (app/repo/search.py applies the permission first). The question, the
research background and those passages go to the AI service; participants appear only as codes.
"""
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import participant_code, assistant_identity
from .chat_models import ChatThread
from .deps import Ctx
from .models import Insight, Interview, Project, RepoDocument, TranscriptSegment
from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider
from .repo import search
from .repo.documents import pieces

log = logging.getLogger(__name__)

TOP_K = 12
MIN_PASSAGES = 4  # fewer than this found by words: add the approved insights so a broad question ("what are the key findings") still has material
MAX_HISTORY = 8  # earlier messages sent along
MAX_THREADS = 30
MAX_SUGGESTIONS = 5
MAX_FOLLOWUPS = 3
MAX_ANSWER = 3000
SCOPES = {"transcripts": "segment", "insights": "insight", "library": "document"}
MSG_NO_ANSWER = "AI 没有给出可用的回答，请换个问法再试一次"
MSG_MOCK = "（演示模式：没有连接 AI 模型，下面只是按关键词找到的相关原话。）"
MSG_NONE_FOUND = "（演示模式）资料里没有找到相关内容。先完成访谈、转写并确认洞察后再来问。"

SYSTEM = """你是市场研究团队的研究助手，帮团队理解“这一个项目”的深度访谈资料。你会收到：项目的研究背景、访谈大纲（可能为空）、编号的资料片段（受访者的原话、已确认的洞察、研究库资料）、之前的对话，和团队的新问题。
只输出一个 JSON 对象，不要输出别的：
{"answer": "<回答：用中文，直接回答问题，说清楚有多少人、什么情况；每个依据资料的判断后面标上编号，如 [1][3]>",
 "suggestions": ["<你的建议：研究上下一步怎么做，每条一句话；没有就给空数组>"],
 "followups": ["<团队接下来可能想问的问题，最多 3 个，每个 20 字以内>"]}
规则：
1. 事实、数字、人数只能来自给你的片段，编号必须是片段里真有的；资料不够就直说“资料里看不出来”，不要编造；不要把没有依据的猜测写进 answer。
2. answer 里不要大段照抄原话（原话会由系统单独列出），用自己的话概括。
3. 建议可以超出资料，但要放在 suggestions 里，是建议不是事实；建议要具体，能直接用于访谈大纲、招募或下一轮研究。
4. 受访者只用编号称呼（如 M-002），不要猜测或询问姓名、电话、邮箱等个人信息。
5. 回答要简洁，先结论后细节。"""
SYSTEM += " " + assistant_identity.PROMPT_LINE   # 小星星助手: never name the model or vendor (user 2026-11-01)


class ChatUnavailable(Exception):
    """The model could not be used or gave nothing usable; `message` is plain Chinese for the team."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def load(raw: str) -> list[dict]:
    """The saved messages of a conversation (tolerates an empty or damaged value)."""
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [m for m in data if isinstance(m, dict) and m.get("role") in ("user", "ai")]


def _passages(db: Session, ctx: Ctx, project_id: str, question: str, history: list[dict], scope: list[str]) -> list[search.Source]:
    types = tuple(SCOPES[s] for s in scope if s in SCOPES) or tuple(SCOPES.values())
    pool = search.candidates(db, ctx, project_id, types)
    last_user = [m["text"] for m in history if m["role"] == "user"][-1:]
    top = search.rank(" ".join([question, *last_user]), pool, TOP_K)
    if len(top) < MIN_PASSAGES:
        have = {s.ref for s in top}
        extra = [s for s in pool if s.type == "insight" and s.ref not in have][: TOP_K - len(top)]
        top += extra
    return top


def _prompt(db: Session, project: Project, question: str, history: list[dict], top: list[search.Source], outline_text: str) -> list[dict]:
    numbered = "\n".join(f"[{i}] ({'受访者原话' if s.type == 'segment' else '已确认的洞察' if s.type == 'insight' else '研究库资料'}{'，' + s.who if s.who else ''}) {s.text[:700]}" for i, s in enumerate(top, 1))
    ctx = f"项目：{project.name}\n研究背景：{(project.brief or '（没有写）')[:800]}\n访谈大纲：{(outline_text or '（还没有写）')[:800]}\n\n资料片段：\n{numbered or '（目前没有可用的资料）'}"
    msgs: list[dict] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": ctx}, {"role": "assistant", "content": '{"answer": "好的，我已读完这些资料。", "suggestions": [], "followups": []}'}]
    for m in history[-MAX_HISTORY:]:
        msgs.append({"role": "user", "content": m["text"]} if m["role"] == "user" else {"role": "assistant", "content": json.dumps({"answer": m.get("answer", ""), "suggestions": m.get("suggestions", []), "followups": []}, ensure_ascii=False)})
    msgs.append({"role": "user", "content": f"新问题：{question}"})
    return msgs


def _clean_list(raw, limit: int, size: int) -> list[str]:
    return [str(x).strip()[:size] for x in raw if str(x).strip()][:limit] if isinstance(raw, list) else []


def _mock_reply(question: str, top: list[search.Source]) -> dict:
    if not top:
        return {"answer": MSG_NONE_FOUND, "suggestions": [], "followups": []}
    cites = "".join(f"[{i}]" for i in range(1, min(3, len(top)) + 1))
    return {"answer": f"{MSG_MOCK}和你的问题最相关的资料见 {cites}。", "suggestions": ["接入 AI 模型后，这里会给出具体的研究建议。"], "followups": ["这些原话能做成集锦吗？"]}


def reply(db: Session, ctx: Ctx, project: Project, thread: ChatThread, question: str, scope: list[str], chat: ModeratorChatProvider | None, outline_text: str) -> dict:
    """Answer one question in a conversation and return the assistant message to store (quotes are addresses only).

    Raises:
        ChatUnavailable: no usable answer after the model call.
        ChatFatalError: the model service refused (bad key, no balance...); not hidden behind a made-up answer.
    """
    history = load(thread.messages)
    top = _passages(db, ctx, project.id, question, history, scope)
    try:
        raw = _mock_reply(question, top) if chat is None else chat.complete(_prompt(db, project, question, history, top, outline_text), temperature=0.3)
    except ChatFatalError:
        raise
    except ChatError as e:
        log.warning("insight chat model call failed: %s", type(e).__name__)
        raise ChatUnavailable("AI 暂时无法回答，请稍后再试") from e
    if not isinstance(raw, dict):
        raise ChatUnavailable(MSG_NO_ANSWER)
    text = str(raw.get("answer", "")).strip()[:MAX_ANSWER]
    if not text:
        raise ChatUnavailable(MSG_NO_ANSWER)
    used: list[int] = []
    for n in (int(x) for x in re.findall(r"\[(\d+)\]", text)):
        if 1 <= n <= len(top):
            if n not in used:
                used.append(n)
    text = re.sub(r"\[(\d+)\]", lambda m: m.group(0) if 1 <= int(m.group(1)) <= len(top) else "", text)  # an invented number is dropped
    return {"role": "ai", "answer": text, "suggestions": _clean_list(raw.get("suggestions"), MAX_SUGGESTIONS, 300), "followups": _clean_list(raw.get("followups"), MAX_FOLLOWUPS, 40),
            "refs": [{"n": n, "ref": top[n - 1].ref} for n in used]}


def resolve(db: Session, ctx: Ctx, project_id: str, refs: list[dict]) -> list[dict]:
    """The quotes behind an answer, looked up again now: [{n, text, who, at_ms, interview_id, type, title}]. A passage that is gone (purged) is left out."""
    codes = participant_code.code_map(db, project_id)
    out = []
    for r in refs:
        kind, _, rest = str(r.get("ref", "")).partition(":")
        item = None
        if kind == "segment":
            row = db.execute(select(TranscriptSegment, Interview).join(Interview, Interview.id == TranscriptSegment.interview_id)
                             .where(TranscriptSegment.id == rest, Interview.org_id == ctx.org_id, Interview.project_id == project_id, Interview.purged_at.is_(None))).first()
            if row:
                seg, itv = row
                item = {"text": seg.text_orig, "who": codes.get(itv.participant_id, ""), "at_ms": seg.start_ms, "interview_id": itv.id, "type": "segment", "title": itv.title}
        elif kind == "insight":
            row = db.execute(select(Insight, Interview).join(Interview, Interview.id == Insight.interview_id)
                             .where(Insight.id == rest, Insight.status == "approved", Interview.org_id == ctx.org_id, Interview.project_id == project_id, Interview.purged_at.is_(None))).first()
            if row:
                ins, itv = row
                item = {"text": f"{ins.theme}：{ins.statement}", "who": codes.get(itv.participant_id, ""), "at_ms": 0, "interview_id": itv.id, "type": "insight", "title": itv.title}
        elif kind == "document":
            doc_id, _, n = rest.partition(":")
            doc = db.get(RepoDocument, doc_id)
            if doc and doc.org_id == ctx.org_id and doc.project_id == project_id and n.isdigit():
                ps = pieces(doc.text)
                if int(n) < len(ps):
                    item = {"text": ps[int(n)], "who": "", "at_ms": 0, "interview_id": None, "type": "document", "title": doc.title}
        if item:
            out.append({"n": r["n"], **item})
    return out


def message_out(db: Session, ctx: Ctx, project_id: str, m: dict) -> dict:
    """A stored message as the page shows it: an assistant message gets its quotes looked up."""
    if m["role"] == "user":
        return {"role": "user", "text": m.get("text", "")}
    return {"role": "ai", "answer": m.get("answer", ""), "suggestions": m.get("suggestions", []), "followups": m.get("followups", []), "quotes": resolve(db, ctx, project_id, m.get("refs", []))}
