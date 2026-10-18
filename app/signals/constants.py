"""Words, emoji and thresholds of the emotion signals. Change them here, not in the code.

These are HINTS about how something was said, to help the team decide where to look or probe. They are never shown to the participant and never
written into an insight as a conclusion: speaking pace, pauses and volume depend on the microphone, the room, the accent and the topic, so every
measure is compared only with the same person's own earlier sentences.
"""

# --- emoji reactions: a participant may send any real emoji from the picker (app/signals/emoji.py); these eight are the quick row shown first ---
QUICK_REACTIONS = ("👍", "❤️", "😊", "😂", "😮", "😕", "😢", "👎")
MAX_REACTIONS_PER_INTERVIEW = 400
MIN_REACTION_GAP_S = 1.0       # one person cannot flood the feed

# --- emoji typed inside a message (a wider set than the buttons) -> tone ---
EMOJI_TONE = {
    "😊": 0.5, "🙂": 0.3, "😀": 0.6, "😃": 0.6, "😄": 0.6, "😁": 0.6, "😍": 0.9, "🥰": 0.9, "😘": 0.7, "👍": 0.6, "👏": 0.6, "❤": 0.9, "❤️": 0.9, "💕": 0.8,
    "🎉": 0.7, "✨": 0.4, "🙏": 0.3, "😂": 0.5, "🤣": 0.5, "😆": 0.5, "😎": 0.4, "👌": 0.4,
    "😕": -0.3, "😟": -0.5, "😞": -0.6, "😢": -0.6, "😭": -0.7, "😠": -0.8, "😡": -0.9, "👎": -0.7, "🙄": -0.5, "😒": -0.5, "😤": -0.6, "😩": -0.6, "😫": -0.6,
    "🤢": -0.8, "🤮": -0.9, "💔": -0.7, "😬": -0.3, "😐": -0.1, "😑": -0.2, "🤔": -0.1,
}

# --- words (a small, deliberately conservative list; tone of the WORDS only, sarcasm is not understood) ---
POSITIVE = {
    "喜欢": 0.6, "很喜欢": 0.8, "爱": 0.5, "好用": 0.7, "好看": 0.6, "不错": 0.5, "很好": 0.6, "太好了": 0.8, "满意": 0.7, "推荐": 0.6, "舒服": 0.6, "清爽": 0.5,
    "划算": 0.6, "值得": 0.6, "惊喜": 0.7, "放心": 0.5, "信任": 0.5, "漂亮": 0.6, "高级": 0.5, "靠谱": 0.6, "棒": 0.6, "完美": 0.8, "愉快": 0.6, "感兴趣": 0.4,
    "like": 0.5, "love": 0.8, "great": 0.7, "good": 0.5, "nice": 0.5, "amazing": 0.8, "perfect": 0.8, "happy": 0.6, "recommend": 0.6, "worth": 0.5, "comfortable": 0.5,
    "excellent": 0.8, "awesome": 0.8, "impressed": 0.7, "satisfied": 0.7, "trust": 0.5, "beautiful": 0.6, "useful": 0.5, "fresh": 0.4, "interested": 0.4, "enjoy": 0.6,
}
NEGATIVE = {
    "讨厌": -0.7, "不喜欢": -0.6, "难用": -0.7, "难看": -0.6, "不好": -0.5, "太贵": -0.6, "失望": -0.8, "后悔": -0.7, "担心": -0.4, "不放心": -0.6, "不信": -0.5,
    "假": -0.5, "骗": -0.7, "过敏": -0.4, "刺痛": -0.6, "油腻": -0.5, "黏": -0.4, "味道重": -0.5, "不舒服": -0.6, "生气": -0.7, "烦": -0.5, "糟糕": -0.7, "垃圾": -0.8,
    "浪费": -0.6, "不值": -0.6, "麻烦": -0.4, "犹豫": -0.3, "怀疑": -0.4, "尴尬": -0.5, "差": -0.4, "失败": -0.6, "不满意": -0.7,
    "hate": -0.8, "dislike": -0.6, "bad": -0.5, "terrible": -0.8, "awful": -0.8, "worse": -0.5, "disappointed": -0.8, "expensive": -0.4, "annoying": -0.6, "annoyed": -0.6,
    "worried": -0.4, "fake": -0.5, "scam": -0.8, "regret": -0.7, "waste": -0.6, "uncomfortable": -0.5, "greasy": -0.5, "sticky": -0.4, "irritat": -0.6, "angry": -0.7,
    "upset": -0.6, "doubt": -0.4, "embarrass": -0.5, "unhappy": -0.6, "rough": -0.3, "pointless": -0.6,
}
NEGATIONS = ("不", "没", "没有", "别", "不是", "不太", "不会", "无", "not", "no", "never", "don't", "doesn't", "didn't", "isn't", "wasn't", "can't", "won't", "hardly")
INTENSIFIERS = ("很", "非常", "特别", "太", "真的", "超", "极其", "十分", "really", "very", "so", "extremely", "absolutely", "totally", "super")
NEGATION_WINDOW = 3            # a negation flips the next few characters (Chinese) / words (other languages)
EXCLAMATION_BOOST = 0.15       # each "!" or "！" (up to three) makes the tone a little stronger
STRONG_NEGATIVE = -0.5         # tags "negative" at or below this
STRONG_POSITIVE = 0.6          # tags "positive" at or above this

# --- speech: always against the speaker's own habits ---
BASELINE_MIN_SENTENCES = 4     # until a person has this many earlier sentences nothing is compared
BASELINE_WINDOW = 40           # the last this many sentences make the baseline
MIN_SENTENCE_UNITS = 5         # characters (Chinese) or words: shorter sentences give no pace
MIN_SENTENCE_MS_FOR_RATE = 1500
SLOW_RATIO = 0.65              # at or below 65% of the usual pace
FAST_RATIO = 1.40
QUIET_DB = 6.0                 # at least this much below the usual level
LOUD_DB = 6.0
VOICED_FLOOR_DB = -60.0        # frames below this are silence (the browser leaves them out)
HESITATION_MIN_MS = 4000       # a pause before answering counts from this length ...
HESITATION_TIMES = 2.5         # ... and when it is this many times the usual pause
HESITATION_MAX_MS = 30_000     # a longer silence is something else (a break, a technical problem), not hesitation
NOTABLE_MIN_TAGS = 2           # a moment is worth a look with a long pause, a strongly negative tone, or this many of slow / quiet / negative

TAG_LABELS = {"hesitation": "回答前停顿长", "slow": "语速偏慢", "fast": "语速偏快", "quiet": "声音偏轻", "loud": "声音偏大",
              "negative": "偏负面", "positive": "偏正面"}
CHAT_SHORT_RATIO = 0.4         # typed answers: much shorter than this person's usual
CHAT_SLOW_RATIO = 2.5          # ... or a reply that took this many times longer than usual to arrive
