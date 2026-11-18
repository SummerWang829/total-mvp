"""Put the outline workbench's demo data into a project: python scripts/outline_wb_demo.py [project_id]  (default: the first project). Fake data only; safe to run twice."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import Base, SessionLocal, engine, ensure_columns  # noqa: E402
from app.main import app  # noqa: E402,F401  (registers every table)
from app.models import Project  # noqa: E402
from app.outline_wb import demo  # noqa: E402


def main() -> int:
    Base.metadata.create_all(engine)   # the same two steps the server runs at start, so an older database gets the new tables and columns
    ensure_columns()
    with SessionLocal() as db:
        pid = sys.argv[1] if len(sys.argv) > 1 else db.scalar(select(Project.id).order_by(Project.created_at))
        if not pid:
            print("没有项目：先建一个项目，或把项目编号写在命令后面。")
            return 1
        made = demo.build(db, pid)
        db.commit()
    print("演示数据已存在，什么都没改。" if not made else f"已生成：大纲 {made['outline']}，pilot 场次 {len(made['interviews'])} 场。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
