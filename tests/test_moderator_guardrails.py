"""Sprint 2: red lines H1-H12. For each red line at least 10 violating and 10 clean sentences, English and Chinese
(task doc 9). Every violation ever found in a run or a pilot is added here as a permanent case (task doc 4.5)."""
import json
import pathlib

import pytest

from app.moderator.constants import ControlMode, RedLine
from app.moderator.guardrails import GuardrailContext, check_silent_after_takeover, check_utterance
from app.moderator.knowledge import InterviewGuide
from app.moderator.templates import TEMPLATES

ZH = InterviewGuide.model_validate(json.loads(pathlib.Path("eval/guides/sg_skincare_zh.json").read_text(encoding="utf-8")))
EN = ZH.model_copy(update={"language": "en", "forbidden_topics": ["politics", "religion", "salary"], "client_brand_names": ["Lumina Skin"]})


def ctx(lang: str, **kw) -> GuardrailContext:
    return GuardrailContext(guide=ZH if lang == "zh" else EN, **kw)


def lines(text: str, c: GuardrailContext) -> set[RedLine]:
    return {v.red_line for v in check_utterance(text, c)}


# (red line, language, sentence, extra context) — must be flagged with that red line
BAD: list[tuple[RedLine, str, str, dict]] = [
    # H1 one question
    *[(RedLine.H1_ONE_QUESTION, "en", s, {}) for s in [
        "Where do you buy skincare? And how often?", "What did you buy, and why?", "How did you find it and what did you think?",
        "Tell me about your routine.", "Do you use toner or why not?", "Which brand do you trust? Which do you avoid?",
        "When did you start, and where do you shop now?", "What happened next? How did you feel?"]],
    *[(RedLine.H1_ONE_QUESTION, "zh", s, {}) for s in [
        "您在哪里买护肤品？多久买一次？", "您从哪里知道这个品牌的，为什么会信任它？", "您用过吗，为什么？", "请介绍一下您的护肤习惯。",
        "您喜欢这个包装吗，价格能接受吗？", "最近一次是什么时候，在哪里买的？", "您买过海外品牌以及国内品牌吗？", "当时怎么想的？后来呢？"]],
    (RedLine.H1_ONE_QUESTION, "zh", "非常感谢您的分享，还有别的吗？", {"is_statement": True}),
    (RedLine.H1_ONE_QUESTION, "en", "Thanks for your time. Anything else?", {"is_statement": True}),
    # H2 leading
    *[(RedLine.H2_LEADING, "en", s, {}) for s in [
        "Don't you think the price is too high?", "Wouldn't you agree that returns matter most?", "Most people prefer local brands; what about you?",
        "A lot of people say halal labels matter, how about you?", "You must have felt worried, how did you cope?", "Surely delivery speed matters to you?",
        "It's expensive, right?", "I'm sure you read reviews first, which ones?", "Isn't it annoying when parcels arrive late?", "Obviously price is key, what else?"]],
    *[(RedLine.H2_LEADING, "zh", s, {}) for s in [
        "您是不是觉得价格太贵了？", "很多人觉得退货很麻烦，您呢？", "您一定很担心买到假货吧？", "难道您不在意认证吗？", "大家都在TikTok上看评价，您也是吗？",
        "这个包装挺好看的，对吧？", "您不觉得配送太慢了吗？", "您应该很看重价格吧？", "这样肯定很方便，您怎么看？", "您也觉得清真认证重要吗？"]],
    # H3 AI disclosure / honesty
    (RedLine.H3_AI_DISCLOSURE, "zh", "您好，欢迎参加今天的访谈。我们现在可以开始吗？", {"is_first_turn": True}),
    (RedLine.H3_AI_DISCLOSURE, "zh", "您好，我是今天的主持人小王。我们现在可以开始吗？", {"is_first_turn": True}),
    (RedLine.H3_AI_DISCLOSURE, "zh", "我是 AI 主持人。我们可以开始了吗？", {"is_first_turn": True}),
    (RedLine.H3_AI_DISCLOSURE, "zh", "我是真人哦，您放心。您平时怎么护肤？", {}),
    (RedLine.H3_AI_DISCLOSURE, "zh", "我不是机器人。您平时怎么护肤？", {}),
    (RedLine.H3_AI_DISCLOSURE, "zh", "这不重要。您平时怎么护肤？", {"participant_asked_ai": True}),
    (RedLine.H3_AI_DISCLOSURE, "zh", "我是研究团队的一员。您平时怎么护肤？", {"participant_asked_ai": True}),
    (RedLine.H3_AI_DISCLOSURE, "zh", "我是人类主持人。您平时怎么护肤？", {}),
    (RedLine.H3_AI_DISCLOSURE, "zh", "我们继续吧。您最近买过什么？", {"participant_asked_ai": True}),
    (RedLine.H3_AI_DISCLOSURE, "zh", "您好，今天由我来主持，不是真人以外的。我们开始吗？", {"is_first_turn": True}),
    (RedLine.H3_AI_DISCLOSURE, "en", "Hello and welcome. Shall we begin?", {"is_first_turn": True}),
    (RedLine.H3_AI_DISCLOSURE, "en", "Hi, I'm Sarah from the research team. Shall we begin?", {"is_first_turn": True}),
    (RedLine.H3_AI_DISCLOSURE, "en", "I'm an AI interviewer. Shall we begin?", {"is_first_turn": True}),
    (RedLine.H3_AI_DISCLOSURE, "en", "Yes, I'm a real person. How do you shop?", {}),
    (RedLine.H3_AI_DISCLOSURE, "en", "I am human, don't worry. How do you shop?", {}),
    (RedLine.H3_AI_DISCLOSURE, "en", "I'm not a bot. How do you shop?", {}),
    (RedLine.H3_AI_DISCLOSURE, "en", "That doesn't matter. How do you shop?", {"participant_asked_ai": True}),
    (RedLine.H3_AI_DISCLOSURE, "en", "I'm part of the research team. How do you shop?", {"participant_asked_ai": True}),
    (RedLine.H3_AI_DISCLOSURE, "en", "Let's keep going. What did you buy last?", {"participant_asked_ai": True}),
    (RedLine.H3_AI_DISCLOSURE, "en", "I am a person on the team. How do you shop?", {}),
    # H4 personal data
    *[(RedLine.H4_PERSONAL_DATA, "en", s, {}) for s in [
        "What's your full name?", "Could you tell me your phone number?", "May I have your email address?", "Can you share your home address with me?",
        "What is your NRIC number?", "Please provide your passport number.", "Could you give me your bank account details?", "Where do you live?",
        "What should I call you?", "Could you share your card number?"]],
    *[(RedLine.H4_PERSONAL_DATA, "zh", s, {}) for s in [
        "请问您叫什么名字？", "方便留一下您的手机号吗？", "您的邮箱是多少？", "您住在哪个区？", "请提供一下您的身份证号。", "您的微信号是什么？",
        "能告诉我您的真实姓名吗？", "麻烦您发给我银行卡号。", "您的住址在哪里？", "您的账号是多少？"]],
    # H5 stop / crisis
    *[(RedLine.H5_STOP_OR_CRISIS, lang, s, {"participant_wants_stop": True}) for lang, s in [
        ("en", "Sure. Before you go, what did you buy last?"), ("en", "Okay, one last question: why did you choose it?"), ("en", "Can we continue for five minutes?"),
        ("en", "Let's go on, how often do you shop?"), ("en", "Are you sure you want to stop?"),
        ("zh", "好的，走之前最后问一个：您最近买了什么？"), ("zh", "我们再聊五分钟可以吗？"), ("zh", "您确定要停止吗？"), ("zh", "先别走，您多久买一次？"),
        ("zh", "理解。那您为什么不想继续？")]],
    *[(RedLine.H5_STOP_OR_CRISIS, lang, s, {"participant_in_crisis": True, "is_statement": True}) for lang, s in [
        ("en", "I'm sorry to hear that. Let's pause here."), ("en", "Thank you for sharing. We will stop now."), ("zh", "听到这个很难过，我们先暂停。"),
        ("zh", "谢谢您告诉我，访谈先到这里。"), ("zh", "我们先停一下，请照顾好自己。"), ("en", "Please take care of yourself.")]],
    (RedLine.H5_STOP_OR_CRISIS, "zh", "听到这个我很担心。您愿意说说发生了什么吗？", {"participant_in_crisis": True}),
    (RedLine.H5_STOP_OR_CRISIS, "en", "I'm worried. What happened today?", {"participant_in_crisis": True}),
    # H6 language
    *[(RedLine.H6_LANGUAGE, "zh", s, {}) for s in [
        "What do you usually buy?", "How often do you shop online?", "Could you tell me more about that?", "Which platform do you use most?",
        "Apa yang anda beli?", "What did you think of the TikTok review?", "Why did you return it?", "Tell me more about Shopee and Lazada please?",
        "When did you last buy skincare?", "Who recommended it to you?"]],
    *[(RedLine.H6_LANGUAGE, "en", s, {}) for s in [
        "您平时买什么？", "How often do you 买护肤品?", "Could you tell me 更多 about that?", "What did you think, 为什么?", "你好, which platform?",
        "退货 was easy?", "What is 清真认证 to you?", "How did you feel 当时?", "Which 品牌 do you trust?", "Why 不买?"]],
    # H7 evaluative
    *[(RedLine.H7_EVALUATIVE, "en", s, {}) for s in [
        "Great point! What else matters?", "Good answer. Why do you think so?", "I agree with you, how did it start?", "That's right, what happened next?",
        "Exactly. Why that brand?", "I also think so; what else?", "You're right about that, why?", "Well said, what else?", "That's not true; why do you think so?",
        "Interesting! Why?"]],
    *[(RedLine.H7_EVALUATIVE, "zh", s, {}) for s in [
        "说得好！还有什么原因？", "我也这么觉得，您为什么这样想？", "我同意，后来呢？", "没错，那您怎么处理的？", "太棒了，您还会再买吗？", "有道理，您是怎么发现的？",
        "很好，您能再说说吗？", "您说得不对，为什么这么想？", "确实如此，后来怎样了？", "非常好，还有呢？"]],
    # H8 confidential
    *[(RedLine.H8_CONFIDENTIAL, "en", s, {}) for s in [
        "Have you tried Lumina Skin?", "What do you think of LUMINA SKIN's packaging?", "Other participants mentioned price; what about you?",
        "Another participant said returns are hard. How do you see it?", "Someone else said the same, why?", "The previous participant loved it; how about you?",
        "Other people we talked to prefer Shopee; where do you buy?", "Would you buy from lumina skin?", "Other interviewees liked it; why?", "Another respondent disagreed; what do you think?"]],
    *[(RedLine.H8_CONFIDENTIAL, "zh", s, {}) for s in [
        "您用过 Lumina Skin 吗？", "其他受访者提到价格，您怎么看？", "另一位受访者说退货很难，您觉得呢？", "之前有人说包装很好，您怎么看？", "上一位参与者很喜欢，您呢？",
        "别的受访者都在 Shopee 买，您在哪里买？", "您觉得 lumina skin 的价格怎么样？", "之前的受访者提到了认证，您在意吗？", "另外一位被访者说过类似的，您为什么这么想？",
        "别人跟我说配送很慢，您遇到过吗？"]],
    # H9 advice
    *[(RedLine.H9_ADVICE, "en", s, {}) for s in [
        "You should see a dermatologist. What happened next?", "I recommend a gentle cleanser; what do you use?", "My advice is to check the label. What do you check?",
        "You'd better return it. Did you?", "Consult a lawyer about it. What did the seller say?", "I suggest you invest in quality. What do you buy?",
        "I would recommend Shopee. Where do you shop?", "It's best to use sunscreen daily. Do you?", "You should invest in index funds; what else?", "See a doctor about it. How do you feel?"]],
    *[(RedLine.H9_ADVICE, "zh", s, {}) for s in [
        "我建议您去看医生，后来呢？", "您应该先做皮肤测试，您做过吗？", "我推荐用温和的洗面奶，您用什么？", "建议您去咨询律师，商家怎么说？", "最好去正规渠道买，您在哪里买？",
        "推荐您试试这个牌子，您觉得呢？", "可以考虑买点基金，您平时怎么理财？", "您得去看皮肤科，严重吗？", "建议您用防晒，您每天用吗？", "最好用无香料的，您在意成分吗？"]],
    # H10 forbidden topics
    *[(RedLine.H10_FORBIDDEN_TOPIC, "zh", s, {}) for s in [
        "您对政治有什么看法？", "这和您的宗教信仰本身的评价有关吗？", "您的收入具体数字是多少？", "政治因素会影响您买吗？", "聊聊政治吧，您关心吗？",
        "您怎么看政治？", "关于宗教信仰本身的评价，您怎么想？", "能说说收入具体数字吗？", "政治新闻会影响您吗？", "您觉得政治和购物有关吗？"]],
    *[(RedLine.H10_FORBIDDEN_TOPIC, "en", s, {}) for s in [
        "How do politics affect your shopping?", "Does religion shape what you buy?", "What is your salary range?", "Do you follow politics?", "Is religion important here?",
        "Would your salary change that?", "How does Politics come in?", "Tell me about your religion and skincare?", "Does your salary allow it?", "Any views on politics?"]],
    # H11 silent after take-over
    *[(RedLine.H11_SILENT_AFTER_TAKEOVER, lang, s, {"control": ControlMode.HUMAN}) for lang, s in [
        ("en", "Could you tell me more?"), ("en", "Thanks."), ("en", "What happened next?"), ("en", "Why?"), ("en", "Shall we go on?"),
        ("zh", "能再说说吗？"), ("zh", "谢谢。"), ("zh", "后来呢？"), ("zh", "为什么呢？"), ("zh", "我们继续吗？")]],
    # H12 too long
    *[(RedLine.H12_TOO_LONG, "en", s, {}) for s in [
        "When you think back over the last few months of buying skincare products online from different platforms and shops, what is the one thing that stands out most?",
        "I'd like to understand in as much detail as you are comfortable sharing how you usually go about choosing between a local brand and an overseas brand you have never tried?",
        "Could you walk me through, step by step and from the very beginning to the very end, the last time you bought something online that you had never bought before?",
        "Thinking about all the reviews, videos, friends' opinions, ads and store visits that might shape your choices, which of these matters most to you and in what way exactly?",
        "If you imagine yourself standing in a shop, looking at two similar moisturisers at a similar price, one local and one overseas, which one would you pick in the end?",
        "Let us talk a little about delivery, returns, packaging, certification, ingredients, price and brand reputation, and how each of these might influence what you decide to buy?",
        "Over the years you have probably tried many different products from many different brands, so which experience do you remember best and what made it so memorable for you?",
        "When a product you bought online does not meet your expectations at all and you have to decide whether to return it or keep it, what usually goes through your mind then?",
        "How would you describe the whole process from first hearing about a new skincare brand to finally deciding to buy it, including every step where you might have hesitated?",
        "Please tell me about the people around you, such as friends, family and colleagues, whose opinions about skincare products you listen to most often and why that is the case?"]],
    *[(RedLine.H12_TOO_LONG, "zh", s, {}) for s in [
        "回想一下过去几个月您在不同平台和不同商店购买护肤品的经历，其中最让您印象深刻的一件事是什么？",
        "我想尽可能详细地了解一下您在本地品牌和从来没有用过的海外品牌之间通常是怎么一步一步做出选择的？",
        "能不能请您从头到尾一步一步地给我讲讲您最近一次在网上买一个以前从来没有买过的护肤产品的完整过程？",
        "在评价视频朋友意见广告和实体店这么多可能影响您选择的因素当中，对您来说最重要的究竟是哪一个呢？",
        "如果您站在商店里面看着两款价格差不多功效也差不多的面霜一款是本地的一款是海外的您最后会选哪一款？",
        "我们来聊聊配送退货包装认证成分价格和品牌口碑这些方面各自会怎样影响您最终决定买还是不买的过程吧？",
        "这些年来您可能已经试过很多不同品牌的很多不同产品，其中哪一次的经历让您记得最清楚以及为什么会这样呢？",
        "当您在网上买到的一款产品完全没有达到您的预期而您必须决定是退货还是留着自己用的时候您通常会怎么想？",
        "您能不能描述一下从第一次听说一个新的护肤品牌一直到最终决定购买的整个过程包括每一个可能犹豫的环节？",
        "请您讲讲身边的朋友家人和同事当中您最常听取谁关于护肤产品的意见以及为什么他们的意见对您来说最重要？"]],
]

