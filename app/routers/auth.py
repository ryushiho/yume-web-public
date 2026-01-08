# app/routers/auth.py

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from config import settings
from app.database import get_db
from app import models
from app.security import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


def _bootstrap_admin_if_needed(db: Session, username: str) -> Optional[models.AdminUser]:
    """
    settings.ADMIN_USERS(하드코딩/환경변수 기반)로 운영하던 기존 방식과의 호환.
    - DB에 admin_users 레코드가 없으면 settings.ADMIN_USERS에서 검증 후 자동 생성 가능하도록 한다.
    """
    username = (username or "").strip()
    if not username:
        return None
    au = db.query(models.AdminUser).filter(models.AdminUser.username == username).first()
    if au:
        return au

    # settings.ADMIN_USERS에 있는 계정이면, 최초 1회 DB로 이관(해시 생성)
    if username in (settings.ADMIN_USERS or {}):
        pw_plain = settings.ADMIN_USERS[username]
        au = models.AdminUser(username=username, password_hash=hash_password(pw_plain))
        db.add(au)
        db.commit()
        db.refresh(au)
        return au
    return None


@router.get("/login")
def login_form(request: Request):
    # 이미 로그인 상태면 대시보드로
    if request.session.get("user"):
        return RedirectResponse(url="/dashboard/", status_code=303)

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
        },
    )


@router.post("/login")
def login(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
):
    username = (username or "").strip()
    password = password or ""

    # 1) DB에 레코드가 있으면 DB 우선 검증
    au = db.query(models.AdminUser).filter(models.AdminUser.username == username).first()
    if not au:
        # 2) 없으면, 기존 settings.ADMIN_USERS 기반으로 부트스트랩 시도
        au = _bootstrap_admin_if_needed(db, username)

    ok = False
    if au:
        ok = verify_password(password, au.password_hash)
    else:
        # (완전 레거시) DB에도 없고 settings에도 없으면 실패
        ok = False

    if ok:
        request.session["user"] = {"id": au.username}
        return RedirectResponse(url="/dashboard/", status_code=303)

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": "아이디 또는 비밀번호가 올바르지 않습니다.",
        },
        status_code=401,
    )


@router.get("/profile")
def profile_form(
    request: Request,
    db: Session = Depends(get_db),
):
    session_user = request.session.get("user")
    if not session_user or not session_user.get("id"):
        return RedirectResponse(url="/auth/login", status_code=303)

    username = str(session_user.get("id"))
    au = db.query(models.AdminUser).filter(models.AdminUser.username == username).first()
    if not au:
        au = _bootstrap_admin_if_needed(db, username)

    if not au:
        request.session.pop("user", None)
        return RedirectResponse(url="/auth/login", status_code=303)

    return templates.TemplateResponse(
        "admin_profile.html",
        {"request": request, "admin": au, "message": None, "error": None},
    )


@router.post("/profile")
def profile_update(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    current_password: str = Form(...),
    new_password: str = Form(""),
    new_password_confirm: str = Form(""),
):
    session_user = request.session.get("user")
    if not session_user or not session_user.get("id"):
        return RedirectResponse(url="/auth/login", status_code=303)

    old_username = str(session_user.get("id"))
    au = db.query(models.AdminUser).filter(models.AdminUser.username == old_username).first()
    if not au:
        au = _bootstrap_admin_if_needed(db, old_username)

    if not au:
        request.session.pop("user", None)
        return RedirectResponse(url="/auth/login", status_code=303)

    # 현재 비밀번호 검증
    if not verify_password(current_password, au.password_hash):
        return templates.TemplateResponse(
            "admin_profile.html",
            {"request": request, "admin": au, "message": None, "error": "현재 비밀번호가 올바르지 않습니다."},
            status_code=400,
        )

    username = (username or "").strip()
    if not username:
        return templates.TemplateResponse(
            "admin_profile.html",
            {"request": request, "admin": au, "message": None, "error": "아이디(표시명)를 입력해줘."},
            status_code=400,
        )

    # 아이디 변경 (중복 체크)
    if username != au.username:
        exists = db.query(models.AdminUser).filter(models.AdminUser.username == username).first()
        if exists:
            return templates.TemplateResponse(
                "admin_profile.html",
                {"request": request, "admin": au, "message": None, "error": "이미 사용 중인 아이디야."},
                status_code=400,
            )
        au.username = username

    # 비밀번호 변경 (선택)
    new_password = new_password or ""
    new_password_confirm = new_password_confirm or ""
    if new_password or new_password_confirm:
        if new_password != new_password_confirm:
            return templates.TemplateResponse(
                "admin_profile.html",
                {"request": request, "admin": au, "message": None, "error": "새 비밀번호가 서로 달라."},
                status_code=400,
            )
        if len(new_password) < 4:
            return templates.TemplateResponse(
                "admin_profile.html",
                {"request": request, "admin": au, "message": None, "error": "새 비밀번호는 4글자 이상으로 해줘."},
                status_code=400,
            )
        au.password_hash = hash_password(new_password)

    db.add(au)
    db.commit()
    db.refresh(au)

    # 세션 갱신
    request.session["user"] = {"id": au.username}

    return templates.TemplateResponse(
        "admin_profile.html",
        {"request": request, "admin": au, "message": "저장 완료!", "error": None},
    )


@router.get("/logout")
def logout(request: Request):
    """로그아웃: 세션 비우고 로그인 페이지로"""
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)
