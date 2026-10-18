"""Numbers and word lists of the speaking-fairness dashboard. Change them here, not in compute.py."""

MODERATOR_KEY = "moderator"

# --- what counts as one turn / one overlap ---
TURN_GAP_MS = 3000            # two sentences of the same speaker less than this apart are one turn
OVERLAP_MIN_MS = 1500         # two people talking at once for at least this long is an interruption. A sentence starts "end minus length", and the two
                              # browsers' recognisers answer with different delays, so back-to-back speakers can look 1 s apart in time: stay above that
UNKNOWN_DURATION_CHARS_PER_S = 4.0   # Chinese; when the recogniser gave no duration the sentence length stands in for it
UNKNOWN_DURATION_WORDS_PER_S = 2.5   # other languages
MIN_SENTENCE_MS = 600         # a sentence is never shorter than this

# --- who is quiet, who talks too much ---
SILENT_YELLOW_S = 240         # no sentence for this long: yellow
SILENT_RED_S = 480            # ... and red
HIGH_SHARE_MIN = 0.40         # a share above max(this, HIGH_SHARE_TIMES / people) is "too much" ...
HIGH_SHARE_TIMES = 1.8
LOW_SHARE_TIMES = 0.35        # ... and below this / people "too little" (judged only once there is something to compare)
JUDGE_AFTER_TALK_MS = 180_000  # share judgements start once the participants have talked this long in total
JUDGE_AFTER_ELAPSED_MS = 300_000   # a participant who has said nothing is called silent after this much interview time

# --- the fairness index ---
FAIRNESS_MIN_TALK_MS = 60_000        # below this much participant talk the index is not shown (too early to mean anything)
FAIRNESS_SERIES_STEP_MS = 120_000    # one point of the curve every two minutes
FAIRNESS_GOOD = 0.75          # at or above: 均衡
FAIRNESS_FAIR = 0.5           # at or above: 偏集中; below: 严重偏集中

# --- informative sentences (a rough, rule-based measure of "said something", not of quality) ---
INFORMATIVE_MIN_CHARS = 8     # Chinese
INFORMATIVE_MIN_WORDS = 4     # other languages
FILLERS = frozenset({
    "嗯", "哦", "啊", "对", "是", "是的", "好", "好的", "行", "可以", "同意", "没错", "对的", "对对", "对对对", "嗯嗯", "哈哈", "哈哈哈", "不知道",
    "yes", "yeah", "yep", "ok", "okay", "right", "sure", "agree", "i agree", "mm", "mhm", "hmm", "uh", "um", "uh huh", "true", "exactly", "same", "me too",
})

SPARK_BUCKETS = 5             # the small chart shows the last five minutes, one bar per minute
SPARK_BUCKET_MS = 60_000
MAX_HINTS = 3
