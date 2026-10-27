"""Interview formats: five fixed kinds, allowed per research method, chosen by candidates in the questionnaire, written into the invitation; the language choice is stored too."""
from test_screen_form import published, qs


def test_a_project_allows_video_by_default_and_the_team_can_change_it(org):
    pid = org.project()
    d = org.c.get(f"/api/projects/{pid}/languages", headers=org.h()).json()
    assert d["formats"] == {"depth": ["video"], "group": ["video"]} and set(d["format_names"]) == {"audio", "text", "text_voice", "video", "video_screen"}
    r = org.c.put(f"/api/projects/{pid}/languages", headers=org.h("admin"), json={"formats": {"depth": ["video", "audio", "text"]}})
    assert r.status_code == 200 and r.json()["formats"]["depth"] == ["video", "audio", "text"] and r.json()["formats"]["group"] == ["video"]
    assert org.c.put(f"/api/projects/{pid}/languages", headers=org.h("admin"), json={"formats": {"depth": []}}).status_code == 422  # at least one
    assert org.c.put(f"/api/projects/{pid}/languages", headers=org.h("admin"), json={"formats": {"depth": ["hologram"]}}).status_code == 422
    assert org.c.put(f"/api/projects/{pid}/languages", headers=org.h("admin"), json={"formats": {"quant": ["video"]}}).status_code == 422  # a method the project does not use


def test_the_candidate_chooses_languages_and_formats_and_they_fill_the_invitation(client, org):
    pid, token = published(org)
    org.c.put(f"/api/projects/{pid}/languages", headers=org.h("admin"), json={"languages": ["zh", "en"], "default": "zh", "formats": {"depth": ["video", "audio"]}})
    info = client.get(f"/api/public/screen/{token}").json()
    assert [f["code"] for f in info["formats"]] == ["audio", "video"] and [x["code"] for x in info["interview_languages"]] == ["zh", "en"]
    ans = {"age": 28, "use": "daily", "chan": ["on"], "why": "价格"}
    r = client.post(f"/api/public/screen/{token}/submit", json={"answers": ans, "name": "林小雨", "email": "lin@example.com", "country": "SG", "consent": True,
                                                              "langs": ["en", "zh", "ta"], "formats": ["audio", "text", "video"]})
    assert r.status_code == 201, r.text
    p = org.c.get(f"/api/projects/{pid}/participants", headers=org.h()).json()[0]
    assert p["pref_langs"] == ["en", "zh"] and p["pref_formats"] == ["audio", "video"] and p["language"] == "en"  # what the project does not offer is dropped; the first is the preferred
    itv = org.c.post(f"/api/projects/{pid}/interviews", headers=org.h(), json={"participant_id": p["id"], "moderator_id": org.users["moderator"]}).json()
    assert itv["format"] == "audio" and itv["language"] == "en"  # the first choice fills the session
    r = org.c.put(f"/api/interviews/{itv['id']}", headers=org.h(), json={"format": "video", "language": "zh"})
    assert r.status_code == 200 and r.json()["format"] == "video" and r.json()["language"] == "zh"
    assert org.c.put(f"/api/interviews/{itv['id']}", headers=org.h(), json={"format": "hologram"}).status_code == 422


def test_the_invitation_names_the_format(org):
    from app import invites
    from app.db import SessionLocal
    from app.models import Interview, Participant
    itv = org.interview()
    org.c.put(f"/api/interviews/{itv['id']}", headers=org.h(), json={"format": "text_voice"})
    with SessionLocal() as db:
        i = db.get(Interview, itv["id"])
        p = db.get(Participant, i.participant_id)
        p.language = "zh"
        _, body_zh = invites.compose(i, p, "王老师", None)
        p.language = "en"
        _, body_en = invites.compose(i, p, "Wang", None)
    assert "文字+语音转录" in body_zh and "Text chat with voice transcription" in body_en
