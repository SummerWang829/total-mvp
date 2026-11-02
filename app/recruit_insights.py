"""The numbers behind the recruiting dashboard (配额与受访者名单 page): how many people started, qualified and were screened out, where they came from, which question
stopped most people, and how the people who qualified differ from the ones who did not.

Everything about people who were screened out is an anonymous tally (how many chose each option of each question); no one's answers are kept as a record.
"""
import json
import logging
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import recruit, recruit_form
from .models import Interview, Participant, Project, RecruitForm, RecruitSettings, ScreenerAnswer
from .moderator.providers import ChatError, ChatFatalError, ModeratorChatProvider

log = logging.getLogger(__name__)

SOURCE_LABELS = {"link": "问卷链接 / 二维码", "import": "外部名单", "manual": "手动添加", "platform": "外部问卷平台", "pool": "受访者库圈人", "": "未记录来源"}
SIMILAR_QUALIFIED = 0.6  # an option chosen by at least this share of the people who qualified is a shared trait
SIMILAR_OUT = 0.5
MAX_COMPARE = 8
MAX_TRAITS = 6
SUMMARY_SYSTEM = """You help a market research team read the recruiting results of one study. You get anonymous statistics only (shares of people who chose each option, among those who
qualified and those who were screened out). Reply with ONE JSON object: {"summary": "<5 to 8 short sentences in Chinese: what the people who qualified have in common, how they differ from the ones
screened out, which question is the strictest, and where to aim the next round of recruiting; plain words, no jargon; do not invent numbers that are not given>"}"""


def empty_tally() -> dict:
    return {"q": {}, "o": {}, "num": {}, "nq": 0, "no": 0}


def record(tally: dict, which: str, questions: list[recruit_form.Question], chosen: dict) -> dict:
    """Add one person's answers to the anonymous tally of the people who qualified ("q") or were screened out ("o"). Returns the tally.

    `chosen` maps a question id to the chosen option id(s) or the number; nothing that identifies the person is kept.
    """
    t = {**empty_tally(), **(tally or {})}
    t["nq" if which == "q" else "no"] = t.get("nq" if which == "q" else "no", 0) + 1
    for q in questions:
        if q.id not in chosen or chosen[q.id] is None:
            continue
        ans = chosen[q.id]
        if q.type in ("single", "multi"):
            per = t[which].setdefault(q.id, {})
            per["_n"] = per.get("_n", 0) + 1  # how many people answered this question
            for oid in ([ans] if q.type == "single" else ans):
                per[oid] = per.get(oid, 0) + 1
        elif q.type == "number":
            s = t["num"].setdefault(which + ":" + q.id, [0.0, 0])
            s[0] += float(ans)
            s[1] += 1
    return t


def _share(tally: dict, which: str, qid: str, oid: str) -> tuple[float, int]:
    per = (tally.get(which) or {}).get(qid) or {}
    n = per.get("_n", 0)
    return ((per.get(oid, 0) / n) if n else 0.0), n


def comparison(questions: list[recruit_form.Question], tally: dict) -> list[dict]:
    """Per option of each choice question: the share among qualified and among screened-out people who answered it, sorted by the gap (largest first)."""
    rows = []
    for i, q in enumerate(questions, 1):
        if q.type not in ("single", "multi"):
            continue
        for o in q.options:
            a, na = _share(tally, "q", q.id, o.id)
            b, nb = _share(tally, "o", q.id, o.id)
            if na + nb == 0:
                continue
            rows.append({"question": f"{i}. {q.text}", "type": q.type, "option": o.text, "qualified": round(a * 100), "out": round(b * 100), "nq": na, "no": nb, "gap": round(abs(a - b) * 100)})
    return sorted(rows, key=lambda r: -r["gap"])[:MAX_COMPARE]


