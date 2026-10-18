"""Background jobs: transcribing runs in a worker, not in the request; the answer is a job id that can be asked about; transient failures are retried with a back-off, permanent
ones fail at once with a plain message; nobody outside the project can see a job."""
from datetime import timedelta

from fastapi import HTTPException

from app import jobs, monitor
from app.db import SessionLocal
from app.models import Interview, Job, utcnow
from test_pipeline import finish, media, start_recording, upload, video_server_configured  # noqa: F401 - fixtures
from test_project_members import second_researcher


def ready_interview(org, media):
    itv = org.interview()
    rid = start_recording(org, itv)
    upload(org, itv, rid, "moderator", media["mod"], 0)
    upload(org, itv, rid, "participant", media["par"], 1000)
    finish(org, itv)
    return itv


def start(org, itv, kind="transcribe", headers=None):
    return org.c.post(f"/api/interviews/{itv['id']}/jobs", headers=headers or org.h(), json={"kind": kind})


def run_all():
    with SessionLocal() as db:
        return jobs.run_pending(db)


def test_a_transcribe_job_answers_at_once_then_runs_in_the_worker(org, media):
    itv = ready_interview(org, media)
    r = start(org, itv)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h()).json() == []       # nothing has run yet
    assert run_all() == 1
    j = org.c.get(f"/api/jobs/{r.json()['id']}", headers=org.h()).json()
    assert j["status"] == "done" and j["result"]["segments"] > 0 and j["attempts"] == 1 and j["error"] == ""
    assert len(org.c.get(f"/api/interviews/{itv['id']}/transcript", headers=org.h()).json()) == j["result"]["segments"]


def test_asking_again_while_one_is_waiting_gives_the_same_job(org, media):
    itv = ready_interview(org, media)
    a, b = start(org, itv).json(), start(org, itv).json()
    assert a["id"] == b["id"]
    run_all()
    assert start(org, itv).json()["id"] != a["id"]          # once finished, a new one can be made


def test_a_permanent_failure_fails_at_once_with_its_message_and_can_be_retried(org):
    itv = org.interview()                                    # nothing recorded
    j = start(org, itv).json()
    run_all()
    got = org.c.get(f"/api/jobs/{j['id']}", headers=org.h()).json()
    assert got["status"] == "failed" and "没有可转写的录制" in got["error"] and got["attempts"] == 1
    r = org.c.post(f"/api/jobs/{j['id']}/retry", headers=org.h()).json()
    assert r["status"] == "queued" and r["attempts"] == 0 and r["error"] == ""
    assert org.c.post(f"/api/jobs/{j['id']}/retry", headers=org.h()).status_code == 409        # only a failed job can be retried


def test_a_transient_failure_is_retried_after_a_wait_and_gives_up_after_three_attempts(org, monkeypatch):
    itv = org.interview()
    calls = []

    def flaky(db, i, job):
        calls.append(1)
        raise HTTPException(502, "转写服务没有响应")
    monkeypatch.setitem(jobs.HANDLERS, "transcribe", flaky)
    j = start(org, itv).json()
    for attempt in (1, 2, 3):
        assert run_all() == 1
        with SessionLocal() as db:
            row = db.get(Job, j["id"])
            if attempt < 3:
                assert row.status == "queued" and row.attempts == attempt and row.run_after > utcnow() and "转写服务" in row.error
                row.run_after = utcnow() - timedelta(seconds=1)          # skip the waiting
                db.commit()
    got = org.c.get(f"/api/jobs/{j['id']}", headers=org.h()).json()
    assert got["status"] == "failed" and got["attempts"] == 3 and len(calls) == 3
    assert run_all() == 0


