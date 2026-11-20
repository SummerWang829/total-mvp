"""Stage 4 load test (also reachable as scripts/outline_wb_board_load.py): 30 interviews at once feed the live board. Uses a temporary database and a FAKE model whose answer time you choose (default 1.5 s ± 30 %), so it measures our own
pipeline (line -> rules -> coding job -> database) and the model-call count, NOT the real model's speed or price. Run: python -m app.outline_wb.loadtest [interviews] [model_seconds] [out.md] [pause_s] [questions]; with LOAD_REAL=1 the real model is used (set the real MODERATOR_* values; the fake latency argument is ignored)
The real figures need the real model: run it with your key in .env and MODERATOR_PROVIDER set (the key is never printed)."""
import os
import random
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
LATENCY = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
OUT = sys.argv[3] if len(sys.argv) > 3 else ""
PAUSE = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0     # seconds between two sentences of one interview (a real interview: about 20-30)
QUESTIONS = int(sys.argv[5]) if len(sys.argv) > 5 else 6    # questions each interview goes through
REAL = os.environ.get("LOAD_REAL") == "1"   # LOAD_REAL=1: use the model configured in .env / the environment (MODERATOR_PROVIDER, key, vendor); the key is never printed
tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{tmp}/load.db", MEDIA_DIR=f"{tmp}/m", DISABLE_WORKER="1", LIVEKIT_URL="", ASR_PROVIDER="mock", LLM_PROVIDER=os.environ.get("LLM_PROVIDER", "mock") if REAL else "mock")
if not REAL:
    os.environ.update(MODERATOR_PROVIDER="mock", MODERATOR_VENDOR="")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402,F401  (registers every table)
from app.models import CaptionLine, Interview, Org, Participant, Project, utcnow  # noqa: E402
from app.outline_wb import board, demo, service as svc, stage3  # noqa: E402
from app.outline_wb.models import CodedAnswer, FocusQuestion  # noqa: E402

CALLS = {"n": 0}
PARTICIPANT_LINES = ["我平时在网上买，价格偏贵。", "最近一次买是上个月，朋友推荐的一款精华。", "我会比较几个品牌，主要看成分和价格。", "包装很好看，但是价格有点高。", "我一般先看评价再决定买不买。", "朋友的推荐对我影响比较大。"]


class FakeChat:
    """A stand-in for the model: answers after LATENCY seconds."""
    name = "fake"

    def complete(self, msgs, **kw):  # noqa: ANN001, ANN003
        CALLS["n"] += 1
        time.sleep(max(0.05, LATENCY * random.uniform(0.7, 1.3)))
        return {"code": random.choice(["偏贵", "成分", "朋友推荐", "包装"]), "confidence": round(random.uniform(0.6, 0.98), 2)}


def setup() -> tuple[str, list[str]]:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        org = Org(name="Load")
        db.add(org)
        db.flush()
        proj = Project(org_id=org.id, name="压测", created_by="u", brief="A 品牌进入新加坡，了解护肤品选购。", language="zh")
        db.add(proj)
        db.flush()
        made = demo.build(db, proj.id)
        v = svc.get_version(db, org.id, made["version"])
        v.status = "published"
        for uid in [i.item_uid for i in svc.items_of(db, v.id)][:3]:
            pass
        items = [i for i in svc.items_of(db, v.id) if i.kind == "question"]
        for it, text in zip(items[:3], ["她们平时怎么买护肤品", "价格接受范围", "会拿什么品牌来比较"]):
            svc.add_focus(db, org.id, proj.id, text, "u", "researcher", item_uid=it.item_uid)
        db.commit()
        return proj.id, [i.id for i in db.query(Interview).filter_by(project_id=proj.id)]


def one(pid: str, n: int, errors: list[str]) -> None:
    try:
        with SessionLocal() as db:
            p = db.get(Project, pid)
            part = Participant(org_id=p.org_id, project_id=p.id, name=f"P{n}", country="SG", language="zh", code=f"Q-{n:03d}")
            db.add(part)
            db.flush()
            itv = Interview(org_id=p.org_id, project_id=p.id, participant_id=part.id, title=f"L{n}", language="zh", status="live", moderation="ai", invite_hash=f"load-{n}", started_at=utcnow(), duration_min=45)
            db.add(itv)
            db.flush()
            stage3.snapshot_on_start(db, itv)
            db.commit()
            iid = itv.id
        items = []
        with SessionLocal() as db:
            items = [i for i in svc.items_of(db, db.get(Interview, iid).outline_version_id) if i.kind == "question"]
        seq = 0
        for it in items[:QUESTIONS]:
            for speaker, text in (("moderator", next(iter(it.texts.values()))), ("participant", random.choice(PARTICIPANT_LINES))):
                seq += 1
                with SessionLocal() as db:
                    itv = db.get(Interview, iid)
                    line = CaptionLine(org_id=itv.org_id, interview_id=iid, seq=seq, speaker=speaker, text=text, at_ms=seq * 15000)
                    db.add(line)
                    db.flush()
                    task = board.assign(db, itv, line, speaker, text)
                    db.commit()
                board.submit(task)
                time.sleep(PAUSE * random.uniform(0.7, 1.3))   # the time between sentences
    except Exception as e:   # noqa: BLE001 - the load test reports any failure instead of stopping
        errors.append(type(e).__name__)


