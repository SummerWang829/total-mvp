"""`python -m app.doctor` provider check: 200 and 404 (local server without /models) pass; bad key / unreachable fail."""
import httpx
import pytest

from app import doctor


class _Resp:
    def __init__(self, code):
        self.status_code = code


@pytest.mark.parametrize("result, failed", [(200, 0), (404, 0), (401, 1), (403, 1), (httpx.ConnectError("x"), 1)])
def test_check_provider(monkeypatch, capsys, result, failed):
    def fake_get(*a, **k):
        if isinstance(result, Exception):
            raise result
        return _Resp(result)

    monkeypatch.setattr(doctor.httpx, "get", fake_get)
    monkeypatch.setattr(doctor, "bad", 0)
    doctor.check_provider("转写", "openai", "http://127.0.0.1:8178/v1", "local")
    assert doctor.bad == failed
    assert ("✗" in capsys.readouterr().out) == bool(failed)


def test_missing_key_fails(monkeypatch):
    monkeypatch.setattr(doctor, "bad", 0)
    doctor.check_provider("转写", "openai", "http://127.0.0.1:8178/v1", None)
    assert doctor.bad == 1
