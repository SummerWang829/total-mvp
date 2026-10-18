"""Limits and word lists of the cue cards. Change them here, not in the rule code."""
from enum import Enum


class Kind(str, Enum):
    """The kinds of card, in the order they are named on screen."""

    COMPLIANCE = "compliance"
    DOMINANT = "dominant"
    SILENT = "silent"
    TIME = "time"
    OFFTOPIC = "offtopic"


class Status(str, Enum):
    """Where a card is in its life."""

    PENDING = "pending"
    USED = "used"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


PRIORITY = {Kind.COMPLIANCE: 90, Kind.DOMINANT: 70, Kind.SILENT: 60, Kind.TIME: 40, Kind.OFFTOPIC: 30}

# --- the cards must never become noise ---
MIN_GAP_S = 45                 # at most one new card every 45 seconds (a card about personal data may come after MIN_GAP_COMPLIANCE_S)
MIN_GAP_COMPLIANCE_S = 10
MAX_PENDING = 3                # while three cards wait unanswered, no new one is made
EXPIRE_S = 300                 # an unanswered card goes away after five minutes
QUIET_AFTER_MODERATOR_S = 8    # no card in the seconds right after the moderator spoke (he or she is mid-question); personal-data cards ignore this
COOLDOWN_S = {Kind.COMPLIANCE: 0, Kind.DOMINANT: 300, Kind.SILENT: 300, Kind.TIME: 180, Kind.OFFTOPIC: 300}   # same kind again, at the earliest
SEAT_COOLDOWN_S = 300          # the same person is named by a card at most once in five minutes
NOISY_MIN_HANDLED = 3          # a kind the moderator mostly dismisses (at least this many answered, this share dismissed) waits twice as long
NOISY_DISMISS_SHARE = 0.75
SHOW_HANDLED = 6               # how many answered cards stay visible below the waiting ones
EVIDENCE_PER_CARD = 2
EVIDENCE_CHARS = 200

# --- time ---
OVERRUN_SHARE = 0.20           # a section is "over" when it has used this much more than planned ...
OVERRUN_MIN_S = 60             # ... and at least this many seconds more
TOTAL_OVERRUN_SHARE = 0.15

# --- off topic ---
OFFTOPIC_SENTENCES = 3         # this many informative sentences in a row without touching the section

# --- personal data (rule words; a hint to be careful, never a verdict) ---
# Deliberately narrow: in skincare research "过敏", "激素", "收入" are everyday product talk and would make the card cry wolf all the time.
HEALTH_WORDS = ("癌", "化疗", "抑郁", "焦虑症", "病史", "确诊", "手术", "住院", "怀孕", "孕期", "哺乳",
                "cancer", "chemo", "depress", "diagnosed", "surgery", "pregnan", "hospitalis", "hospitaliz")
MONEY_WORDS = ("存款", "负债", "欠债", "贷款", "破产", "月薪", "年薪",
               "my salary", "in debt", "bankrupt", "my loan", "my mortgage")
PHONE_PATTERN = r"(?<!\d)(?:\+?65[\s-]?)?[689]\d{3}[\s-]?\d{4}(?!\d)|(?<!\d)1[3-9]\d{9}(?!\d)"
EMAIL_PATTERN = r"[\w.+-]+@[\w-]+\.[\w.-]+"