def test_a_retry_that_works_ends_done(org, monkeypatch):
    itv = org.interview()
    state = {"n": 0}

    def once_bad(db, i, job):
        state["n"] += 1
        if state["n"] == 1:
            raise HTTPException(502, "转写服务没有响应")
        return {"segments": 7}
    monkeypatch.setitem(jobs.HANDLERS, "transcribe", once_bad)
    j = start(org, itv).json()
    run_all()
    with SessionLocal() as db:
        db.get(Job, j["id"]).run_after = utcnow() - timedelta(seconds=1)
        db.commit()
    run_all()
    got = org.c.get(f"/api/jobs/{j['id']}", headers=org.h()).json()
    assert got["status"] == "done" and got["result"] == {"segments": 7} and got["attempts"] == 2 and got["error"] == ""


def test_an_unexpected_crash_is_logged_and_shown_as_a_plain_line_without_the_exception_text(org, monkeypatch):
    itv = org.interview()
    monkeypatch.setitem(jobs.HANDLERS, "transcribe", lambda db, i, job: (_ for _ in ()).throw(KeyError("secret-detail-xyz")))
    j = start(org, itv).json()
    run_all()
    got = org.c.get(f"/api/jobs/{j['id']}", headers=org.h()).json()
    assert got["status"] == "failed" and "secret-detail-xyz" not in got["error"] and "管理员" in got["error"]


def test_a_job_stuck_in_running_after_a_restart_is_queued_again(org, monkeypatch):
    itv = org.interview()
    j = start(org, itv).json()
    with SessionLocal() as db:
        row = db.get(Job, j["id"])
        row.status, row.started_at = "running", utcnow() - timedelta(seconds=jobs.STUCK_AFTER_S + 60)
        db.commit()
    monkeypatch.setitem(jobs.HANDLERS, "transcribe", lambda db, i, job: {"segments": 1})
    assert run_all() == 1
    assert org.c.get(f"/api/jobs/{j['id']}", headers=org.h()).json()["status"] == "done"


def test_a_disagreement_analysis_can_run_as_a_job(org):
    from test_group_analysis import group_with_transcript
    iid = group_with_transcript(org)
    j = org.c.post(f"/api/interviews/{iid}/jobs", headers=org.h(), json={"kind": "disagreement"}).json()
    run_all()
    got = org.c.get(f"/api/jobs/{j['id']}", headers=org.h()).json()
    assert got["status"] == "done" and got["result"]["claims"] > 0
    assert org.c.get(f"/api/interviews/{iid}/disagreement", headers=org.h()).json()["analysed"] is True


def test_only_the_project_team_can_use_jobs(org, media):
    itv = ready_interview(org, media)
    j = start(org, itv).json()
    other = second_researcher(org)["h"]
    assert start(org, itv, headers=other).status_code == 404
    assert org.c.get(f"/api/jobs/{j['id']}", headers=other).status_code == 404
    assert org.c.get(f"/api/interviews/{itv['id']}/jobs", headers=other).status_code == 404
    assert org.c.post(f"/api/jobs/{j['id']}/retry", headers=other).status_code == 404
    for role in ("observer", "moderator"):
        assert start(org, itv, headers=org.h(role)).status_code == 403
        assert org.c.get(f"/api/jobs/{j['id']}", headers=org.h(role)).status_code == 403
    assert start(org, itv, kind="nope").status_code == 422
    assert [x["id"] for x in org.c.get(f"/api/interviews/{itv['id']}/jobs", headers=org.h()).json()] == [j["id"]]


def test_purging_an_interview_removes_its_jobs_and_a_purged_interview_cannot_run_one(org):
    from app.retention import purge_interview
    itv = org.interview()
    j = start(org, itv).json()
    with SessionLocal() as db:
        purge_interview(db, db.get(Interview, itv["id"]), "test")
        db.commit()
        assert db.get(Job, j["id"]) is None


def test_the_monitor_warns_about_failed_and_stuck_jobs(org):
    itv = org.interview()
    assert monitor.check_jobs().level == "ok"
    with SessionLocal() as db:
        db.add(Job(org_id=db.get(Interview, itv["id"]).org_id, interview_id=itv["id"], kind="transcribe", status="failed", finished_at=utcnow()))
        db.commit()
    assert monitor.check_jobs().level == "warn"
