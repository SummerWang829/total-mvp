"""Fixed names, limits and user-facing texts for the AI moderator (Quality Charter: no magic numbers or strings in logic).

Values a deployment may want to change (model, URLs, timeouts) live in .env via app/config.py instead.
"""
from enum import Enum


class ModerationMode(str, Enum):
    """Who runs an interview. Stored on Interview.moderation; decides whether the consent text discloses the AI."""

    HUMAN = "human"
    AI = "ai"


class ModeratorProviderName(str, Enum):
    """Accepted values of MODERATOR_PROVIDER."""

    MOCK = "mock"      # scripted, offline; every test uses it
    OPENAI = "openai"  # any OpenAI-compatible /chat/completions (local Ollama or a cloud provider)


class Phase(str, Enum):
    """Stages of an interview (task doc 4.3). Code moves between them; the model never jumps by itself."""

    INTRO = "intro"        # disclose the AI, purpose, recording and privacy; ends when the participant says they are ready
    WARMUP = "warmup"      # easy narrative questions
    CORE = "core"          # the guide's topics, one after another (each topic is a sub-state, see fsm.TopicProgress)
    STIMULI = "stimuli"    # reactions to shown materials (optional)
    RECOVERY = "recovery"  # ask each missed must-cover item once, before wrapping up
    WRAPUP = "wrapup"      # summarise, ask "anything important we did not talk about?"
    ENDED = "ended"


class EndReason(str, Enum):
    """Why an interview reached Phase.ENDED."""

    COMPLETED = "completed"
    PARTICIPANT_STOPPED = "participant_stopped"  # red line H5: stop at once, politely
    CRISIS = "crisis"                            # red line H5: safety statement + red alert to staff


class PacingAction(str, Enum):
    """What the state machine tells the engine to do next turn."""

    CONTINUE = "continue"            # stay on the current question; a follow-up is allowed only if followup_allowed
    NEXT_QUESTION = "next_question"  # ask the next prepared question of the same topic / phase
    NEXT_TOPIC = "next_topic"        # transition to the next core topic
    ENTER_PHASE = "enter_phase"      # a new phase has started (its opening question is due)
    PROBE_ITEM = "probe_item"        # prepared questions used up: explore this uncovered must-cover item of the topic
    EXPLORE = "explore"              # prepared questions and items are used up but the topic still has time: dig deeper into what was said
    ASK_MISSED_ITEM = "ask_missed"   # recovery: ask this never-asked must-cover item, once
    END = "end"


class Speaker(str, Enum):
    """Who said a line in the conversation memory."""

    AI = "ai_moderator"        # shown in transcripts as "AI 主持人"
    HUMAN = "human_moderator"  # a staff member who took over (Sprint 5)
    PARTICIPANT = "participant"


MAX_WHISPER_CHARS = 500  # an observer's note to the AI; longer notes are refused, not cut (the observer sees why)


class TransitionReason(str, Enum):
    """Why the state machine decided what it decided. Logged on every turn and checked by the eval."""

    STARTED = "started"
    IN_PROGRESS = "in_progress"
    PARTICIPANT_READY = "participant_ready"
    QUESTION_DONE = "question_done"        # the engine judged the prepared question answered
    FOLLOWUP_LIMIT = "followup_limit"      # 3 follow-ups on one question (hard cap)
    QUESTIONS_DONE = "questions_done"      # nothing left to ask in this topic / phase (prepared questions and items)
    ITEM_COVERED = "item_covered"          # the item being explored is now covered
    BEHIND_SCHEDULE = "behind_schedule"    # time is running out in the topic and must-cover items are still unasked: go straight to them
    EXPLORING = "exploring"              # time is left in the topic and the answers still add information: dig deeper
    SATURATED = "saturated"                # topic saturation >= threshold
    TOPIC_TURN_LIMIT = "topic_turn_limit"
    TOPIC_TIME_UP = "topic_time_up"
    PHASE_TIME_UP = "phase_time_up"
    MISSED_ITEMS = "missed_items"          # must-cover items left: ask each once before wrapping up
    OVERTIME = "overtime"                  # 15 % over the total budget: straight to wrap-up
    WRAPUP_ANSWERED = "wrapup_answered"
    PARTICIPANT_STOPPED = "participant_stopped"
    CRISIS = "crisis"