def traits(questions: list[recruit_form.Question], tally: dict) -> dict:
    """What the people who qualified have in common, and what the ones screened out share: plain sentences from the tallies."""
    qualified, out = [], []
    for i, q in enumerate(questions, 1):
        if q.type in ("single", "multi"):
            for o in q.options:
                a, na = _share(tally, "q", q.id, o.id)
                b, nb = _share(tally, "o", q.id, o.id)
                if na >= 3 and a >= SIMILAR_QUALIFIED:
                    qualified.append((a, f"{round(a * 100)}% 选了「{o.text}」（第 {i} 题）"))
                if nb >= 3 and b >= SIMILAR_OUT:
                    out.append((b, f"{round(b * 100)}% 选了「{o.text}」（第 {i} 题）"))
        elif q.type == "number":
            for which, bucket in (("q", qualified), ("o", out)):
                s = (tally.get("num") or {}).get(f"{which}:{q.id}")
                if s and s[1] >= 3:
                    bucket.append((0.5, f"第 {i} 题「{q.text[:20]}」平均 {s[0] / s[1]:.0f}"))
    pick = lambda xs: [t for _, t in sorted(xs, key=lambda x: -x[0])[:MAX_TRAITS]]
    return {"qualified": pick(qualified), "out": pick(out)}


def build(db: Session, project: Project) -> dict:
    """The whole dashboard as one JSON object."""
    form = db.get(RecruitForm, project.id)
    settings = db.get(RecruitSettings, project.id)
    questions = recruit_form.load(form.questions) if form else []
    tally = (form.tally if form else None) or empty_tally()
    people = db.scalars(select(Participant).where(Participant.project_id == project.id, Participant.anonymized_at.is_(None))).all()
    invited_ids = set(db.scalars(select(Interview.participant_id).where(Interview.project_id == project.id)))
    done_ids = set(db.scalars(select(Interview.participant_id).where(Interview.project_id == project.id, Interview.status == "ended")))
    started, qualified = (form.n_started if form else 0), (form.n_qualified if form else 0)
    out_n = form.n_out if form else 0
    finished = qualified + out_n
    usable = [p for p in people if p.screening == "eligible" and p.screen_result != "disqualified"]
    external = sum(1 for p in people if p.source == "import")
    sources = Counter((p.source_name or SOURCE_LABELS.get(p.source, "未记录来源")) for p in people)
    out_by = [{"qid": qid, "label": f"第 {next((i for i, q in enumerate(questions, 1) if q.id == qid), 0)} 题 {next((q.text for q in questions if q.id == qid), '')}"[:40], "n": n}
              for qid, n in sorted(((form.out_by if form else {}) or {}).items(), key=lambda kv: -kv[1])]
    tags = Counter(t for p in people for t in (p.tags or []))
    return {
        "published": bool(form and form.status == "published"), "has_form": bool(questions),
        "kpi": {"started": started, "finished": finished, "qualified": qualified, "pass_rate": round(qualified * 100 / finished) if finished else 0, "external": external,
                "usable": len(usable), "invited": sum(p.id in invited_ids for p in usable), "done": sum(p.id in done_ids for p in people)},
        "funnel": [{"label": "开始作答（累计，含已清理）", "n": started}, {"label": "完成甄别（累计，含已清理）", "n": finished}, {"label": "合格（累计，含已清理）", "n": qualified}, {"label": "外部名单", "n": external},
                   {"label": "已邀请", "n": sum(p.id in invited_ids for p in usable)}, {"label": "完成访谈", "n": sum(p.id in done_ids for p in people)}],
        "sources": [{"name": k, "n": v} for k, v in sources.most_common()],
        "out_by": out_by,
        "quotas": recruit.quota_progress(db, project.id, (settings.quotas if settings else []) or []),
        "rules": {"count": len(settings.rules or []) if settings else 0, "version": settings.rules_version if settings else 0},
        "traits": traits(questions, tally), "compare": comparison(questions, tally),
        "tags": [{"tag": k, "n": v} for k, v in tags.most_common(12)],
        "tally_people": {"qualified": tally.get("nq", 0), "out": tally.get("no", 0)},
    }


def mock_summary(dash: dict) -> str:
    t = dash["traits"]
    first = t["qualified"][0] if t["qualified"] else "还没有足够的数据"
    return f"（离线演示版）合格的人主要特点：{first}。通过率 {dash['kpi']['pass_rate']}%。接上小星星助手后会给出更完整的画像和投放建议。"


