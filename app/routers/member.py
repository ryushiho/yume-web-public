# app/routers/member.py

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_member_user, get_current_member_or_admin
from app.security import hash_password, verify_password
from config import settings
from app import models

router = APIRouter(
    prefix="/member",
    tags=["member"],
)

templates = Jinja2Templates(directory="app/templates")

def _sync_member_admin_flag(db: Session, m: models.MemberUser) -> None:
    """환경설정 기반으로 회원 관리자 권한을 부여한다(필요 시 DB 반영)."""
    should_admin = False
    if settings.BOOTSTRAP_ADMIN_DISCORD_ID and (m.discord_id == settings.BOOTSTRAP_ADMIN_DISCORD_ID):
        should_admin = True
    if m.discord_id in getattr(settings, "ADMIN_DISCORD_IDS", set()):
        should_admin = True

    if should_admin and (not getattr(m, "is_admin", False)):
        m.is_admin = True
        db.commit()
        db.refresh(m)



def _is_valid_discord_id(s: str) -> bool:
    """로그인용 ID 검증.

    기존: Discord snowflake(숫자)만 허용
    변경: 일반 아이디도 허용 (요청사항)

    - 2~32자
    - 영문/숫자/한글/_(underscore)/-(dash) 허용
    """
    v = (s or "").strip()
    if not (2 <= len(v) <= 32):
        return False
    return bool(re.fullmatch(r"[0-9A-Za-z가-힣_-]+", v))


def _normalize_discord_id(s: str) -> str:
    return (s or "").strip()


def _normalize_nickname(s: str) -> str:
    return (s or "").strip()


@router.get("/register")
def register_form(request: Request):
    return templates.TemplateResponse(
        "member_register.html",
        {"request": request, "error": None},
    )


@router.post("/register")
def register(
    request: Request,
    db: Session = Depends(get_db),
    discord_id: str = Form(...),
    nickname: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    discord_id = _normalize_discord_id(discord_id)
    nickname = _normalize_nickname(nickname)

    if not _is_valid_discord_id(discord_id):
        return templates.TemplateResponse(
            "member_register.html",
            {"request": request, "error": "아이디는 2~32자, 영문/숫자/한글/_/-만 사용할 수 있어."},
            status_code=400,
        )

    if not nickname or len(nickname) > 30:
        return templates.TemplateResponse(
            "member_register.html",
            {"request": request, "error": "닉네임은 1~30자로 입력해줘."},
            status_code=400,
        )

    if not password or len(password) < 8:
        return templates.TemplateResponse(
            "member_register.html",
            {"request": request, "error": "비밀번호는 8자 이상으로 설정해줘."},
            status_code=400,
        )

    if password != password_confirm:
        return templates.TemplateResponse(
            "member_register.html",
            {"request": request, "error": "비밀번호 확인이 일치하지 않아."},
            status_code=400,
        )

    exists = (
        db.query(models.MemberUser)
        .filter(models.MemberUser.discord_id == discord_id)
        .first()
    )
    if exists:
        return templates.TemplateResponse(
            "member_register.html",
            {"request": request, "error": "이미 가입된 아이디야. 로그인해줘."},
            status_code=409,
        )

    pw_hash = hash_password(password)
    m = models.MemberUser(
        discord_id=discord_id,
        nickname=nickname,
        password_hash=pw_hash,
        is_active=True,
        created_at=datetime.utcnow(),
    )
    db.add(m)
    db.commit()
    db.refresh(m)

    # 가입 직후 로그인 처리
    _sync_member_admin_flag(db, m)

    request.session["member"] = {"member_id": m.id, "id": m.discord_id, "nickname": m.nickname, "is_admin": bool(getattr(m, "is_admin", False))}
    return RedirectResponse(url="/member/dashboard", status_code=303)


@router.get("/login")
def login_form(request: Request):
    # 이미 로그인 상태면 대시보드로
    if request.session.get("member"):
        return RedirectResponse(url="/member/dashboard", status_code=303)

    return templates.TemplateResponse(
        "member_login.html",
        {"request": request, "error": None},
    )


@router.post("/login")
def login(
    request: Request,
    db: Session = Depends(get_db),
    discord_id: str = Form(...),
    password: str = Form(...),
):
    discord_id = _normalize_discord_id(discord_id)

    m: Optional[models.MemberUser] = (
        db.query(models.MemberUser)
        .filter(models.MemberUser.discord_id == discord_id)
        .first()
    )

    if (not m) or (not m.is_active) or (not verify_password(password, m.password_hash)):
        return templates.TemplateResponse(
            "member_login.html",
            {"request": request, "error": "아이디 또는 비밀번호가 올바르지 않습니다."},
            status_code=401,
        )

    m.last_login_at = datetime.utcnow()
    db.add(m)
    db.commit()

    request.session["member"] = {"member_id": m.id, "id": m.discord_id, "nickname": m.nickname, "is_admin": bool(getattr(m, "is_admin", False))}
    return RedirectResponse(url="/member/dashboard", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.pop("member", None)
    return RedirectResponse(url="/", status_code=303)


@router.get("/dashboard")
def member_dashboard(
    request: Request,
    viewer=Depends(get_current_member_or_admin),
):
    # admin도 이 페이지 볼 수 있게
    member = request.session.get("member")
    admin = request.session.get("user")
    return templates.TemplateResponse(
        "member_dashboard.html",
        {
            "request": request,
            "member": member,
            "admin": admin,
        },
    )
