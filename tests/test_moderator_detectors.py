"""Rule detectors for stop / crisis / "are you an AI?" / ready. Every miss ever found becomes a permanent case here."""
import pytest

from app.moderator.detectors import detect_asks_if_ai, detect_crisis, detect_ready, detect_stop

ASKS_AI = ["你是真人吗？", "你是不是 AI？", "你是不是AI", "您是 AI 吗", "我是在跟机器人聊天吗？", "你是机器人吗", "是不是 bot 啊", "你是真的人吗",
           "Are you a real person?", "Am I talking to a bot?", "Are you an AI?", "is this a bot", "who am I talking to?"]
NOT_ASKS_AI = ["我在 Shopee 买的", "AI 推荐的商品我不太信", "你们用 AI 做分析吗", "我是真心喜欢这个", "I use an AI app for shopping", "Real reviews matter"]
CRISIS = ["说实话我最近不想活了。", "有时候我真的想伤害自己。", "想自杀", "活着没意思", "I want to end my life", "Sometimes I want to hurt myself", "I don't want to live anymore"]
NOT_CRISIS = ["这个价格要死了", "我快累死了哈哈", "I'm dying to try it", "That price is killing me"]
STOP = ["我不想继续了。", "我想停止这个访谈。", "结束访谈吧。", "不聊了", "I want to stop.", "I don't want to continue.", "Can we stop the interview?"]
NOT_STOP = ["我不会停止用这个牌子", "它让我停不下来", "The bus stop is near the shop", "I can't stop buying it"]
READY = ["好的", "好的，可以开始。", "可以", "没问题，开始吧", "OK", "Sure, let's begin.", "Yes", "ready"]
NOT_READY = ["等一下", "我不想继续了", "这个要多久？", "Wait, how long is this?"]


@pytest.mark.parametrize("text", ASKS_AI)
def test_asks_if_ai(text):
    assert detect_asks_if_ai(text)


@pytest.mark.parametrize("text", NOT_ASKS_AI)
def test_not_asks_if_ai(text):
    assert not detect_asks_if_ai(text)


@pytest.mark.parametrize("text", CRISIS)
def test_crisis(text):
    assert detect_crisis(text)


@pytest.mark.parametrize("text", NOT_CRISIS)
def test_not_crisis(text):
    assert not detect_crisis(text)


@pytest.mark.parametrize("text", STOP)
def test_stop(text):
    assert detect_stop(text)


@pytest.mark.parametrize("text", NOT_STOP)
def test_not_stop(text):
    assert not detect_stop(text)


@pytest.mark.parametrize("text", READY)
def test_ready(text):
    assert detect_ready(text)


@pytest.mark.parametrize("text", NOT_READY)
def test_not_ready(text):
    assert not detect_ready(text)
