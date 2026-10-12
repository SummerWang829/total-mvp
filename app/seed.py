"""`python -m app.seed --url http://localhost:8000`: demo accounts + a tidy demo project, then prints how to log in.

First run (or after the admin's "清理演示数据"): builds sessions at every stage of the participant journey, one finished
interview with a transcript, insights and a clip, and one session kept for your own testing.
Later runs leave the demo data alone and only give the testing session a fresh invitation link.
"""
import argparse
from datetime import datetime, timedelta, timezone

import httpx

PW = "demo12345"
USERS = [("admin", "管理员", "admin"), ("researcher", "研究员", "researcher"), ("moderator", "主持人", "moderator"), ("observer", "观察员（客户）", "observer")]
PROJECT = "演示：新加坡护肤品深访"
TEST_TITLE = "演示访谈（给你测试用）"

PEOPLE = [  # name, country, language
    ("Tan Wei Ling", "SG", "en"), ("陈美玲", "SG", "zh"), ("Nur Aisyah", "SG", "ms"), ("Kavitha R.", "SG", "ta"),
    ("Siti Rahma", "ID", "en"), ("Lim Jun Jie", "SG", "en"),
]
TRANSCRIPT = [  # speaker, seconds, original, Chinese
    ("moderator", 2, "Thanks for joining. How do you usually choose a new skincare product?", "谢谢参加。你一般怎么挑选新的护肤品？"),
    ("participant", 8, "I always read reviews on Shopee first, then I ask my sister.", "我总是先看虾皮上的评价，然后问我姐姐。"),
    ("moderator", 16, "What makes you trust a brand you have never used?", "什么会让你信任一个从没用过的牌子？"),
    ("participant", 22, "Clear ingredients. Singapore is so humid, I avoid anything oily.", "成分写得清楚。新加坡太潮湿了，油的东西我都不用。"),
    ("moderator", 31, "Where do you buy in the end?", "最后你在哪里买？"),
    ("participant", 36, "Usually Watsons, because I can test the texture there before paying.", "通常在屈臣氏，因为付钱前可以在那里试质地。"),
    ("participant", 45, "I will pay more if the brand explains the formula honestly.", "如果品牌老实讲清楚配方，我愿意多付钱。"),
]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--url", default="http://localhost:8000"); a = ap.parse_args()
    c = httpx.Client(base_url=a.url + "/api", timeout=30)
    r = c.post("/auth/register", json={"org_name": "演示公司", "name": "管理员", "email": "admin@demo.test", "password": PW})
    if r.status_code != 200:
        r = c.post("/auth/login", json={"email": "admin@demo.test", "password": PW})
    if r.status_code != 200:
        print("演示数据没有创建：", r.text); return
    adm = {"Authorization": "Bearer " + r.json()["token"]}
    ids = {}
    for key, name, role in USERS[1:]:
        u = c.post("/users", headers=adm, json={"name": name, "email": f"{key}@demo.test", "password": PW, "role": role})
        ids[key] = u.json()["id"] if u.status_code == 201 else next(x["id"] for x in c.get("/users", headers=adm).json() if x["email"] == f"{key}@demo.test")
    login = lambda key: {"Authorization": "Bearer " + c.post("/auth/login", json={"email": f"{key}@demo.test", "password": PW}).json()["token"]}
    rs, mod = login("researcher"), login("moderator")
    proj = next((p for p in c.get("/projects", headers=rs).json() if p["name"] == PROJECT), None) or \
        c.post("/projects", headers=rs, json={"name": PROJECT, "market": "SG", "language": "en", "brief": "了解新加坡消费者如何挑选海外护肤品牌"}).json()
    pid = proj["id"]
    its = c.get(f"/projects/{pid}/interviews", headers=rs).json()
    if not its:
        build(c, rs, mod, pid, ids)
        its = c.get(f"/projects/{pid}/interviews", headers=rs).json()
    test = next((i for i in its if i["title"] == TEST_TITLE and i["status"] != "ended" and not i["purged"]), None)
    if test:  # a fresh link every start: links are only shown once
        invite = c.post(f"/interviews/{test['id']}/invite/rotate", headers=rs).json()["invite_path"]
    else:
        part = c.post(f"/projects/{pid}/participants", headers=rs, json={"name": "测试受访者", "country": "SG", "language": "en"}).json()
        test = c.post(f"/projects/{pid}/interviews", headers=rs, json={"participant_id": part["id"], "title": TEST_TITLE,
                                                                       "moderator_id": ids["moderator"], "observer_ids": [ids["observer"]]}).json()
        invite = test["invite_path"]
    print("\n" + "=" * 60)
    print(f"打开  {a.url}   用下面任一账号登录（密码都是 {PW}）：")
    for key, name, _ in USERS:
        print(f"   {name:　<8} {key}@demo.test")
    print(f"\n演示项目：{a.url}/#/p/{pid}/home")
    print(f"给你测试用的受访者邀请链接（用另一个浏览器或无痕窗口打开）：\n   {a.url}{invite}")
    print("=" * 60)