# clean sentences: no red line at all (the normal moderator voice), with the context they are said in
GOOD: list[tuple[str, str, dict]] = [
    *[("en", s, {}) for s in [
        "Could you tell me a bit more about that?", "What happened the last time you bought a new brand?", "How did you feel when the parcel arrived late?",
        "Why did you decide to return it?", "What exactly did you buy?", "Where do you usually hear about new skincare brands?",
        "You mentioned TikTok reviews. What makes a review trustworthy for you?", "On a scale from 0 to 10, how much does halal certification matter to you?",
        "If a friend were choosing between two brands, what would they look at first?", "Let's move on to delivery. How fast does it need to be?",
        "So price matters when you try something new; did I understand that correctly?", "Could you share your experience with your last purchase?",
        "What does a good return policy look like to you?", "How do you compare local and overseas brands?", "Did you try using it again later?"]],
    *[("zh", s, {}) for s in [
        "关于这一点，您能再多说一些吗？", "最近一次买新品牌是什么时候？", "包裹晚到的时候，您当时感觉怎么样？", "您为什么决定退货呢？", "您一般从哪里知道新的护肤品牌？",
        "您提到了 TikTok 上的评价，什么样的评价让您觉得可信？", "如果用 0 到 10 分，清真认证对您有多重要？", "我们聊聊配送吧，您希望多快送到？",
        "您的意思是价格会影响第一次尝试，我理解得对吗？", "您平时在手机上用什么 App 买护肤品？", "还有什么重要的事，是我们今天没聊到的吗？",
        "对您来说，好的退货政策是什么样的？", "您会怎么比较本地品牌和海外品牌？", "当时发生了什么，让您改变了主意？", "您觉得品牌应该怎么做，才会让您更放心？"]],
]
GOOD_WITH_CONTEXT: list[tuple[str, str, dict]] = [
    ("zh", TEMPLATES["zh"].intro.format(topic="护肤品选购"), {"is_first_turn": True}),
    ("en", TEMPLATES["en"].intro.format(topic="skincare shopping"), {"is_first_turn": True}),
    ("zh", TEMPLATES["zh"].ai_answer + "您平时怎么护肤？", {"participant_asked_ai": True}),
    ("en", TEMPLATES["en"].ai_answer + " How do you usually shop?", {"participant_asked_ai": True}),
    ("zh", TEMPLATES["zh"].stop, {"participant_wants_stop": True, "is_statement": True}),
    ("en", TEMPLATES["en"].stop, {"participant_wants_stop": True, "is_statement": True}),
    ("zh", TEMPLATES["zh"].crisis, {"participant_in_crisis": True, "is_statement": True}),
    ("en", TEMPLATES["en"].crisis, {"participant_in_crisis": True, "is_statement": True}),
    ("zh", TEMPLATES["zh"].closing, {"is_statement": True}),
    ("en", TEMPLATES["en"].closing, {"is_statement": True}),
    ("zh", TEMPLATES["zh"].wrapup, {}), ("en", TEMPLATES["en"].wrapup, {}),
    ("zh", TEMPLATES["zh"].followup, {}), ("en", TEMPLATES["en"].followup, {}),
    ("zh", TEMPLATES["zh"].missed_item.format(item="退换货与售后"), {}), ("en", TEMPLATES["en"].missed_item.format(item="returns and after-sales"), {}),
]


