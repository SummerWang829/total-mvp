"""The outline lint: the measured spec of §6 as deterministic checks, used to send a bad outline back to the model.

Why this exists: the transcripts show what a real guide must cover (attribute / feeling / reason / boundary are the
four dimensions that appear in almost every interview) and how it must be written (complete, self-contained, one
question per sentence) — while the live moderators' 8% confirmation questions and 7% leading questions are *not*
guide material. Every rule below is a number taken from that analysis; none of it is a style preference.

The messages are written for the model to read: `_valid()` returns them as the retry reason, so a rejected outline
comes back as "fix these exact things", not as "try again".
"""
import re
from collections import Counter

from .moderator.knowledge import InterviewGuide

MAX_QUESTIONS_ZH = 45          # the corpus' moderator turn median is 16 characters; 45 is the ceiling for a readable question
MAX_QUESTIONS_OTHER = 90       # English carries the same content in about twice the characters
MIN_TOPIC_QUESTIONS = 3        # measured: median topic block is 5 questions, p75 8
MIN_AVERAGE_QUESTIONS = 3.5    # a topic that is asked 3 questions deep is not yet a topic
SCALE = re.compile(r"0\s*[-–~至到]\s*10|1\s*[-–~至到]\s*5|打分|评分|满分|打几分|几分")
WHY = re.compile(r"为什么|为何|依据|原因|理由|怎么会|凭什么")
MIN_TOTAL_QUESTIONS = 12       # below this there is nothing to measure: a real interview needs a body
MAX_TOTAL_QUESTIONS = 45       # above this the participant is tired; the measured guides sit at 18-25
SECONDS_BY_DEPTH = {"none": 40, "shallow": 60, "deep": 108, "free": 90}   # asked + answered, before probing
OVERHEAD_S = 180               # welcome, consent, closing
DURATION_TOLERANCE = 0.20      # +/- 20% of the planned minutes is fine
CORE_DIMENSIONS = ("attribute", "feeling", "reason", "boundary")   # the four with the highest coverage in the corpus
MIN_CORE_DIMENSIONS = 2        # a safety net, not the spec: the prompt asks for 3, the lint blocks a guide of pure "what happened"

ANY_YESNO = re.compile(r"是不是|有没有|会不会|能不能|可不可以|是否|要不要")
NARRATIVE = re.compile(r"哪次|哪几|哪些|哪个|什么|怎么|如何|当时|后来|过程|多久|多少|几次|哪一回")
YESNO = re.compile(r"^(?:你|您|那|那么|所以|平时|一般)?\s*(是不是|有没有|会不会|能不能|可不可以|是否|要不要|对吗|对吧|好不好|行不行|买过吗|知道吗|用过吗)"
                   r"|^(do|did|have|has|are|is|was|were|would|can|could|will) (you|it|they|there)\b(?!.*\banything\b)", re.I)
LEADING = re.compile(r"你不觉得|难道|是不是觉得|你也觉得|你有没有觉得|don't you think|wouldn't you|isn't it", re.I)
ELLIPSIS = re.compile(r"^.{1,8}呢[？?]$")
INTERROG = re.compile(r"什么|为什么|怎么|如何|哪|谁|多少|是不是|有没有|会不会|能不能|哪里")   # one clause carrying a question word
SECOND_Q = re.compile(r"^(?:是|那|为什么|怎么|如何|哪|谁|多少|是不是|有没有|会不会|能不能|什么时候|哪里)")   # a clause that opens a second question
CLOSING = re.compile(r"收尾|结束|结尾|wrap|closing|close", re.I)   # one question by design, so not held to the 3+ rule

