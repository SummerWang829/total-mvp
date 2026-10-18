"""The research library: search and ask across transcripts, approved insights and imported documents, filtered by what the person may open,
with every answer resting on passages the server copied (never on what the model typed)."""
import pytest

from app import ratelimit
from app.db import SessionLocal
from app.models import Insight, Interview, TranscriptSegment, utcnow
from app.repo import ask as ask_mod
from app.repo.documents import pieces
from app.repo.tokens import tokens
from conftest import H
from test_project_members import second_researcher

LINES = [("moderator", "你上一次买护肤品是怎么选的？"), ("participant", "我会先看成分表，因为皮肤敏感，价格太贵的我一般不会买。"),
         ("moderator", "信息从哪里来？"), ("participant", "主要看小红书上真实用户的评价，朋友推荐也很重要。")]


def put_transcript(iid, lines=LINES):
    with SessionLocal() as db:
        for i, (spk, text) in enumerate(lines):
            db.add(TranscriptSegment(org_id=db.get(Interview, iid).org_id, interview_id=iid, idx=i, speaker=spk, start_ms=i * 5000, end_ms=i * 5000 + 4000, text_orig=text, lang="zh"))
        db.commit()


def library(org, lines=LINES):
    itv = org.interview()
    put_transcript(itv["id"], lines)
    pid = org.c.get(f"/api/interviews/{itv['id']}", headers=org.h()).json()["project_id"]
    return itv, pid


