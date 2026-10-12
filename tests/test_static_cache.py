"""Browsers must re-check static files, so a changed page, script or logo shows up after a normal refresh."""
import pytest


@pytest.mark.parametrize("path", ["/", "/logo.js", "/app.css", "/join.html"])
def test_static_files_are_revalidated(client, path):
    r = client.get(path)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


def test_unchanged_file_is_still_cheap(client):
    etag = client.get("/logo.js").headers["etag"]
    assert client.get("/logo.js", headers={"If-None-Match": etag}).status_code == 304
