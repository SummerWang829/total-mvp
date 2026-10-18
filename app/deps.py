from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from sqlalchemy import select

from .models import Interview, InterviewObserver, ProjectMember, User
from .security import decode_access_token

bearer = HTTPBearer(auto_error=False)
STAFF = ("admin", "researcher")
# People who belong to a project as members (the project's own list decides what they may open): researchers do everything,
# a recruiter only recruits, a viewer (the client) only reads the conclusions.
MEMBER_ROLES = ("researcher", "recruiter", "viewer")
READERS = ("admin", "researcher", "viewer")        # may read a project's findings, report, monitoring and survey results
RECRUITERS = ("admin", "researcher", "recruiter")  # may work with the project's people: import, screen, tag, quotas
ANY_PROJECT_ROLE = ("admin", "researcher", "recruiter", "viewer")


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


def can_see_project(db: Session, ctx: Ctx, project_id: str) -> bool:
    """Admins see every project of their company; a researcher only the projects they are a member of (everyone else is assigned per interview)."""
    role = ctx.user.role
    if role == "admin":
        return True
    if role in MEMBER_ROLES:
        return db.scalar(select(ProjectMember.id).where(ProjectMember.project_id == project_id, ProjectMember.user_id == ctx.user.id)) is not None
    return False


def ensure_project(db: Session, ctx: Ctx, project_id: str, what: str = "项目不存在") -> None:
    """404 (not 403) when this person has no access to the project: they must not learn that it exists."""
    if not can_see_project(db, ctx, project_id):
        raise HTTPException(404, what)


def visible_interview(db: Session, ctx: Ctx, interview_id: str) -> Interview:
    """Tenant + assignment scoping. Returns 404 (not 403) for anything the caller must not know exists."""
    itv = db.get(Interview, interview_id)
    if not itv or itv.org_id != ctx.org_id:
        raise HTTPException(404, "访谈不存在")
    role = ctx.user.role
    if role in STAFF:
        ensure_project(db, ctx, itv.project_id, "访谈不存在")
        return itv
    if role == "moderator" and itv.moderator_id == ctx.user.id:
        return itv
    if role == "observer" and any(o.user_id == ctx.user.id for o in itv.observers):
        return itv
    raise HTTPException(404, "访谈不存在")