DIMENSIONS = {
    "attribute": (r"什么材质|什么颜色|什么成分|什么功能|什么味道|什么规格|哪些成分|材质|成分|功效|包装|外观|口感|规格|look like|taste like|material|ingredient|feature|packaging|colour|color", "属性"),
    "feeling": (r"觉得|感觉|印象|喜欢|不喜欢|满意|感受|看法|第一反应|how did you feel|what did you think|impression|do you like|dislike|satisf", "感受"),
    "reason": (r"为什么|为啥|原因|\bwhy\b", "原因"),
    "boundary": (r"多少钱|价格|预算|能接受|接受|上限|贵|便宜|值不值|划算|price|how much|afford|accept|upper limit|worth", "边界"),
    "occasion": (r"哪一次|哪次|上次|上一次|当时|那天|last time|the time when|that day", "具体情境"),
    "process": (r"怎么|如何|后来|然后|过程|流程|做了什么|\bhow did\b|what happened", "过程"),
    "criteria": (r"看重|标准|考虑什么|在意|what mattered|criteria|important", "标准"),
    "compare": (r"还是|比较|相比|区别|哪个|other brands?|compare|versus|\bvs\b", "对比"),
    "source": (r"从哪|哪里知道|听说|广告|推荐|信息来源|where did you (hear|see|find)|recommendation", "来源"),
    "barrier": (r"没买|放弃|顾虑|担心|犹豫|麻烦|不买|gave up|stopped considering|put you off|worry", "阻碍"),
    "intention": (r"会不会买|愿意尝试|愿意买|今后|下次会|would you buy|would you use|intend to", "意向"),
}
DIMENSION_RE = {k: re.compile(v[0], re.I) for k, v in DIMENSIONS.items()}


def _questions(guide: InterviewGuide) -> list[str]:
    out = list(guide.warmup_questions)
    for topic in guide.topics:
        out += list(topic.questions)
    for stim in guide.stimuli:
        out += list(stim.questions)
    return out


def dimension_of(q: str) -> str | None:
    """Which content dimension one question is after (first marker that matches), or None.

    The order matters: a question about "这个多少钱、你觉得贵吗" is a boundary question, not a feeling one.
    """
    # why/boundary are unambiguous; 觉得 and 成分/材质 appear in nearly every question, so they decide last
    for key in ("reason", "boundary", "attribute", "occasion", "process", "criteria", "compare", "source", "barrier",
                "intention", "feeling"):
        if DIMENSION_RE[key].search(q):
            return key
    return None


def _dim(q: str, meta: dict | None) -> str | None:
    """The dimension of one question: what the model declared, else what the markers say.

    The model chooses a topic's spine before writing, so its declaration is the intent; the regex is only a fallback
    for older text that has no 维度 line (and for the questions of a hand-written guide).
    """
    declared = str((meta or {}).get("dim") or "").lower()
    return declared if declared in DIMENSION_RE else dimension_of(q)


def dimensions_of(guide: InterviewGuide) -> list[str]:
    """The content dimensions the guide actually covers, in a stable order (same markers as the corpus analysis)."""
    text = " ".join(_questions(guide))
    return [k for k, rx in DIMENSION_RE.items() if rx.search(text)]


def _planned_minutes(guide: InterviewGuide, notes: dict | None) -> float:
    """How long this guide takes at the depth each question is written for (welcome and closing included)."""
    seconds = OVERHEAD_S
    meta = notes or {}
    topics = getattr(guide, "topics", None)
    if topics is None and isinstance(guide, dict):
        topics = guide.get("topics") or []
    topics = topics or []
    questions_of = lambda p: list(getattr(p, "questions", None) or (p.get("questions") if isinstance(p, dict) else []) or [])
    for m in meta.get("warmup") or []:
        seconds += SECONDS_BY_DEPTH.get(str((m or {}).get("depth", "shallow")), 60)
    topics_meta = meta.get("topics") or []
    for ti, topic in enumerate(topics):
        row = topics_meta[ti] if ti < len(topics_meta) else []
        for qi in range(len(questions_of(topic))):
            # no note means the model wrote no depth: a topic question is probed, so it costs a deep one
            depth = str((row[qi] if qi < len(row) else {}).get("depth", "deep") or "deep")
            seconds += SECONDS_BY_DEPTH.get(depth, 60)
    for si, stimulus in enumerate(getattr(guide, "stimuli", None) or (guide.get("stimuli") if isinstance(guide, dict) else []) or []):
        stimuli_meta = meta.get("stimuli") or []
        row = stimuli_meta[si] if si < len(stimuli_meta) else []
        for qi in range(len(questions_of(stimulus))):
            depth = str((row[qi] if qi < len(row) else {}).get("depth", "shallow") or "shallow")
            seconds += SECONDS_BY_DEPTH.get(depth, 60)
    return seconds / 60.0


