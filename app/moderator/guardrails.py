"""Red lines H1-H12, checked on every sentence before it is said (task doc 4.5). Pure rules, no model calls.

A draft that fails is regenerated (at most MAX_REGENERATIONS times); after that the engine says a safe fallback
sentence instead and raises an alert. Rules cover English and Chinese (phase A). Rules that need judgement
(subtle leading, invented product facts) are also scored by the judge model in the eval (Sprint 3).
Every violation found in a run or a pilot becomes a permanent test case in tests/test_moderator_guardrails.py.
"""
import re
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from .constants import (MAX_HAN_PER_SENTENCE_ZH, MAX_WORDS_PER_SENTENCE_EN, ZH_MIN_HAN_RATIO, ControlMode, RedLine)
from .knowledge import InterviewGuide
from .schemas import Violation
from .templates import templates_for

HAN = re.compile(r"[一-鿿]")
LATIN_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
SENTENCE_END = re.compile(r"(?<=[。！？!?])|(?<=\.)\s+")
QUESTION_MARKS = ("?", "？")


class GuardrailContext(BaseModel):
    """What the gate needs to know about the turn besides the sentence itself."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    guide: InterviewGuide
    is_statement: bool = False          # closing / stop / crisis statement: must contain no question
    is_first_turn: bool = False         # the intro: must disclose the AI (H3)
    participant_asked_ai: bool = False  # must answer honestly (H3)
    participant_wants_stop: bool = False
    participant_in_crisis: bool = False
    control: ControlMode = ControlMode.AI


# ---------- helpers ----------
def _sentences(text: str) -> list[str]:
    """Split into sentences on Chinese and Latin end punctuation."""
    return [s.strip() for s in SENTENCE_END.split(text) if s and s.strip()]


def _question_sentences(text: str) -> list[str]:
    """Sentences that end with a question mark."""
    return [s for s in _sentences(text) if s.endswith(QUESTION_MARKS)]


def _found(patterns: list[str], text: str) -> str | None:
    """The first pattern found in the text (case-insensitive), or None."""
    return next((p for p in patterns if re.search(p, text, flags=re.IGNORECASE)), None)


def _matched_text(patterns: list[str], text: str) -> str | None:
    """The words in the text that the first matching pattern found (what to tell the model to avoid), or None."""
    for pattern in patterns:
        if match := re.search(pattern, text, flags=re.IGNORECASE):
            return match.group(0).strip() or pattern
    return None


def _v(line: RedLine, detail: str) -> list[Violation]:
    """One violation, as a list (each check returns a list)."""
    return [Violation(red_line=line, detail=detail[:200])]


# ---------- H1 one question ----------
EN_DOUBLE = [r"\?\s*\S.*\?", r"\b(?:and|or) (?:what|why|how|when|where|who|which|do you|did you|would you|could you|is it|are you)\b"]
ZH_WH = r"(?:为什么|为何|怎么|怎样|如何|什么|哪(?:里|儿|些|个|种|家)?|谁|多少|几(?:个|次|种|年|天))"
# "A吗，B吗" / "A吗，为什么" / "以及…" / "另外…吗" are two questions; "还有什么…，是…吗？" (wh then 吗) is one question
ZH_DOUBLE = [r"以及", r"另外.*(?:吗|呢|？)", r"(?:吗|呢)[，,；;].*(?:吗|呢)", r"(?:吗|呢)[，,；;].*" + ZH_WH]


def check_one_question(text: str, ctx: GuardrailContext) -> list[Violation]:
    """H1: exactly one question (none in a closing statement), and no two questions packed into one sentence."""
    count = sum(text.count(m) for m in QUESTION_MARKS)
    if ctx.is_statement:
        return _v(RedLine.H1_ONE_QUESTION, "statement contains a question") if count else []
    if count != 1:
        return _v(RedLine.H1_ONE_QUESTION, f"{count} questions")
    question = _question_sentences(text)[-1] if _question_sentences(text) else text
    if ctx.guide.language == "zh":
        wh_clauses = [c for c in re.split(r"[，,；;]", question) if re.search(ZH_WH, c)]
        if len(wh_clauses) > 1 or _found(ZH_DOUBLE, question):
            return _v(RedLine.H1_ONE_QUESTION, "two questions in one sentence")
    elif _found(EN_DOUBLE, question):
        return _v(RedLine.H1_ONE_QUESTION, "two questions in one sentence")
    return []


# ---------- H2 leading ----------
LEADING = [
    r"\bdon'?t you (?:think|agree|feel)\b", r"\bwouldn'?t you (?:say|agree)\b", r"\bisn'?t it\b", r"\baren'?t you\b", r"\bsurely\b",
    r"\bmost people\b", r"\ba lot of people (?:think|say|feel|believe)\b", r"\beveryone (?:knows|says|thinks|agrees)\b",
    r"\byou must (?:have|be|feel|think)\b", r"\bi'?m sure you\b", r"\bobviously\b", r",\s*right\?", r"\bdidn'?t you\b",
    r"是不是", r"难道", r"不觉得.*(?:吗|？)", r"很多人(?:都)?(?:觉得|认为|说)", r"大家都", r"[你您]一定", r"肯定(?:很|会|是|也)",
    r"对吧", r"是吧", r"应该(?:很|会|是)?.{0,10}吧", r"[你您]也(?:觉得|认为)",
]


def check_leading(text: str, ctx: GuardrailContext) -> list[Violation]:
    """H2: no leading or suggestive wording (subtler cases are also scored by the judge model)."""
    hit = _matched_text(LEADING, text)
    return _v(RedLine.H2_LEADING, f"leading wording: {hit}") if hit else []


# ---------- H3 AI disclosure / honesty ----------
CLAIMS_HUMAN = [r"\bi(?:'m| am) (?:a )?(?:real )?(?:person|human)\b", r"\bi(?:'m| am) not (?:an? )?(?:ai|bot|robot)\b", r"我是真人",
                r"我是(?:一个)?人类", r"我不是(?:AI|ai|机器人|人工智能)"]
ADMITS_AI = [r"\bAI\b", r"\bartificial intelligence\b", r"人工智能", r"AI"]


def check_ai_disclosure(text: str, ctx: GuardrailContext) -> list[Violation]:
    """H3: the first turn discloses the AI; an "are you real?" gets an honest answer; never claim to be human."""
    if hit := _found(CLAIMS_HUMAN, text):
        return _v(RedLine.H3_AI_DISCLOSURE, f"claims to be human: {hit}")
    if ctx.is_first_turn:
        markers = templates_for(ctx.guide.language).ai_disclosure_markers
        if not all(m.lower() in text.lower() for m in markers):
            return _v(RedLine.H3_AI_DISCLOSURE, "intro does not disclose the AI moderator")
    if ctx.participant_asked_ai and not _found(ADMITS_AI, text):
        return _v(RedLine.H3_AI_DISCLOSURE, "did not answer honestly that it is an AI")
    return []


# ---------- H4 personal data ----------
PII_NOUNS_EN = (r"(?:full |real |last |first )?name|phone number|mobile number|contact number|e-?mail(?: address)?|home address|address|nric(?: number)?|"
                r"ic number|passport(?: number)?|account (?:number|details|name)|password|bank (?:account|details)|card number|id number|identity card")
PII_ASK_EN = [r"\b(?:what(?:'s| is| are)|tell me|share|give me|provide|may i (?:have|get|ask)|can i (?:have|get)|could (?:i have|you (?:give|tell|share|provide))|send me)\b"
              r"[^.?!]{0,25}\b(?:your|ur) (?:" + PII_NOUNS_EN + r")\b", r"\bwhere do you live\b", r"\bwhat should i call you\b"]
PII_ASK_ZH = [r"[你您](?:的)?(?:真实)?(?:姓名|全名|名字|电话号码|手机号|联系方式|微信号|邮箱|住址|地址|身份证|护照|账号|密码|银行卡|卡号)",
              r"(?:请|麻烦)(?:您|你)?(?:提供|告诉我|留下|发给我).{0,10}(?:姓名|名字|电话|手机|邮箱|地址|身份证|护照|账号|密码|银行卡)",
              r"[你您]住在哪", r"[你您]叫什么(?:名字)?"]


def check_personal_data(text: str, ctx: GuardrailContext) -> list[Violation]:
    """H4: never ask for name, phone, ID, accounts or other personal data. Only questions/requests count, so the
    intro may say "the AI will not ask for your name"."""
    asking = [s for s in _sentences(text) if s.endswith(QUESTION_MARKS) or re.search(r"(?:请|麻烦|please|tell me|provide)", s, re.I)]
    for sentence in asking:
        if re.search(r"(?:不会|不用|无需|不需要|won'?t|will not|don'?t need)", sentence, re.I):
            continue  # a reassurance that we do not need it
        if hit := _found(PII_ASK_ZH + PII_ASK_EN, sentence):
            return _v(RedLine.H4_PERSONAL_DATA, f"asks for personal data: {hit}")
    return []


# ---------- H5 stop / crisis ----------
def check_stop_or_crisis(text: str, ctx: GuardrailContext) -> list[Violation]:
    """H5: after a stop request or a crisis signal, only the closing / safety statement; a crisis one gives help numbers."""
    if not (ctx.participant_wants_stop or ctx.participant_in_crisis):
        return []
    if not ctx.is_statement or any(m in text for m in QUESTION_MARKS):
        return _v(RedLine.H5_STOP_OR_CRISIS, "kept interviewing after a stop request or crisis signal")
    if ctx.participant_in_crisis and not any(m in text for m in templates_for(ctx.guide.language).crisis_markers):
        return _v(RedLine.H5_STOP_OR_CRISIS, "crisis statement without help numbers")
    return []


# ---------- H6 language ----------
def check_language(text: str, ctx: GuardrailContext) -> list[Violation]:
    """H6: reply in the interview language only (brand names in Latin letters are fine in Chinese)."""
    han = len(HAN.findall(text))
    latin = sum(len(w) for w in LATIN_WORD.findall(text))
    if ctx.guide.language == "zh":
        if han == 0 or han / (han + latin / 4) < ZH_MIN_HAN_RATIO:  # a Latin word counts like ~1 Chinese character per 4 letters
            return _v(RedLine.H6_LANGUAGE, "reply is not in Chinese")
    elif ctx.guide.language == "en" and han:
        return _v(RedLine.H6_LANGUAGE, "Chinese characters in an English interview")
    return []


# ---------- H7 evaluative ----------
EVALUATIVE = [
    r"\bgreat (?:answer|point|question|insight)\b", r"\bgood (?:point|answer)\b", r"\bi (?:totally |completely )?agree\b", r"\bthat'?s (?:so )?(?:right|true|correct|wrong)\b",
    r"(?:^|[.!?]\s*)(?:exactly|perfect|awesome|wonderful|fascinating|brilliant|excellent|interesting)\s*[,.!]", r"\bi (?:also|too) (?:think|feel|believe)\b",
    r"\byou'?re (?:right|wrong)\b", r"\bwell said\b", r"\bthat'?s not true\b", r"\bi disagree\b", r"\bwhat a (?:great|good|wonderful)\b",
    r"说得(?:好|对|很好|太好|有道理)", r"我也(?:这么|这样)?(?:觉得|认为|是)", r"我(?:很|完全)?同意", r"没错", r"确实如此", r"太棒了", r"[你您](?:说得)?(?:错了|不对)",
    r"有道理", r"(?:^|[。！？，,])很好[。！!，,]", r"真不错", r"非常好", r"我不同意", r"这不对",
]


def check_evaluative(text: str, ctx: GuardrailContext) -> list[Violation]:
    """H7: no praising, agreeing or contradicting."""
    hit = _matched_text(EVALUATIVE, text)
    return _v(RedLine.H7_EVALUATIVE, f"evaluative wording: {hit}") if hit else []


# ---------- H8 confidential ----------
OTHER_PARTICIPANTS = [r"\bother (?:participants|interviewees|respondents)\b", r"\banother (?:participant|interviewee|respondent)\b",
                      r"\bsomeone else (?:said|told|mentioned)\b", r"\bprevious (?:participant|interviewee|person)\b", r"\bother people we(?:'ve| have)? (?:talked|spoken|spoke)\b",
                      r"(?:其他|别的|另一位|另外一位|上一位|之前的)(?:受访者|参与者|被访者)", r"之前有(?:人|位)(?:说|提到|讲)", r"别人(?:都)?(?:跟我|和我)?说"]


def check_confidential(text: str, ctx: GuardrailContext) -> list[Violation]:
    """H8: no client brand (unless the guide allows it) and no mention of other participants."""
    if not ctx.guide.brand_disclosure_allowed:
        for brand in ctx.guide.client_brand_names:
            if brand.lower() in text.lower():
                return _v(RedLine.H8_CONFIDENTIAL, "names the client brand")
    if hit := _found(OTHER_PARTICIPANTS, text):
        return _v(RedLine.H8_CONFIDENTIAL, f"mentions other participants: {hit}")
    return []


# ---------- H9 advice ----------
ADVICE = [r"\byou should\b", r"\bi (?:would |'d )?(?:recommend|suggest|advise)\b", r"\bmy advice\b", r"\byou'?d better\b", r"\bconsult a (?:doctor|lawyer|dermatologist)\b",
          r"\bsee a (?:doctor|dermatologist)\b", r"\binvest (?:in|your)\b", r"\bit(?:'s| is) (?:best|better) to (?:take|use|buy|see|invest)\b",
          r"我建议", r"建议[你您](?:去|用|买|吃|看|咨询|找|试)", r"[你您]应该", r"我推荐", r"推荐[你您]", r"最好(?:去|吃|用|买|找|看)", r"可以考虑(?:去|吃|用|买|投资)",
          r"去看(?:医生|皮肤科)", r"咨询(?:一下)?律师", r"(?:买|投资)(?:点|些)?(?:股票|基金|黄金)"]


def check_advice(text: str, ctx: GuardrailContext) -> list[Violation]:
    """H9: no medical, legal, investment or purchase advice."""
    hit = _matched_text(ADVICE, text)
    return _v(RedLine.H9_ADVICE, f"gives advice: {hit}") if hit else []


# ---------- H10 forbidden topics ----------
def check_forbidden_topics(text: str, ctx: GuardrailContext) -> list[Violation]:
    """H10: never bring up a topic the project forbids."""
    hit = next((t for t in ctx.guide.forbidden_topics if t.lower() in text.lower()), None)
    return _v(RedLine.H10_FORBIDDEN_TOPIC, f"forbidden topic: {hit}") if hit else []


# ---------- H11 silent after take-over ----------
def check_silent_after_takeover(text: str, ctx: GuardrailContext) -> list[Violation]:
    """H11: once a human has taken over, the AI says nothing at all (the engine never calls the model then)."""
    return _v(RedLine.H11_SILENT_AFTER_TAKEOVER, "AI spoke while a human is in control") if ctx.control is ControlMode.HUMAN and text.strip() else []


# ---------- H12 length ----------
def check_length(text: str, ctx: GuardrailContext) -> list[Violation]:
    """H12: each sentence short enough to listen to (≤ 25 English words / ≤ 40 Chinese characters)."""
    for sentence in _sentences(text):
        if ctx.guide.language == "zh" and len(HAN.findall(sentence)) > MAX_HAN_PER_SENTENCE_ZH:
            return _v(RedLine.H12_TOO_LONG, f"sentence has {len(HAN.findall(sentence))} Chinese characters")
        if ctx.guide.language == "en" and len(LATIN_WORD.findall(sentence)) > MAX_WORDS_PER_SENTENCE_EN:
            return _v(RedLine.H12_TOO_LONG, f"sentence has {len(LATIN_WORD.findall(sentence))} words")
    return []


CHECKS: list[tuple[RedLine, Callable[[str, GuardrailContext], list[Violation]]]] = [
    (RedLine.H1_ONE_QUESTION, check_one_question), (RedLine.H2_LEADING, check_leading), (RedLine.H3_AI_DISCLOSURE, check_ai_disclosure),
    (RedLine.H4_PERSONAL_DATA, check_personal_data), (RedLine.H5_STOP_OR_CRISIS, check_stop_or_crisis), (RedLine.H6_LANGUAGE, check_language),
    (RedLine.H7_EVALUATIVE, check_evaluative), (RedLine.H8_CONFIDENTIAL, check_confidential), (RedLine.H9_ADVICE, check_advice),
    (RedLine.H10_FORBIDDEN_TOPIC, check_forbidden_topics), (RedLine.H11_SILENT_AFTER_TAKEOVER, check_silent_after_takeover),
    (RedLine.H12_TOO_LONG, check_length),
]


def check_utterance(text: str, ctx: GuardrailContext) -> list[Violation]:
    """Run every red line on a sentence about to be said.

    Args:
        text: what the AI is about to say.
        ctx: facts about this turn.

    Returns:
        All violations found; empty means it may be said.
    """
    found: list[Violation] = []
    for _, check in CHECKS:
        found += check(text, ctx)
    return found
