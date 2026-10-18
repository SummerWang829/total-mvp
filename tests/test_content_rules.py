"""The rule library for what participants type or say: it hides what must not stay in the record, and leaves ordinary answers alone."""
import time

import pytest

from app import content_rules as cr
from app.content_rules import constants as K


def flags(text):
    return set(cr.check(text).flags)


# ---------- contact details ----------
@pytest.mark.parametrize("text", [
    "打我电话 13812345678", "我手机 138 1234 5678", "call me on 9123 4567", "my number is +65 9123 4567", "whatsapp me +60 12-345 6789",
    "email me at lin.tan@example.com", "发到 zhang_wei@qq.com 吧", "visit https://shopee.sg/x?id=1", "see www.lazada.com/abc",
    "加我微信 abc12345", "我的微信号：zhang123456", "my wechat id: john_tan88", "wechat: lin88", "微信 lin8888 聊", "我 qq 12345678", "ig: @mei.skin",
    "my instagram is @mei_skin88", "加我微信", "add me on wechat", "私聊我 vx",
    "九一二三四五六七八", "nine one two three four five six seven eight",
])
def test_contact_details_are_found(text):
    assert K.CONTACT in flags(text), text


@pytest.mark.parametrize("text", [
    "I like the line extension", "the fb page was down", "价格是 9000 到 12000", "我今年 35 岁，预算 5000 块", "order number 20261018123456789",
    "I trip to JB every month", "这个精华 7 天见效，贵了 30 块", "1 到 5 分，我打 4 分", "在微信上看到的广告", "I saw it on Instagram", "我在小红书上看到过",
    "my wechat is slow today", "the line was long", "一千二百三十五块", "I paid 128.50 for it",
])
def test_ordinary_numbers_and_platform_talk_are_not_contact_details(text):
    assert flags(text) == set(), text


def test_the_account_name_after_a_request_to_move_channel_is_hidden_with_it():
    r = cr.check("好的，加我微信 abc12345 聊，我晚上在线")
    assert "abc12345" not in r.text and r.text.startswith("好的，") and r.text.endswith("聊，我晚上在线")
    assert "abc_01" not in cr.check("add me on wechat: abc_01 tonight").text and "tonight" in cr.check("add me on wechat: abc_01 tonight").text


def test_a_contact_detail_is_replaced_and_the_rest_kept():
    r = cr.check("好的，你可以打我电话 13812345678 或者发邮件 lin@example.com，我都在")
    assert "13812345678" not in r.text and "lin@example.com" not in r.text and r.text.count("[联系方式已隐藏]") == 2 and r.text.startswith("好的，你可以打我电话")
    assert cr.check("call me on 9123 4567", "en").text == "call me on [contact hidden]"


# ---------- abuse, hate, threats, sexual ----------
@pytest.mark.parametrize("text", [
    "你这个傻逼", "傻 逼", "傻.逼广告", "what the fuck", "f u c k this", "f*ck you", "fucking mess", "you bitch", "asshole!", "操你妈的", "你妈死了", "nmsl", "脑残设计",
    "智障广告", "pukimak lah", "kanina", "cheebye", "你个畜生", "motherfucker", "F U C K", "ｆｕｃｋ",
])
def test_abuse_is_found_and_masked(text):
    r = cr.check(text)
    assert K.ABUSE in r.flags and "***" in r.text and r.conduct and not r.severe, text


@pytest.mark.parametrize("text", ["你们这帮黑鬼", "he is a nigger", "fucking faggot", "chink", "死基佬", "all those ah tiong", "keling", "支那人", "小日本"])
def test_hate_is_found_masked_and_severe(text):
    r = cr.check(text)
    assert K.HATE in r.flags and r.severe and "***" in r.text, text


@pytest.mark.parametrize("text", ["我要杀了你", "我会找到你的", "go die", "I will kill you", "I'll find you", "kill yourself", "你去死吧", "kys"])
def test_threats_are_found_masked_and_severe(text):
    r = cr.check(text)
    assert K.THREAT in r.flags and r.severe, text


@pytest.mark.parametrize("text", ["send nudes", "blowjob", "约炮吧"])
def test_sexual_harassment_is_found(text):
    assert K.SEXUAL in flags(text)


@pytest.mark.parametrize("text", [
    "这个精华牛逼", "太牛逼了，真的好用", "I went to the class on Tuesday", "the cockpit of the plane", "a classic pass", "the title is fine", "assess the results",
    "他妈的太贵了", "damn this is expensive", "this is crap", "shit, I forgot", "卧槽这么贵", "hell no", "你好", "Scunthorpe", "peacock feathers", "Maine Coon cat",
    "我老公是做白色家电的", "我很喜欢这款，质地清爽", "skys the limit", "it killed my skin barrier", "this cream will kill the bacteria", "我要杀价，太贵了",
    "牛B",
])
def test_everyday_and_mild_words_are_left_alone(text):
    r = cr.check(text)
    assert r.text == text and r.clean, text


def test_a_plain_insult_word_is_masked_even_inside_an_ordinary_sentence():
    assert cr.check("my dick of a boss told me").text == "my *** of a boss told me"   # on purpose: better one false alarm than an insult in the record


def test_mild_swearing_is_not_touched_because_it_is_honest_emphasis_about_a_product():
    r = cr.check("他妈的，这个价格也太离谱了，damn")
    assert r.clean and "他妈的" in r.text and "damn" in r.text


# ---------- how the text is handled ----------
def test_clean_text_comes_back_as_the_very_same_string():
    s = "我平时先看成分，再看价格，最后问朋友。"
    assert cr.check(s).text is s


def test_zero_width_characters_and_full_width_letters_do_not_slip_past():
    assert K.ABUSE in flags("fu​ck")        # a zero-width space inside the word
    assert K.ABUSE in flags("ｆｕｃｋ")                 # full-width letters
    assert K.CONTACT in flags("ｅｍａｉｌ ｌｉｎ＠ｅｘａｍｐｌｅ．ｃｏｍ".replace("＠", "@").replace("．", "."))


def test_the_flags_of_a_message_with_several_problems_are_all_reported():
    r = cr.check("傻逼，打我电话 13812345678，不然我会找到你")
    assert r.flags == {K.ABUSE, K.CONTACT, K.THREAT} and r.severe


def test_a_long_text_is_checked_fast():
    best = 9.0
    for _ in range(3):  # best of three: a busy machine (a full qa run) must not make this fail
        t0 = time.monotonic()
        cr.check("我平时先看成分，再看价格，最后问朋友。" * 600)
        best = min(best, time.monotonic() - t0)
    assert best < 0.5


def test_the_team_can_add_terms_without_touching_code(tmp_path, monkeypatch):
    extra = tmp_path / "extra.txt"
    extra.write_text("# new terms\nhate:foobarslur\nabuse!zorp\n", encoding="utf-8")
    monkeypatch.setenv(K.EXTRA_FILE_ENV, str(extra))
    cr.reload()
    try:
        assert K.HATE in flags("what a foobarslur") and K.ABUSE in flags("you zorp") and flags("zorping along") == set()   # "!" = the whole word only
    finally:
        monkeypatch.delenv(K.EXTRA_FILE_ENV)
        cr.reload()
    assert flags("what a foobarslur") == set()