def summarize(chat: ModeratorChatProvider | None, dash: dict) -> str:
    """The AI's plain-words reading of the aggregates (only numbers and the researcher's own question and option texts are sent; nobody's personal data).

    Raises:
        recruit_form.FormError: no data yet, or the model service is unavailable.
        ChatFatalError: the model service refused (bad key, no balance...).
    """
    if not dash["compare"] and not dash["traits"]["qualified"]:
        raise recruit_form.FormError("还没有足够的数据，等有人答完问卷再来总结")
    if chat is None:
        return mock_summary(dash)
    facts = {"kpi": dash["kpi"], "out_by": dash["out_by"], "traits": dash["traits"], "compare": dash["compare"]}
    try:
        reply = chat.complete([{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}], temperature=0.3)
        return str(reply["summary"]).strip()[:1500]
    except ChatFatalError:
        raise
    except ChatError as e:
        raise recruit_form.FormError("小星星助手暂时无法总结，请稍后再试") from e
    except (KeyError, TypeError, ValueError) as e:
        log.warning("recruiting summary unusable: %s", type(e).__name__)
        raise recruit_form.FormError("小星星助手这次没有给出可用的总结，请再试一次") from e


def tag_exclusions(questions: list[recruit_form.Question]) -> dict[str, set[str]]:
    """Which tags cannot be on the same person, from the questionnaire's structure only (never from what the names look like):
    the tags of one single-choice question exclude each other, and so do the tags of two options of a multi-choice question that the team linked as mutually exclusive."""
    out: dict[str, set[str]] = {}

    def link(a: str, b: str) -> None:
        if a and b and a != b:
            out.setdefault(a, set()).add(b)
            out.setdefault(b, set()).add(a)

    for q in questions:
        if q.type == "single":
            tags = [o.tag for o in q.options if o.tag]
            for i, a in enumerate(tags):
                for b in tags[i + 1:]:
                    link(a, b)
        elif q.type == "multi":
            by_id = {o.id: o for o in q.options}
            for o in q.options:
                for other_id in o.excl:
                    other = by_id.get(other_id)
                    if other is not None:
                        link(o.tag, other.tag)
    return out


def tag_conflicts(tags: list[str], exclusions: dict[str, set[str]]) -> list[tuple[str, str]]:
    """The pairs among `tags` that exclude each other."""
    mine = list(dict.fromkeys(tags))
    return [(a, b) for i, a in enumerate(mine) for b in mine[i + 1:] if b in exclusions.get(a, ())]


def tag_library(db: Session, project_id: str) -> list[str]:
    """The project's tag library: the questionnaire's tags, the ones added by hand and the ones already on people, minus those taken out."""
    form = db.get(RecruitForm, project_id)
    settings = db.get(RecruitSettings, project_id)
    from_form = [o.tag for q in (recruit_form.load(form.questions) if form else []) for o in q.options if o.tag]
    used = [t for (tags,) in db.execute(select(Participant.tags).where(Participant.project_id == project_id, Participant.anonymized_at.is_(None))) for t in (tags or [])]
    hidden = set((settings.tag_hidden if settings else None) or [])
    out: list[str] = []
    for t in [*from_form, *((settings.tag_lib if settings else None) or []), *used]:
        if t and t not in out and t not in hidden:
            out.append(t)
    return out


def annotate(items: list[dict], questions: list[recruit_form.Question]) -> list[dict]:
    """Add what the questionnaire knows to a participant's saved answers: the question type and the code (1, 2, ...) of each chosen option.

    Answers are matched to the questionnaire by the question's text. Answers typed in by hand, imported, or from a questionnaire that was rewritten
    since get no type and no codes; they still show their number, question and answer.
    """
    by_text = {q.text.strip(): q for q in questions}
    out = []
    for n, it in enumerate(items, 1):
        q = by_text.get(it["question"].strip())
        row = {**it, "n": n, "type": "", "options": []}
        if q is not None and q.type in ("single", "multi"):
            codes = {o.text.strip(): j for j, o in enumerate(q.options, 1)}
            parts = [it["answer"].strip()] if it["answer"].strip() in codes else [x.strip() for x in it["answer"].split("、") if x.strip()]
            row["type"] = q.type
            row["options"] = [{"text": x, "code": codes.get(x)} for x in parts]
        elif q is not None:
            row["type"] = q.type
        out.append(row)
    return out


def key_answers(db: Session, ids: list[str]) -> dict[str, str]:
    """A short "key answers" line per person (their first three answers, shortened), for the list."""
    out: dict[str, list[str]] = {}
    if not ids:
        return {}
    for a in db.scalars(select(ScreenerAnswer).where(ScreenerAnswer.participant_id.in_(ids)).order_by(ScreenerAnswer.participant_id, ScreenerAnswer.position)):
        if len(out.setdefault(a.participant_id, [])) < 3 and a.answer:
            out[a.participant_id].append(a.answer[:18])
    return {k: " · ".join(v) for k, v in out.items()}