def _is_yesno(plain: str) -> bool:
    """A question the person can answer with 是/不是, 有/没有.

    Two shapes: it opens with a yes/no word ("你有没有…", "是不是…"), or it ends with 吗/嘛 ("买之前你看成分表吗").
    A question that carries a narrative word (哪次/什么/怎么/当时…) is asking for a story, not for a yes or no, so it is
    not flagged — measured on the fixture set: this reports 9 of the 11 "…吗" questions and stops the 3 false alarms.
    """
    if NARRATIVE.search(plain):
        return False
    if YESNO.search(plain) or ANY_YESNO.search(plain):   # "买之前你有没有看过成分表" opens mid-sentence
        return True
    return plain.rstrip().endswith(("吗", "嘛"))


def _form_problems(q: str, language: str, where: str) -> list[str]:
    limit = MAX_QUESTIONS_ZH if language == "zh" else MAX_QUESTIONS_OTHER
    plain = q.strip().rstrip("？?").strip()
    out = []
    if len(plain) > limit:
        out.append(f"{where} 太长（{len(plain)} 字，上限 {limit}）：「{plain[:20]}…」：删掉铺垫，只留一个疑问")
    clauses = [c.strip() for c in re.split(r"[，,]", plain) if c.strip()]
    # a second question starts its own clause with a question word ("…，是什么让你放下？");
    # "…，自己一年大概要花到多少才用得上" is still one question, so a clause merely containing one is not enough
    compound = len(clauses) > 1 and bool(SECOND_Q.match(clauses[-1])) and any(INTERROG.search(c) for c in clauses[:-1])
    if len(re.findall(r"[？?]", q)) > 1 or compound:
        out.append(f"{where} 一句里有两个问题（「{plain[:24]}」）：拆成两题，一题只问一件事，例如把「当时哪几款被你放弃了，是什么让你放下？」拆成「当时哪几款被你放弃了？」和「是什么让你放下？」")
    if ELLIPSIS.match(q.strip()):
        out.append(f"{where} 是省略式追问（「{plain}」）：补成一句能独立读懂的话，例如把「那支精华呢？」写成「那支精华你当时为什么选它？」")
    if _is_yesno(plain):
        out.append(f"{where} 是是非题（「{plain[:24]}」）：把「有没有/是不是/吗」删掉，改成以「哪一次 / 哪几款 / 当时你怎么做」开头")
    if LEADING.search(q):
        out.append(f"{where} 带诱导（「{plain[:24]}」）：删掉「你不觉得/难道/是不是」这类替受访者下结论的说法")
    return out


