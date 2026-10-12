from sqlalchemy.orm import Session

from .models import AuditLog


def audit(db: Session, org_id: str, actor: str, action: str, target: str = "", ip: str = "", **meta) -> None:
    db.add(AuditLog(org_id=org_id, actor=actor, action=action, target=target, ip=ip, meta=meta))
