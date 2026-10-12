from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import Interview, InterviewObserver, User
from .security import decode_access_token

bearer = HTTPBearer(auto_error=False)
STAFF = ("admin", "researcher")


@dataclass
class Ctx:
    user: User
    ip: str

    @property
    def org_id(self) -> str:
        return self.user.org_id

    @property
    def actor(self) -> str:
        return f"user:{self.user.id}"


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def current_ctx(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> Ctx:
    if not cred:
        raise HTTPException(401, "未登录")
    try:
        claims = decode_access_token(cred.credentials)
    except jwt.PyJWTError:
        raise HTTPException(401, "登录已失效")
    user = db.get(User, claims["sub"])
    if not user or not user.active:
        raise HTTPException(401, "账号不可用")
    return Ctx(user=user, ip=client_ip(request))  # role/org always come from the DB, not the token


def require_roles(*roles: str):
    def dep(ctx: Ctx = Depends(current_ctx)) -> Ctx:
        if ctx.user.role not in roles:
            raise HTTPException(403, "没有权限")
        return ctx
    return dep


def visible_interview(db: Session, ctx: Ctx, interview_id: str) -> Interview:
    """Tenant + assignment scoping. Returns 404 (not 403) for anything the caller must not know exists."""
    itv = db.get(Interview, interview_id)
    if not itv or itv.org_id != ctx.org_id:
        raise HTTPException(404, "访谈不存在")
    role = ctx.user.role
    if role in STAFF:
        return itv
    if role == "moderator" and itv.moderator_id == ctx.user.id:
        return itv
    if role == "observer" and any(o.user_id == ctx.user.id for o in itv.observers):
        return itv
    raise HTTPException(404, "访谈不存在")
