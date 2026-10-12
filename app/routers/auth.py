from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import Ctx, current_ctx, require_roles
from ..models import Org, User
from ..security import create_access_token, hash_password, verify_password

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
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "org_id": u.org_id}


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
def login(body: Login, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.email == body.email.lower()))
    if not u or not u.active or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "邮箱或密码不对")
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