def check(guide: InterviewGuide, language: str, notes: dict | None = None, strict: bool = True, minutes: int | None = None) -> list[str]:
    """Everything wrong with this guide, in the model's own working language (Chinese), newest problems first.

    Args:
        guide: the outline as the engine will read it.
        language: the interview language (a 45-character limit for Chinese, 90 for the others).
        notes: {"warmup": [...], "topics": [[...]], "stimuli": [[...]]} — checked for one entry per question.
        strict: False skips the structural rules (used by the offline mock draft, which is a sketch by design).

    Returns:
        An empty list when the guide passes, otherwise the reasons to send back to the model.
    """
    problems: list[str] = []
    for i, q in enumerate(guide.warmup_questions, 1):
        problems += _form_problems(q, language, f"暖场第 {i} 题")
    for ti, topic in enumerate(guide.topics, 1):
        for qi, q in enumerate(topic.questions, 1):
            problems += _form_problems(q, language, f"话题「{topic.title}」第 {qi} 题")
    for si, stimulus in enumerate(guide.stimuli, 1):
        for qi, q in enumerate(stimulus.questions, 1):
            problems += _form_problems(q, language, f"素材「{stimulus.description}」第 {qi} 题")

    questions = _questions(guide)
    if strict:   # the totals hold whatever the topic count: two topics of one question each are not a guide
        total = len(questions)
        if total < MIN_TOTAL_QUESTIONS:
            problems.append(f"整份提纲只有 {total} 题：少于 {MIN_TOTAL_QUESTIONS} 题撑不起一场访谈")
        elif total > MAX_TOTAL_QUESTIONS:
            problems.append(f"整份提纲有 {total} 题：超过 {MAX_TOTAL_QUESTIONS} 题，受访者会在中途疲劳")
        if minutes:
            planned = _planned_minutes(guide, notes)
            if abs(planned - minutes) / minutes > DURATION_TOLERANCE:
                problems.append(f"按现在的追问深度，这份提纲约 {planned:.0f} 分钟，和你设定的 {minutes} 分钟差得太多："
                                f"要么加减题目，要么调整题目深度")
    if strict:   # a rating without a follow-up "why" is a number nobody can explain
        for ti, topic in enumerate(guide.topics, 1):
            for qi, q in enumerate(topic.questions):
                if not SCALE.search(q):
                    continue
                nxt = topic.questions[qi + 1] if qi + 1 < len(topic.questions) else ""
                if not (nxt and WHY.search(nxt)):
                    problems.append(f"话题「{topic.title}」第 {qi + 1} 题是评分题，但它后面没有一题问「为什么是这个分」："
                                    f"量表只能测强度，原因要紧接着问")
                    break

    if strict and len(guide.topics) >= 1:
        # a closing topic is one question by design ("还有什么我没问到的"); everything else must be asked 3+ deep
        body = [(t, len(t.questions)) for t in guide.topics if not CLOSING.search(t.title)]
        for topic, n in body:
            if n < MIN_TOPIC_QUESTIONS:
                problems.append(f"话题「{topic.title}」只有 {n} 题：每个话题至少 {MIN_TOPIC_QUESTIONS} 题（真实访谈的中位数是 5 题）")
        sizes = [n for _, n in body]
        if sizes and sum(sizes) / len(sizes) < MIN_AVERAGE_QUESTIONS:
            problems.append(f"每个话题平均只有 {sum(sizes) / len(sizes):.1f} 题：至少要 {MIN_AVERAGE_QUESTIONS:.0f} 题，一个话题要能被问进去，不能一题就换")
        flat = []   # a topic must drill: 2+ questions on the same dimension (measured: 1-2 dimensions per topic)
        for ti, (topic, _) in enumerate(body, 1):
            row = (notes or {}).get("topics") or []
            row = row[ti - 1] if ti - 1 < len(row) else []
            dims = [d for d in (_dim(q, row[qi] if qi < len(row) else None) for qi, q in enumerate(topic.questions)) if d]
            if len(topic.questions) >= 3 and dims and max(Counter(dims).values()) < 2:
                flat.append(topic.title)
        if flat and (len(flat) >= 2 or len(body) == 1):   # one flat topic among several is survivable; alone it is the whole guide
            problems.append("话题" + "、".join(f"「{t}」" for t in flat[:3])
                            + "每一题都在换维度：真实访谈里一个话题会在同一维度上钻 2–3 次（例如「什么材质」→「哪几支」→「和你在用的那支比」）。请让大多数话题至少两题落在同一维度")
        declared = [str((m or {}).get("dim") or "") for topic_rows in ((notes or {}).get("topics") or [])
                    for m in (topic_rows or [])]
        covered = dimensions_of(guide) + [d for d in dict.fromkeys(declared) if d and d not in dimensions_of(guide)]
        missing = [DIMENSIONS[d][1] for d in CORE_DIMENSIONS if d not in covered]
        if len(missing) > len(CORE_DIMENSIONS) - MIN_CORE_DIMENSIONS:
            problems.append(f"缺少维度：{('、'.join(missing))}。属性、感受、原因、边界这四类里至少要覆盖两类（真实访谈里它们出现在 26%–53% 的话题中）")

    if notes:
        if len(notes.get("warmup") or []) != len(guide.warmup_questions):
            problems.append("暖场的 notes 数量与问题数量不一致")
        for ti, topic in enumerate(guide.topics):
            topic_notes = (notes.get("topics") or [])
            if ti < len(topic_notes) and len(topic_notes[ti] or []) != len(topic.questions):
                problems.append(f"话题「{topic.title}」的 notes 数量与问题数量不一致")

    return problems[:6]