def main() -> int:
    pid, _ = setup()
    if not REAL:
        board._chat = lambda: FakeChat()   # type: ignore[assignment]
    elif board._chat() is None:
        print("LOAD_REAL=1 but no real model is configured (MODERATOR_PROVIDER / MODERATOR_VENDOR / key): nothing to measure.")
        return 2
    if REAL:   # 2026-11-20: the real model is counted too (the report said 0 calls, so the calls per interview and the cost could not be worked out)
        real_chat = board._chat

        class Counting:
            def __init__(self, inner):  # noqa: ANN001
                self.inner, self.name = inner, getattr(inner, "name", "real")

            def complete(self, msgs, **kw):  # noqa: ANN001, ANN003
                CALLS["n"] += 1
                return self.inner.complete(msgs, **kw)

        board._chat = lambda: (lambda c: Counting(c) if c is not None else None)(real_chat())   # type: ignore[assignment]
    errors: list[str] = []
    t0 = time.time()
    ts = [threading.Thread(target=one, args=(pid, n, errors)) for n in range(N)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    sent = time.time() - t0
    for _ in range(300):   # wait for the background jobs to drain
        with SessionLocal() as db:
            pending = board._POOL._work_queue.qsize()
        if pending == 0:
            time.sleep(LATENCY * 2)
            break
        time.sleep(0.5)
    wall = time.time() - t0
    ms = sorted(m["ms"] for m in board.METRICS)
    bad = sum(1 for m in board.METRICS if not m["ok"])
    with SessionLocal() as db:
        coded = db.query(CodedAnswer).count()
    p = lambda q: ms[min(len(ms) - 1, int(len(ms) * q))] / 1000 if ms else 0   # noqa: E731
    lines = [f"# 看板压测（模拟）", "", f"- 同时进行的访谈：{N} 场；每场 {QUESTIONS} 题 × 2 句；三个关注问题；每句间隔约 {PAUSE:g} 秒（真实访谈约 20–30 秒，所以这次的压力约是真实的 {25 / PAUSE:.0f} 倍）。",
             ("- **用的是真实模型**（.env 里配好的那个）：延迟、失败和费用是真的；平均每场调用次数乘以单价就是每场费用。" if REAL else f"- **模型是假的**：每次编码等 {LATENCY} 秒 ±30%（不是真 DeepSeek 的速度和价格）。这里量的是我们自己的流水线：句子 → 规则 → 后台编码 → 数据库。"),
             f"- 任务数：{len(ms)}；失败：{bad}；访谈创建/发句子的错误：{len(errors)}；写进库的编码回答：{coded}。",
             f"- 从“句子进来”到“编码写入数据库”（含排队）：中位数 {statistics.median(ms) / 1000 if ms else 0:.2f} 秒，p95 {p(0.95):.2f} 秒，最大 {p(0.999):.2f} 秒（目标 ≤ 15 秒，含真实模型时间）。",
             f"- 模型调用：共 {CALLS['n']} 次，平均每场 {CALLS['n'] / N:.1f} 次（只在“被问到”或“明显在讲关注问题”的受访者句子上调用）。",
             f"- 发句子用时 {sent:.1f} 秒，全部处理完 {wall:.1f} 秒；后台线程 {board.WORKERS} 个。",
             "", ("没有测：实时推送（看板用 3 秒轮询）。" if REAL else "没有测：真实 DeepSeek 的延迟、失败率和每场费用（要在装好密钥的机器上用 LOAD_REAL=1 再跑一次）；实时推送（看板用 3 秒轮询）；线上单容器 + SQLite 的真实并发。")]
    text = "\n".join(lines) + "\n"
    if OUT:
        Path(OUT).write_text(text, encoding="utf-8")
    sys.stdout.buffer.write(text.encode("utf-8"))
    return 0 if bad == 0 and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