def search(org, q, headers=None, **params):
    r = org.c.get("/api/repo/search", headers=headers or org.h(), params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()["hits"]


def ask(org, q, headers=None, **body):
    return org.c.post("/api/repo/ask", headers=headers or org.h(), json={"question": q, **body})


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.seen = reply, []

    def answer(self, question, sources):
        self.seen.append(sources)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


# ---------- the search terms ----------
def test_chinese_is_searched_by_pairs_of_characters_and_english_by_words():
    assert tokens("价格太贵") == ["价格", "格太", "太贵"]
    assert tokens("Halal certification, 价格") == ["halal", "certification", "价格"]
    assert tokens("为什么") == [] and tokens("What is the") == []   # question words alone say nothing
    assert tokens("") == []


def test_documents_are_cut_into_passages_of_reasonable_size():
    text = "第一段。\n\n" + "这是一句比较长的话，" * 100 + "\n\n最后一段"
    got = pieces(text)
    assert 2 <= len(got) <= 6 and all(len(p) <= 520 for p in got) and "最后一段" in got[-1]
    assert pieces("   \n\n  ") == []


# ---------- searching ----------
def test_search_finds_what_participants_said_with_the_question_before_it_and_a_code_instead_of_a_name(org):
    itv, pid = library(org)
    hits = search(org, "价格太贵")
    assert len(hits) == 1 and hits[0]["type"] == "segment" and "价格太贵" in hits[0]["text"]
    assert hits[0]["who"] == "P-001" and hits[0]["question_before"] == "你上一次买护肤品是怎么选的？" and hits[0]["interview_id"] == itv["id"]
    assert "Wei Ling" not in str(hits) and hits[0]["project"]["id"] == pid
    assert search(org, "你上一次买护肤品是怎么选的") == []   # the interviewer's own lines are not sources


def test_the_better_match_comes_first_and_unrelated_words_find_nothing(org):
    library(org, [("participant", "我喜欢夏天去海边玩。"), ("participant", "护肤品的成分我最关心，成分表一定要看，成分安全最重要。")])
    hits = search(org, "护肤品成分")
    assert "成分安全" in hits[0]["text"]
    assert search(org, "量子计算机") == []


def test_only_selected_source_types_and_one_project_are_searched(org):
    itv, pid = library(org)
    assert search(org, "小红书", types="document") == []
    assert len(search(org, "小红书", types="segment")) == 1
    assert search(org, "小红书", project_id=pid)[0]["project"]["id"] == pid
    other = org.project()
    assert search(org, "小红书", project_id=other) == []


def test_approved_insights_are_searchable_but_drafts_are_not(org):
    itv, pid = library(org)
    with SessionLocal() as db:
        db.add(Insight(org_id=db.get(Interview, itv["id"]).org_id, interview_id=itv["id"], theme="价格敏感", statement="受访者认为太贵的产品不会考虑", status="approved"))
        db.add(Insight(org_id=db.get(Interview, itv["id"]).org_id, interview_id=itv["id"], theme="草稿主题", statement="仅仅是草稿内容", status="draft"))
        db.commit()
    got = search(org, "价格敏感", types="insight")
    assert len(got) == 1 and got[0]["type"] == "insight" and got[0]["interview_id"] == itv["id"]
    assert search(org, "草稿内容", types="insight") == []


def test_a_purged_interview_disappears_from_the_library_at_once(org):
    itv, pid = library(org)
    assert search(org, "价格太贵")
    with SessionLocal() as db:
        db.get(Interview, itv["id"]).purged_at = utcnow()
        db.commit()
    assert search(org, "价格太贵") == []


def test_a_focus_group_line_shows_its_seat_instead_of_a_code(org):
    library(org, [("moderator", "说说看？"), ("p3", "我觉得送货速度比价格更重要。")])
    assert search(org, "送货速度")[0]["who"] == "参与者 ·03"


# ---------- permission ----------
def test_a_researcher_not_on_the_project_gets_nothing_from_it_in_search_or_answers(org):
    itv, pid = library(org)
    other = second_researcher(org)
    assert search(org, "价格太贵", headers=other["h"]) == []
    assert org.c.get("/api/repo/search", headers=other["h"], params={"q": "价格太贵", "project_id": pid}).status_code == 404
    r = ask(org, "大家怎么看价格太贵", headers=other["h"])
    assert r.status_code == 200 and r.json()["insufficient"] is True and r.json()["answer"] == []
    assert ask(org, "价格", headers=other["h"], project_id=pid).status_code == 404
    assert search(org, "价格太贵", headers=org.h("admin"))   # admins see every project
    org.c.post(f"/api/projects/{pid}/members", headers=org.h(), json={"user_id": other["id"]})
    assert search(org, "价格太贵", headers=other["h"])   # once added, they do


def test_the_model_is_only_given_passages_the_person_may_see(org, monkeypatch):
    library(org)
    other = second_researcher(org)
    fake = FakeLLM({"claims": [], "insufficient": True})
    monkeypatch.setattr(ask_mod, "get_llm", lambda: fake)
    ask(org, "价格太贵", headers=other["h"])
    assert fake.seen == []   # nothing visible, so the model is not even called
    ask(org, "价格太贵")
    assert fake.seen and "价格太贵" in str(fake.seen[0])


def test_other_companies_and_other_roles_are_kept_out(org, client):
    library(org)
    from conftest import Org
    foreign = Org(client, "Other", "b@other.com")
    assert search(foreign, "价格太贵", headers=foreign.h("admin")) == []
    for role in ("moderator", "observer"):
        assert org.c.get("/api/repo/search", headers=org.h(role), params={"q": "价格"}).status_code == 403
        assert ask(org, "价格", headers=org.h(role)).status_code == 403
        assert org.c.get("/api/repo/documents", headers=org.h(role)).status_code == 403


# ---------- asking ----------
def test_an_answer_is_built_from_claims_each_with_the_verbatim_passages_it_rests_on(org):
    itv, pid = library(org)
    r = ask(org, "消费者怎么看价格太贵")
    body = r.json()
    assert r.status_code == 200 and body["insufficient"] is False and body["answer"]
    ev = body["answer"][0]["evidence"][0]
    assert ev["text"] == LINES[1][1] and ev["who"] == "P-001" and ev["interview_id"] == itv["id"] and ev["project"]["id"] == pid


def test_not_enough_evidence_is_said_plainly_instead_of_guessing(org):
    library(org)
    r = ask(org, "量子计算机的市场规模如何").json()
    assert r["insufficient"] is True and r["answer"] == [] and "没有足够的证据" in r["message"]


def test_claims_citing_unknown_passages_or_inventing_figures_are_dropped(org, monkeypatch):
    library(org)
    reply = {"claims": [{"text": "受访者普遍嫌价格太贵", "evidence": [1]},
                        {"text": "有 37% 的人不会买太贵的", "evidence": [1]},        # a figure that no cited passage contains
                        {"text": "编造的结论", "evidence": [99]},                      # a passage that was never given
                        {"text": "没有证据的结论", "evidence": []},
                        {"text": "", "evidence": [1]}], "insufficient": False}
    monkeypatch.setattr(ask_mod, "get_llm", lambda: FakeLLM(reply))
    body = ask(org, "价格太贵").json()
    assert [c["text"] for c in body["answer"]] == ["受访者普遍嫌价格太贵"]
    assert body["answer"][0]["evidence"][0]["text"] == LINES[1][1]   # the quote is the server's copy


def test_a_figure_that_really_is_in_the_cited_passage_is_allowed(org, monkeypatch):
    library(org, [("participant", "我大概每个月花 200 块买护肤品，超过 300 就觉得太贵了。")])
    monkeypatch.setattr(ask_mod, "get_llm", lambda: FakeLLM({"claims": [{"text": "超过 300 就觉得太贵", "evidence": [1]}]}))
    assert ask(org, "护肤品太贵").json()["answer"][0]["text"] == "超过 300 就觉得太贵"


def test_if_every_claim_is_dropped_or_the_model_says_so_the_answer_is_insufficient(org, monkeypatch):
    library(org)
    monkeypatch.setattr(ask_mod, "get_llm", lambda: FakeLLM({"claims": [{"text": "x", "evidence": [42]}], "insufficient": False}))
    assert ask(org, "价格太贵").json()["insufficient"] is True
    monkeypatch.setattr(ask_mod, "get_llm", lambda: FakeLLM({"claims": [{"text": "x", "evidence": [1]}], "insufficient": True}))
    assert ask(org, "价格太贵").json()["insufficient"] is True
    monkeypatch.setattr(ask_mod, "get_llm", lambda: FakeLLM("garbage"))
    assert ask(org, "价格太贵").json()["insufficient"] is True


def test_a_model_failure_is_reported_without_leaking_anything(org, monkeypatch):
    library(org)
    monkeypatch.setattr(ask_mod, "get_llm", lambda: FakeLLM(ValueError("secret detail")))
    r = ask(org, "价格太贵")
    assert r.status_code == 502 and "secret" not in r.text and "搜索" in r.json()["detail"]


def test_questions_are_limited_per_person_per_hour(org):
    library(org)
    ratelimit.reset()
    codes = [ask(org, "价格太贵").status_code for _ in range(42)]
    assert codes[:40] == [200] * 40 and codes[40:] == [429, 429]
    ratelimit.reset()
    assert ask(org, "价格太贵").status_code == 200


def test_asking_leaves_an_audit_line_without_the_question(org):
    library(org)
    ask(org, "价格太贵的秘密问题词")
    log = org.c.get("/api/audit?limit=20", headers=org.h("admin")).json()
    assert any(e["action"] == "repo.ask" for e in log) and "秘密问题词" not in str(log)


# ---------- documents ----------
def test_an_imported_document_is_searchable_and_can_be_removed(org):
    itv, pid = library(org)
    r = org.c.post("/api/repo/documents", headers=org.h(), json={"title": "2023 护肤趋势报告", "text": "报告指出，新加坡消费者越来越关注成分透明度。\n\n另外，直播带货影响了年轻人的购买决定。", "source_note": "内部，2023-06"})
    assert r.status_code == 201 and r.json()["pieces"] >= 1 and r.json()["project"] is None
    hit = search(org, "成分透明度")[0]
    assert hit["type"] == "document" and hit["title"] == "2023 护肤趋势报告" and hit["who"] == ""
    assert [d["title"] for d in org.c.get("/api/repo/documents", headers=org.h()).json()] == ["2023 护肤趋势报告"]
    assert org.c.delete(f"/api/repo/documents/{r.json()['id']}", headers=org.h()).status_code == 204
    assert search(org, "成分透明度") == []


def test_a_project_document_is_only_for_that_projects_members_and_a_company_document_for_all_staff(org):
    itv, pid = library(org)
    other = second_researcher(org)
    mine = org.c.post("/api/repo/documents", headers=org.h(), json={"title": "项目内部笔记", "text": "客户希望强调独家配方这一点。", "project_id": pid}).json()
    shared = org.c.post("/api/repo/documents", headers=org.h(), json={"title": "公司方法论", "text": "访谈提纲应当使用开放式问题。"}).json()
    assert search(org, "独家配方", headers=other["h"]) == [] and search(org, "开放式问题", headers=other["h"])
    assert [d["id"] for d in org.c.get("/api/repo/documents", headers=other["h"]).json()] == [shared["id"]]
    assert org.c.delete(f"/api/repo/documents/{mine['id']}", headers=other["h"]).status_code == 404
    assert org.c.post("/api/repo/documents", headers=other["h"], json={"title": "x", "text": "y y", "project_id": pid}).status_code == 404


def test_bad_documents_are_refused(org):
    assert org.c.post("/api/repo/documents", headers=org.h(), json={"title": "空", "text": "  \n\n "}).status_code == 422
    assert org.c.post("/api/repo/documents", headers=org.h(), json={"title": "", "text": "x"}).status_code == 422
    assert org.c.delete("/api/repo/documents/nope", headers=org.h()).status_code == 404


# ---------- uploading files ----------
def upload(org, name, data, headers=None, **form):
    return org.c.post("/api/repo/documents/upload", headers=headers or org.h(), files={"file": (name, data, "application/octet-stream")}, data=form)


def test_a_word_file_is_read_and_searchable(org):
    from app import docx_writer
    data = docx_writer.render([("title", "趋势报告"), ("p", "报告指出，新加坡消费者越来越关注成分透明度。"), ("table", ["指标", "数值"], [["关注度", "八成"]]), ("bullet", "直播带货影响年轻人")])
    r = upload(org, "2023 趋势报告.docx", data, source_note="内部")
    assert r.status_code == 201 and r.json()["title"] == "2023 趋势报告" and r.json()["source_note"] == "内部"
    assert search(org, "成分透明度")[0]["type"] == "document" and search(org, "八成")[0]["title"] == "2023 趋势报告" and search(org, "直播带货")


def test_text_files_work_and_a_title_can_be_given_and_a_project(org):
    itv, pid = library(org)
    r = upload(org, "notes.md", "# 访谈提纲\n\n先问购买习惯。".encode("utf-8"), title="我的笔记", project_id=pid)
    assert r.status_code == 201 and r.json()["title"] == "我的笔记" and r.json()["project"]["id"] == pid
    assert upload(org, "a.csv", "题目,选项\n购买渠道,线上".encode("utf-8-sig")).status_code == 201
    other = second_researcher(org)
    assert upload(org, "x.txt", b"hello world", headers=other["h"], project_id=pid).status_code == 404


def test_files_that_cannot_be_read_are_refused_clearly(org):
    for name, data, why in (("a.pdf", b"%PDF-1.4", "PDF"), ("a.exe", b"MZ", "只支持"), ("a.docx", b"not a zip", "有效的 Word"), ("empty.txt", b"  \n ", "没有可用的文字"),
                            ("big.txt", b"x" * (8 * 1024 * 1024 + 10), "太大")):
        r = upload(org, name, data)
        assert r.status_code == 422 and why in r.json()["detail"], (name, r.text)


def test_a_zip_that_unpacks_to_something_huge_is_refused():
    import io
    import zipfile
    from app.repo import extract
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<w:p><w:t>a</w:t></w:p>" + " " * (extract.MAX_XML + 10))
    import pytest
    with pytest.raises(extract.ExtractError):
        extract.docx_text(buf.getvalue())
    assert extract.docx_text(__import__("app.docx_writer", fromlist=["render"]).render([("p", "a & b <c>")])) == "a & b <c>"


def test_uploading_needs_the_staff_role(org):
    for role in ("moderator", "observer", "viewer"):
        if role == "viewer":
            continue
        assert upload(org, "a.txt", b"hello", headers=org.h(role)).status_code == 403
