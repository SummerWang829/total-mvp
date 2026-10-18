"""The rule library for what participants type or say: contact details, abuse, hate, threats, sexual harassment.

One list for every project (the research team does not have to configure anything). It is a starting point written by the developers, to be
extended by the operations team without touching code: see ``EXTRA_FILE`` below. Policy in one place:

- contact details  -> hidden in everything stored and shown ("[联系方式已隐藏]"), never sent to the AI model; the team is told.
- abuse, sexual    -> the words are replaced by "***"; the team is told.
- hate, threats    -> the words are replaced by "***"; the team is told at once as a serious matter.
- mild swearing ("damn", "他妈的", "shit") is left as it is: in market research it is often honest, useful emphasis about a product.

A word is only in a list if it is hardly ever innocent. Words that are everyday in skincare talk or have a harmless meaning ("牛逼", "ass" inside
"class", "title", "cockpit") are deliberately absent and the "exact" entries never match inside a longer word.
"""

# --- categories and what they mean for the team ---
CONTACT = "contact"
ABUSE = "abuse"
HATE = "hate"
THREAT = "threat"
SEXUAL = "sexual"
CATEGORIES = (CONTACT, ABUSE, HATE, THREAT, SEXUAL)
SEVERE = frozenset({HATE, THREAT})          # the team is told at once
CONDUCT = frozenset({ABUSE, SEXUAL, HATE, THREAT})   # everything that is about how someone speaks (not about private data)

MASK_WORD = "***"
MASK_CONTACT = {"zh": "[联系方式已隐藏]", "en": "[contact hidden]"}
STRIKES_BEFORE_ENDING = 3   # the AI interviewer ends the chat politely after this many messages with abuse or hate (contact details do not count)
EXTRA_FILE_ENV = "CONTENT_RULES_EXTRA"   # a text file, one rule per line: "category:term"  (e.g.  hate:some new slur ), "#" starts a comment

# --- stems: matched anywhere in a word (a stem is hardly ever innocent) ---
STEMS = {
    ABUSE: (
        # English
        "fuck", "motherfuck", "bitch", "whore", "slut", "bastard", "asshole", "arsehole", "dickhead", "shithead", "wanker", "twat", "dumbass", "jackass",
        # Chinese
        "傻逼", "傻比", "傻屄", "煞笔", "沙比", "傻叉", "傻x", "操你妈", "草你妈", "艹你妈", "操你", "肏", "日你妈", "干你娘", "妈逼", "你妈逼", "你妈死了", "你妈的",
        "狗日的", "狗娘养的", "王八蛋", "畜生", "贱人", "贱货", "婊子", "骚货", "鸡巴", "屁眼", "脑残", "智障", "弱智", "白痴", "去你妈的", "滚你妈",
        # Singapore / Malay / Tamil (romanised and script)
        "cheebye", "chee bye", "pukimak", "puki mak", "lanjiao", "lan jiao", "kimak", "bangsat", "sundal", "lancau",
        "thevidiya", "thevdiya", "koothi", "ஓத்தா", "தேவடியா", "புண்டை", "கூதி", "சுன்னி",
    ),
    HATE: (
        "nigger", "nigga", "faggot", "kike", "wetback", "towelhead", "raghead", "tranny", "chinaman", "ching chong",
        "黑鬼", "黑奴", "支那", "小日本", "日本鬼子", "死基佬", "臭同性恋", "阿三", "ah tiong", "keling", "cina babi", "china babi", "babi cina", "华猪", "马来猪", "穆畜",
    ),
    THREAT: (
        "杀了你", "弄死你", "砍死你", "打死你", "杀你全家", "死全家", "全家死光", "去死吧", "我会找到你", "kill yourself",
    ),
    SEXUAL: (
        "blowjob", "handjob", "cumshot", "jerk off", "masturbat", "send nudes", "nude pics", "口交", "打飞机", "裸照", "约炮", "做爱吗",
    ),
}