# Pacing defaults (task doc 4.3); calibrated by the benchmark later, so they live in PacingConfig, not in the logic.
REFERENCE_DURATION_MIN = 60.0
# minutes of a 60-minute interview; scaled in proportion to the project's planned duration
PHASE_MINUTES_AT_60: dict[Phase, float] = {Phase.INTRO: 4.0, Phase.WARMUP: 6.5, Phase.STIMULI: 11.0, Phase.RECOVERY: 1.5, Phase.WRAPUP: 4.0}
DEFAULT_SATURATION_THRESHOLD = 0.8
DEFAULT_MAX_FOLLOWUPS_PER_QUESTION = 3
DEFAULT_HURRY_AFTER_SHARE = 0.5       # past this share of a topic's time, with must-cover items still unasked: no more follow-ups, ask the items
DEFAULT_EXPLORE_UNTIL_SHARE = 0.85   # keep exploring a topic while less than this share of its time budget is used
DEFAULT_MAX_EXPLORE_PER_TOPIC = 6     # at most this many extra deeper questions per topic
DEFAULT_EXPLORE_MIN_INFO_GAIN = 0.3   # stop exploring when the latest answer added less new information than this (diminishing returns)
DEFAULT_OVERTIME_FORCE_RATIO = 1.15  # 15 % over the total budget: wrap up now
DEFAULT_MAX_TURNS_PER_TOPIC = 14
DEFAULT_RECENT_TURNS_VERBATIM = 12    # older turns are condensed for the model
DIGEST_LINE_CHARS = 70                # one older participant answer, cut to this many characters in the condensed history
CONTEXT_ASKED_QUESTIONS = 20          # the model is shown only the last 20 questions asked (the repeat guard in the engine checks all of them)
DIGEST_MAX_CHARS = 1800               # the condensed history never grows past this (the newest lines win)

class ResponseCategory(str, Enum):
    """What kind of answer the participant just gave (task doc 4.2 "judge")."""

    ANSWERED = "answered"              # a real answer
    TOO_SHORT = "too_short"
    OFF_TOPIC = "off_topic"
    EMOTIONAL = "emotional"
    SENSITIVE = "sensitive"            # touches something private; do not push
    QUESTION_BACK = "question_back"    # the participant asked the moderator something
    CONTRADICTION = "contradiction"    # conflicts with something said earlier
    NOT_UNDERSTOOD = "not_understood"
    WANTS_TO_STOP = "wants_to_stop"
    CRISIS = "crisis"


class LadderLevel(str, Enum):
    """How deep an answer goes: fact -> behaviour -> reason -> emotion -> value (task doc 4.3 追问阶梯)."""

    FACT = "fact"
    BEHAVIOUR = "behaviour"
    REASON = "reason"
    EMOTION = "emotion"
    VALUE = "value"


class Operator(str, Enum):
    """Probe operators; exactly one per turn (task doc 4.3)."""

    CLARIFY = "clarify"            # "您指的是…？"
    SPECIFY = "specify"            # "最近一次是怎么发生的？"
    WHY = "why"
    EMOTION = "emotion"            # "当时感觉如何？"
    IMPACT = "impact"              # "后来呢？如果能改变…"
    CONTRAST = "contrast"
    PROJECTIVE = "projective"      # indirect: "您觉得像您这样的人会…"
    SCALE = "scale"                # 0-10 when answers are too short
    NUDGE = "nudge"                # very short answer: gentle "能多说一点吗？"
    REFLECT = "reflect"            # paraphrase and confirm
    REDIRECT = "redirect"          # back to the topic
    TRANSITION = "transition"      # move to the next prepared question / topic / item
    REPHRASE = "rephrase"          # the participant did not understand
    REASSURE = "reassure"          # emotional: acknowledge, no pressure
    WAIT = "wait"                  # voice version only (silence)


class ControlMode(str, Enum):
    """Who is speaking for the team. HUMAN: a staff member took over and the AI stays silent (red line H11)."""

    AI = "ai"
    HUMAN = "human"


class AlertLevel(str, Enum):
    """Alerts to every staff member online (task doc 5)."""

    NONE = "none"
    YELLOW = "yellow"   # one turn needed the safe fallback sentence
    RED = "red"         # crisis signal, or red lines failed on 2 turns in a row: suggest taking over


class RedLine(str, Enum):
    """Red lines checked before anything is said (task doc 4.5). One violation in any run is a failure."""

    H1_ONE_QUESTION = "H1"
    H2_LEADING = "H2"
    H3_AI_DISCLOSURE = "H3"
    H4_PERSONAL_DATA = "H4"
    H5_STOP_OR_CRISIS = "H5"
    H6_LANGUAGE = "H6"
    H7_EVALUATIVE = "H7"
    H8_CONFIDENTIAL = "H8"
    H9_ADVICE = "H9"
    H10_FORBIDDEN_TOPIC = "H10"
    H11_SILENT_AFTER_TAKEOVER = "H11"
    H12_TOO_LONG = "H12"


class QualityRule(str, Enum):
    """Rules on what the AI says that are not red lines, but are enforced by the same gate (the draft is regenerated)."""

    Q1_REPEAT = "Q1"  # the same question twice in one interview (eval gate: 0 repeats)


REPEAT_SIMILARITY = 0.9  # two AI questions this similar (difflib ratio, spaces ignored) count as a repeat