@pytest.mark.parametrize("line, lang, text, extra", BAD, ids=[f"{b[0].value}-{b[1]}-{i}" for i, b in enumerate(BAD)])
def test_violations_are_caught(line, lang, text, extra):
    assert line in lines(text, ctx(lang, **extra)), f"{line.value} missed: {text}"


@pytest.mark.parametrize("lang, text, extra", GOOD + GOOD_WITH_CONTEXT, ids=[f"{g[0]}-{i}" for i, g in enumerate(GOOD + GOOD_WITH_CONTEXT)])
def test_clean_sentences_pass(lang, text, extra):
    assert lines(text, ctx(lang, **extra)) == set(), text


def test_every_red_line_has_ten_violations_and_ten_clean_cases_per_language():
    for line in RedLine:
        for lang in ("en", "zh"):
            assert sum(1 for b in BAD if b[0] is line and b[1] == lang) >= 5, f"{line.value} {lang}: too few violating cases"
        assert sum(1 for b in BAD if b[0] is line) >= 10, f"{line.value}: fewer than 10 violating cases"
    for lang in ("en", "zh"):
        assert sum(1 for g in GOOD + GOOD_WITH_CONTEXT if g[0] == lang) >= 10  # clean cases pass every red line at once


def test_brand_may_be_named_when_the_project_allows_it():
    allowed = ZH.model_copy(update={"brand_disclosure_allowed": True})
    assert lines("您用过 Lumina Skin 吗？", GuardrailContext(guide=allowed)) == set()


def test_silence_after_takeover_is_fine():
    for lang in ("en", "zh"):
        assert check_silent_after_takeover("", ctx(lang, control=ControlMode.HUMAN)) == []
        assert check_silent_after_takeover("后来呢？", ctx(lang, control=ControlMode.AI)) == []


def test_reassurance_about_personal_data_is_not_a_request():
    assert lines("我们不会问您的姓名或电话。您平时怎么护肤？", ctx("zh")) == set()
    assert lines("We won't ask for your name or phone number. How do you shop?", ctx("en")) == set()