# --- exact words: only when the whole word stands alone (an optional plural "s" is allowed) ---
EXACT = {
    ABUSE: ("cunt", "otha", "kanina", "dick", "cock", "pussy", "prick", "arse", "retard", "retarded", "spastic", "cnm", "nmsl", "wdnmd", "mlgb", "nmb", "cnmb", "knn"),   # ("JB" is Johor Bahru: left out)
    HATE: ("chink", "gook", "spic", "paki", "dyke", "negro"),   # ("coon" is left out: Maine Coon cats)
    THREAT: ("kys",),
    SEXUAL: ("porn", "nudes", "sexting"),
}

# --- patterns that are not single words ---
THREAT_PATTERNS = (
    r"\b(?:i(?:'ll| will| am going to|'m going to|'m gonna)|we(?:'ll| will)|gonna)\s+(?:kill|murder|hurt|beat|stab|shoot|rape)\s+(?:you|u|him|her|them)\b",
    r"\bgo\s+die\b", r"\b(?:i|we)(?:'ll| will)\s+find\s+(?:you|where you live)\b",
)

# --- contact details ---
EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
URL = r"(?:https?://|www\.)[^\s<>\"']+"
PHONE_PATTERNS = (
    r"(?<!\d)1[3-9]\d[\s-]?\d{4}[\s-]?\d{4}(?!\d)",                      # China mobile
    r"(?<!\d)(?:\+?65[\s-]?)?[3689]\d{3}[\s-]?\d{4}(?!\d)",              # Singapore
    r"(?<!\d)\+\d{1,3}[\s-]?\d[\d\s-]{6,14}\d(?!\d)",                    # any international number
    r"(?<!\d)(?:\d[\s-]?){10,14}\d(?!\d)",                               # a long run of digits
    r"[零一二三四五六七八九幺〇]{8,}",                                     # a number said in Chinese digits
    r"\b(?:(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)\b[\s,.-]*){8,}",   # a number said in English words
)
# a social-media cue followed by an account name. Bare cues that are also ordinary words ("line", "ig", "fb") only count with an explicit
# "id" / "号" / colon, so "line extension" or "the fb page" is never hidden.
_HANDLE = r"@?[A-Za-z0-9][A-Za-z0-9_\-.]{3,}"
STRONG_CUES = r"(?:微信号?|vx|v信|薇信|wechat|weixin|whatsapp|whats app|telegram|instagram|insta|facebook|tiktok|小红书号?|抖音号?|qq|扣扣|snapchat)"
WEAK_CUES = r"(?:line|ig|fb|wx|tg|signal)"
SOCIAL_PATTERNS = (
    r"(?:" + STRONG_CUES + "|" + WEAK_CUES + r")\s*(?:号|id|账号|帐号|account)\s*[:：=是为]?\s*" + _HANDLE,   # "wechat id: abc123", "微信号 abc123"
    r"(?:" + STRONG_CUES + "|" + WEAK_CUES + r")\s*[:：=是为]\s*" + _HANDLE,                                   # "wechat: abc123", "微信：abc123"
    r"(?<![A-Za-z])" + STRONG_CUES + r"\s+@?(?=[A-Za-z_.\-]*\d)" + _HANDLE,                                   # "wechat abc123" (the name has a digit)
    # asking to move to another channel, with the account name if one follows ("加我微信 abc12345", "add me on wechat: abc")
    r"(?:加|私聊|联系|找|add|dm|message|text|call|contact)\s*(?:我|me)?\s*(?:一下)?\s*(?:on|at)?\s*" + STRONG_CUES
    + r"(?:\s*[:：=是为]\s*" + _HANDLE + r"|\s+@?(?=[A-Za-z_.\-]*\d)" + _HANDLE + r")?(?![A-Za-z])",
)
AT_HANDLE = r"(?<![\w@])@[A-Za-z0-9_.]{4,}"

# --- spelling tricks: "f u c k", "f*ck", "sh1t", fullwidth letters ---
LEET = {"a": "[a@4àáâä]", "e": "[e3éèêë]", "i": "[i1!|íìîï]", "o": "[o0óòôö]", "u": "[uüùúû*]", "s": "[s$5]", "l": "[l1|]", "t": "[t7+]", "b": "[b8]"}
LETTER_GAP = r"[\s._\-*]{0,2}"
HAN_GAP = r"[\s._\-*·]{0,2}"
ZERO_WIDTH = "​‌‍⁠﻿­"
