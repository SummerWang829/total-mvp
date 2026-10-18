from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import mailer, ratelimit
from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import Ctx, client_ip, current_ctx, require_roles
from ..models import Org, User, UserInvite, utcnow
from ..security import create_access_token, hash_password, new_secret_token, token_hash, verify_password

router = APIRouter(prefix="/api", tags=["auth"])
ROLES = {"admin", "researcher", "moderator", "observer"}


class Register(BaseModel):
    org_name: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=200)


class Login(BaseModel):
    email: str
    password: str


class NewUser(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=200)
    role: str


def user_out(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "org_id": u.org_id, "active": u.active}


@router.post("/auth/register")
def register(body: Register, db: Session = Depends(get_db)):
    if not settings.allow_signup:
        raise HTTPException(403, "注册已关闭")
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "邮箱已被使用")
    org = Org(name=body.org_name)
    db.add(org)
    db.flush()
    u = User(org_id=org.id, email=email, name=body.name, role="admin", password_hash=hash_password(body.password))
    db.add(u)
    audit(db, org.id, f"user:{u.id}", "org.create", org.id)
    db.commit()
    return {"token": create_access_token(u.id, org.id, u.role), "user": user_out(u)}


@router.post("/auth/login")
def login(body: Login, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if wait := ratelimit.retry_after_for_login(body.email, ip):
        raise HTTPException(429, ratelimit.MESSAGE, headers={"Retry-After": str(wait)})
    u = db.scalar(select(User).where(User.email == body.email.lower()))
    if not u or not u.active or not verify_password(body.password, u.password_hash):
        ratelimit.login_failed(body.email, ip)
        raise HTTPException(401, "邮箱或密码不对")
    ratelimit.login_ok(body.email)
    audit(db, u.org_id, f"user:{u.id}", "login")
    db.commit()
    return {"token": create_access_token(u.id, u.org_id, u.role), "user": user_out(u)}


@router.get("/me")
def me(ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)):
    org = db.get(Org, ctx.org_id)
    return {**user_out(ctx.user), "org_name": org.name}


@router.get("/users")
def users(ctx: Ctx = Depends(require_roles("admin", "researcher")), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).where(User.org_id == ctx.org_id).order_by(User.created_at))
    return [user_out(u) for u in rows]


@router.post("/users", status_code=201)
def create_user(body: NewUser, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(422, "角色不对")
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "邮箱已被使用")
    u = User(org_id=ctx.org_id, email=email, name=body.name, role=body.role, password_hash=hash_password(body.password))
    db.add(u)
    audit(db, ctx.org_id, ctx.actor, "user.create", u.id, role=body.role)
    db.commit()
    return user_out(u)


# ---------- changing members: role, active, one-time links ----------
INVITE_DAYS = 7
FORGOT_HOURS = 1  # a link asked for by the account owner themselves lives shorter than one an admin hands out
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class Patch(BaseModel):
    role: str | None = None
    active: bool | None = None


def _member(db: Session, ctx: Ctx, uid: str) -> User:
    u = db.get(User, uid)
    if not u or u.org_id != ctx.org_id:
        raise HTTPException(404, "没有这个成员")
    return u


def _other_active_admins(db: Session, ctx: Ctx, exclude: str) -> int:
    return len(db.scalars(select(User.id).where(User.org_id == ctx.org_id, User.role == "admin", User.active.is_(True), User.id != exclude)).all())