# What the model is told when a draft of its was rejected: the rule, the exact words, and how to fix it (the first real runs showed
# that a bare rule code was not enough: the same forbidden phrase was written three times in a row).
REJECTION_FIX_HINTS: dict[str, str] = {
    "H1": "write exactly one question with one question mark; no second question, no \"or\" alternatives",
    "H2": "rewrite without those words and without suggesting an answer; Chinese: use 是否 / 有没有 instead of 是不是",
    "H3": "say plainly that you are an AI moderator, not a person, before the question (only if they asked)",
    "H4": "do not ask for any personal data",
    "H5": "stop the interview politely, with no question",
    "H6": "write the whole reply in the interview language",
    "H7": "no praise, agreement or disagreement: start with the question or a neutral 3-word acknowledgement",
    "H8": "do not name the client brand, other participants or invent facts",
    "H9": "no advice of any kind",
    "H10": "do not touch that topic",
    "H12": "each sentence at most 20 English words or 30 Chinese characters: cut it, split it",
    "Q1": "ask a DIFFERENT question: new words and a narrower angle, or the next question from the decision",
}

# Engine limits (task doc 4.2 / 4.4)
MAX_REGENERATIONS = 2          # after a red-line failure; then the safe fallback sentence
MAX_PARSE_RETRIES = 1          # unparsable model output: ask once more, then fall back
RED_ALERT_AFTER_FAILED_TURNS = 2
NUDGE_MAX_CHARS = 15           # task doc: nudge when the answer is shorter than 15 characters
MAX_WORDS_PER_SENTENCE_EN = 25  # H12
MAX_HAN_PER_SENTENCE_ZH = 40    # H12
ZH_MIN_HAN_RATIO = 0.6          # H6: share of Chinese characters among letters in a Chinese reply (brand names are Latin)
DEEP_DIVE_WEIGHTS: dict[str, float] = {"information_gain": 0.30, "emotion": 0.20, "pain_point": 0.20, "contradiction": 0.15,
                                       "business_value": 0.15}
DEEP_DIVE_THRESHOLD = 0.55

# A model server on one of these hosts runs on this computer, so participants' words never leave it.
LOCAL_MODEL_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})

# Request fields a vendor needs for fast, plain answers; applied unless MODERATOR_EXTRA_BODY (a JSON object) says otherwise.
# deepseek-flash "thinks" before answering by default: slower and not useful for an interviewer's short, rule-checked replies.
VENDOR_REQUEST_DEFAULTS: dict[str, dict] = {"api.deepseek.com": {"thinking": {"type": "disabled"}}}

# Model calls: network errors and 5xx answers are retried with exponential backoff (the call has no side effects).
CHAT_RETRY_ATTEMPTS = 3
CHAT_RETRY_FIRST_WAIT_S = 0.5
CHAT_RETRY_MAX_WAIT_S = 4.0
CHAT_DEFAULT_TEMPERATURE = 0.3
HTTP_SERVER_ERROR_MIN = 500
# The model service refused in a way that no retry and no fallback sentence can fix: the run must stop and say why (found when the
# DeepSeek balance ran out mid-evaluation and 4 hours of "results" were fallback sentences).
FATAL_HTTP_REASONS: dict[int, str] = {401: "密钥无效或已失效", 402: "账号余额不足（请到服务商后台充值）", 403: "密钥没有权限", 404: "接口地址或模型名不对"}
TRANSIENT_HTTP_STATUS = frozenset({408, 409, 425, 429})  # timeouts and rate limits: wait and ask again, with backoff
MSG_SERVICE_UNAVAILABLE = "AI 模型服务拒绝了请求（HTTP {status}：{reason}）。请检查 .env 里的 MODERATOR_API_KEY、MODERATOR_MODEL 和账号余额。"
ALERT_SERVICE_UNAVAILABLE = "AI 模型服务不可用（{reason}），已改说安全句，请立即接管并联系管理员"
CHAT_COMPLETIONS_PATH = "/chat/completions"
JSON_RESPONSE_FORMAT: dict[str, str] = {"type": "json_object"}
MOCK_DEFAULT_REPLY: dict[str, str] = {"spoken_response": "Could you tell me a bit more about that?"}

# Texts. Staff-facing ones are Chinese (the research team); participant-facing ones are bilingual like the rest of the join flow.
MSG_CLOUD_VENDOR_MISSING = "AI 主持用的是云端模型，但没有填写供应商名称（.env 里的 MODERATOR_VENDOR），同意书无法告诉受访者数据发给了谁"
MSG_AI_INTERVIEW_NOT_READY = "This interview cannot start yet, please contact the research team / 这场访谈暂时不能开始，请联系研究团队"
MSG_EVERY_CONSENT_NEEDED = "Every item needs your agreement to take part / 每一项都需要你明确同意才能参加"
MSG_UNKNOWN_PROVIDER = "MODERATOR_PROVIDER={value!r}：只支持 mock 或 openai（兼容 OpenAI 接口的服务，含本机 Ollama）"
