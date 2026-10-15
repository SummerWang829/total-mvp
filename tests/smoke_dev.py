"""Smoke test of the user's entry point: `scripts/dev.sh` must start the video server + app + demo data, and the printed
accounts and invite link must actually work. Run by scripts/qa.sh."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    tmp = tempfile.mkdtemp()
    port = 8000 + int(time.time()) % 900 + 50
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PORT": str(port), "SKIP_PIP": "1", "DATABASE_URL": f"sqlite:///{tmp}/d.db", "MEDIA_DIR": f"{tmp}/media", "LIVEKIT_URL": "ws://localhost:7880",
           "ASR_PROVIDER": "mock", "LLM_PROVIDER": "mock", "ASR_API_KEY": "", "LLM_API_KEY": "",
           "MODERATOR_PROVIDER": "mock", "MODERATOR_API_KEY": "", "MODERATOR_VENDOR": ""}  # never the user's real AI services
    out = open(os.path.join(tmp, "out.txt"), "w")
    p = subprocess.Popen(["bash", "scripts/dev.sh"], cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    fails = []
    try:
        text = ""
        for _ in range(120):
            time.sleep(0.5)
            text = open(os.path.join(tmp, "out.txt"), encoding="utf-8", errors="replace").read()  # the file is read while it is still being written: a half-written character must not crash the check
            if "已启动" in text or p.poll() is not None:
                break
        if "已启动" not in text:
            print(text[-1500:]); fails.append("dev.sh did not reach 'started'")
        else:
            base = f"http://localhost:{port}"
            m = re.search(r"/join\.html\?t=[\w-]+", text)
            if not m:
                fails.append("no invite link printed")
            else:
                info = json.loads(urllib.request.urlopen(base + "/api" + m.group(0).replace("/join.html?t=", "/public/join/")).read())
                if not info["consent"]["text"] or "Singapore" not in info["consent"]["text"]:
                    fails.append("invite link does not open the consent info")
            for acct in ("admin", "researcher", "moderator", "observer"):
                req = urllib.request.Request(base + "/api/auth/login", data=json.dumps({"email": f"{acct}@demo.test", "password": "demo12345"}).encode(), headers={"Content-Type": "application/json"})
                if "token" not in json.loads(urllib.request.urlopen(req).read()):
                    fails.append(f"demo account {acct} cannot log in")
            cfg = json.loads(urllib.request.urlopen(base + "/api/public/config").read())
            if not cfg["video"]:
                fails.append("video server not configured by dev.sh")
            if urllib.request.urlopen("http://localhost:7880").status != 200:
                fails.append("video server not running")
            for page in ("/", "/join.html", "/room.html", "/share.html", "/vendor/livekit-client.umd.js"):
                if urllib.request.urlopen(base + page).status != 200:
                    fails.append("page missing: " + page)
    finally:
        if os.name == "nt":  # Windows: no process groups / pkill; kill the whole dev.sh process tree instead
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            try:
                os.killpg(p.pid, 15)
            except Exception:
                pass
            time.sleep(1)
            subprocess.run(["pkill", "-f", "[l]ivekit-server --config /tmp/total-livekit.yaml"])
    print("SMOKE dev.sh:", "FAILED " + "; ".join(fails) if fails else "ok")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