@router.patch("/users/{uid}")
def patch_user(uid: str, body: Patch, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    """Change a member's role or switch the account off / on. Never locks the company out: you cannot change yourself, and the last active admin stays."""
    u = _member(db, ctx, uid)
    if u.id == ctx.user.id:
        raise HTTPException(409, "不能修改自己的角色或停用自己的账号")
    if body.role is not None and body.role not in ROLES:
        raise HTTPException(422, "角色不对")
    losing_admin = u.role == "admin" and u.active and ((body.role is not None and body.role != "admin") or body.active is False)
    if losing_admin and _other_active_admins(db, ctx, u.id) == 0:
        raise HTTPException(409, "公司至少要保留一位管理员")
    if body.role is not None and body.role != u.role:
        audit(db, ctx.org_id, ctx.actor, "user.role", u.id, frm=u.role, to=body.role)
        u.role = body.role
    if body.active is not None and body.active != u.active:
        audit(db, ctx.org_id, ctx.actor, "user.activate" if body.active else "user.deactivate", u.id)
        u.active = body.active
    db.commit()
    return user_out(u) | {"active": u.active}


class InviteIn(BaseModel):
    email: str = Field(pattern=EMAIL)
    role: str
    send_email: bool = False


class ForgotIn(BaseModel):
    email: str = Field(max_length=200)


def _make_invite(db: Session, ctx: Ctx, kind: str, email: str, role: str, user_id: str | None = None) -> tuple[UserInvite, str]:
    token = new_secret_token()
    inv = UserInvite(org_id=ctx.org_id, kind=kind, email=email, role=role, user_id=user_id, token_hash=token_hash(token), created_by=ctx.user.id,
                     expires_at=utcnow() + timedelta(days=INVITE_DAYS))
    db.add(inv)
    db.flush()
    audit(db, ctx.org_id, ctx.actor, f"user.{kind}_link", inv.id, role=role)
    db.commit()
    return inv, token


def _email_link(db: Session, inv: UserInvite, token: str, hours: int | None = None) -> None:
    """Queue the email carrying this link (the caller commits). Raises RuntimeError when account emails are not configured."""
    kind = inv.kind
    body = mailer.body_for(kind, db.get(Org, inv.org_id).name, mailer.link_for(token), days=INVITE_DAYS, hours=hours)
    mailer.queue(db, inv.email, kind, body, inv.org_id)


def _need_mail() -> None:
    if not mailer.configured():
        raise HTTPException(409, "还没有开通发信服务，请改为复制链接发给对方")


@router.get("/mail/status")
def mail_status(ctx: Ctx = Depends(require_roles("admin"))):
    """Whether links can be emailed (the team page shows the "send by email" choice only then)."""
    return {"configured": mailer.configured()}


@router.post("/auth/forgot", status_code=202)
def forgot(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    """Always the same answer, whether or not the address has an account (no way to find out who is registered).

    A real, active account gets an email with a one-hour, one-time link; anything else gets nothing. Asking too often is dropped quietly.
    """
    ip, email = client_ip(request), body.email.strip().lower()
    answer = {"ok": True}
    if not mailer.configured() or not ratelimit.forgot_allowed(email, ip):
        return answer
    u = db.scalar(select(User).where(User.email == email))
    if not u or not u.active:
        return answer
    for old in db.scalars(select(UserInvite).where(UserInvite.user_id == u.id, UserInvite.kind == "reset", UserInvite.used_at.is_(None), UserInvite.created_by == f"self:{u.id}")).all():
        db.delete(old)  # one live "forgot" link per account
    token = new_secret_token()
    inv = UserInvite(org_id=u.org_id, kind="reset", email=u.email, role=u.role, user_id=u.id, token_hash=token_hash(token), created_by=f"self:{u.id}",
                     expires_at=utcnow() + timedelta(hours=FORGOT_HOURS))
    db.add(inv)
    db.flush()
    _email_link(db, inv, token, hours=FORGOT_HOURS)
    audit(db, u.org_id, f"user:{u.id}", "user.forgot_password", u.id)
    db.commit()
    return answer


def invite_out(inv: UserInvite, token: str | None = None) -> dict:
    out = {"id": inv.id, "kind": inv.kind, "email": inv.email, "role": inv.role, "expires_at": inv.expires_at.isoformat()}
    if token:  # shown once: only its hash is stored
        out["invite_path"] = "/invite.html?t=" + token
    return out


@router.post("/users/invites", status_code=201)
def create_invite(body: InviteIn, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(422, "角色不对")
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "这个邮箱已经是成员了")
    if body.send_email:
        _need_mail()
    inv, token = _make_invite(db, ctx, "join", email, body.role)
    return _maybe_emailed(db, inv, token, body.send_email)


def _maybe_emailed(db: Session, inv: UserInvite, token: str, send: bool) -> dict:
    """The link as the admin sees it; with send=True it is also queued by email and flagged "emailed"."""
    out = invite_out(inv, token)
    if send:
        _email_link(db, inv, token)
        audit(db, inv.org_id, "system", "user.link_emailed", inv.id)
        db.commit()
    return out | {"emailed": send}


@router.get("/users/invites")
def list_invites(ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    rows = db.scalars(select(UserInvite).where(UserInvite.org_id == ctx.org_id, UserInvite.used_at.is_(None), UserInvite.expires_at > utcnow()).order_by(UserInvite.created_at)).all()
    return [invite_out(i) for i in rows]


@router.delete("/users/invites/{iid}", status_code=204)
def revoke_invite(iid: str, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    inv = db.get(UserInvite, iid)
    if not inv or inv.org_id != ctx.org_id:
        raise HTTPException(404, "没有这条邀请")
    audit(db, ctx.org_id, ctx.actor, "user.invite_revoke", inv.id)
    db.delete(inv)
    db.commit()


@router.post("/users/{uid}/reset-link", status_code=201)
def reset_link(uid: str, send_email: bool = False, ctx: Ctx = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    u = _member(db, ctx, uid)
    if send_email:
        _need_mail()
    inv, token = _make_invite(db, ctx, "reset", u.email, u.role, u.id)
    return _maybe_emailed(db, inv, token, send_email)


# ---------- the public side of those links ----------
def _live_invite(db: Session, token: str) -> UserInvite:
    inv = db.scalar(select(UserInvite).where(UserInvite.token_hash == token_hash(token)))
    if not inv or inv.used_at or inv.expires_at < utcnow():
        raise HTTPException(404, "链接无效或已过期")  # the same answer for a wrong, used, revoked or expired link
    return inv


@router.get("/public/invite/{token}")
def invite_info(token: str, db: Session = Depends(get_db)):
    inv = _live_invite(db, token)
    return {"kind": inv.kind, "org_name": db.get(Org, inv.org_id).name, "email": inv.email, "role": inv.role}


class Accept(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    password: str = Field(min_length=8, max_length=200)


@router.post("/public/invite/{token}/accept")
def invite_accept(token: str, body: Accept, db: Session = Depends(get_db)):
    inv = _live_invite(db, token)
    if inv.kind == "join":
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(422, "请填写姓名")
        if db.scalar(select(User).where(User.email == inv.email)):
            raise HTTPException(409, "这个邮箱已经是成员了")
        u = User(org_id=inv.org_id, email=inv.email, name=name, role=inv.role, password_hash=hash_password(body.password))
        db.add(u)
        db.flush()
    else:
        u = db.get(User, inv.user_id)
        if not u or u.org_id != inv.org_id or not u.active:
            raise HTTPException(404, "链接无效或已过期")
        u.password_hash = hash_password(body.password)
    inv.used_at = utcnow()
    audit(db, inv.org_id, f"user:{u.id}", "user.joined" if inv.kind == "join" else "user.password_reset", u.id)
    db.commit()
    return {"token": create_access_token(u.id, u.org_id, u.role), "user": user_out(u)}