def build(c: httpx.Client, rs: dict, mod: dict, pid: str, ids: dict) -> None:
    """One session at each stage of the journey, so every page has something real to show."""
    now = datetime.now(timezone.utc)
    parts = [c.post(f"/projects/{pid}/participants", headers=rs, json={"name": n, "country": ct, "language": lg}).json() for n, ct, lg in PEOPLE]
    new = lambda p, title, when=None: c.post(f"/projects/{pid}/interviews", headers=rs, json={
        "participant_id": p["id"], "title": title, "scheduled_at": iso(when) if when else None,
        "moderator_id": ids["moderator"], "observer_ids": [ids["observer"]]}).json()
    pub = lambda itv, path, body=None: c.post(f"/public/join/{itv['invite_token']}/{path}", json=body)
    consent = lambda itv: pub(itv, "consent", {"country": "SG", "recording": True, "observers": True, "cross_border": True})
    device = lambda itv: pub(itv, "device", {"camera": True, "microphone": True, "speaker": True})

    # 1. finished: came in, talked, transcript + insights + a clip
    done = new(parts[0], "深访 01 · 购买渠道")
    pub(done, "opened"); device(done); consent(done)
    c.post(f"/interviews/{done['id']}/admit", headers=mod, json={"admit": True})
    c.post(f"/interviews/{done['id']}/start", headers=mod)
    pub(done, "enter")
    c.post(f"/interviews/{done['id']}/end", headers=mod)
    segs = [{"idx": n, "speaker": sp, "start_ms": s * 1000, "end_ms": s * 1000 + 5000, "text_orig": o, "text_zh": zh, "lang": "en"}
            for n, (sp, s, o, zh) in enumerate(TRANSCRIPT)]
    c.post(f"/interviews/{done['id']}/transcript", headers=rs, json={"segments": segs})
    by_idx = {s["idx"]: s for s in c.get(f"/interviews/{done['id']}/transcript", headers=rs).json()}
    ins = lambda theme, stmt, idxs: c.post(f"/interviews/{done['id']}/insights", headers=rs, json={"theme": theme, "statement": stmt,
        "evidence": [{"segment_id": by_idx[i]["id"], "quote": by_idx[i]["text_orig"], "lang": "orig"} for i in idxs]}).json()
    trust = ins("信任来自说清楚", "受访者愿意为讲清配方的品牌多付钱，成分透明比价格更关键。", [3, 6])
    ins("线下试用决定购买", "受访者先在电商平台看评价，最后在屈臣氏试过质地再买。", [1, 5])
    c.post(f"/insights/{trust['id']}/approve", headers=rs)
    c.post(f"/interviews/{done['id']}/clips", headers=rs, json={"evidence_id": trust["evidence"][0]["id"]})
    # 2. consented and device ok, scheduled in an hour
    ready = new(parts[1], "深访 02 · 成分与功效", now + timedelta(hours=1))
    pub(ready, "opened"); device(ready); consent(ready)
    # 3. opened the invitation, tomorrow
    opened = new(parts[2], "深访 03 · 价格敏感", now + timedelta(days=1))
    pub(opened, "opened")
    # 4. invitation sent only, in two days
    new(parts[3], "深访 04 · 社媒种草", now + timedelta(days=2))
    # parts[4] (Indonesia) shows "地区暂未开放"; parts[5] is in the pool, not invited yet


if __name__ == "__main__":
    main()
