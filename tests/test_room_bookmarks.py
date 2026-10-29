"""Timestamp bookmarks are server-timed, live-only, and scoped to visible interviews."""
import pytest
from app.routers import concept
from conftest import Org


def live(org):
    it = org.interview()
    assert org.c.post(f"/api/interviews/{it['id']}/start", headers=org.h('moderator')).status_code == 200
    return it


@pytest.mark.parametrize('label', ['高光', '痛点', '金句', '存疑'])
def test_marker_uses_server_time_and_filters(org, monkeypatch, label):
    it = live(org)
    monkeypatch.setattr(concept, 'at_ms', lambda _: 43210)
    url = f"/api/interviews/{it['id']}/highlights"
    r = org.c.post(url, headers=org.h('observer'), json={'kind':'mark', 'text':label, 'at_ms':123})
    assert r.status_code == 201, r.text
    assert r.json()['at_ms'] == 43210 and r.json()['text'] == label and r.json()['caption_id'] is None
    assert org.c.get(url+'?kind=mark', headers=org.h()).json()[0]['kind'] == 'mark'
    assert org.c.get(url+'?kind=probe', headers=org.h()).json() == []


@pytest.mark.parametrize('body', [{'text':'编造观点'}, {'text':'高光','caption_id':'fake'}, {'text':'痛点','caption_ids':['fake']}, {'text':'金句','depth':'deep'}])
def test_invalid_marker_rejected(org, body):
    it=live(org)
    assert org.c.post(f"/api/interviews/{it['id']}/highlights",headers=org.h(),json={'kind':'mark',**body}).status_code==422


@pytest.mark.parametrize('status',['scheduled','ended'])
def test_mark_requires_live_interview(org,status):
    it=org.interview()
    if status=='ended':
        from app.db import SessionLocal
        from app.models import Interview
        with SessionLocal() as db:
            db.get(Interview,it['id']).status='ended';db.commit()
    assert org.c.post(f"/api/interviews/{it['id']}/highlights",headers=org.h(),json={'kind':'mark','text':'高光'}).status_code==409


def test_mark_denies_other_org_unassigned_and_anonymous(org):
    it=live(org);url=f"/api/interviews/{it['id']}/highlights"
    other=Org(org.c,'Other','other@else.test')
    assert org.c.post(url,headers=other.h(),json={'kind':'mark','text':'高光'}).status_code==404
    assert org.c.post(url,json={'kind':'mark','text':'高光'}).status_code==401
    private=org.interview(observers=False)
    org.c.post(f"/api/interviews/{private['id']}/start",headers=org.h('moderator'))
    assert org.c.post(f"/api/interviews/{private['id']}/highlights",headers=org.h('observer'),json={'kind':'mark','text':'高光'}).status_code==404
