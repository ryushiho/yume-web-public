# app/routers/auth.py

from typing import Optional, Dict, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from config import settings  # /opt/yume-web/config.py 에서 settings 사용

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
)

templates = Jinja2Templates(directory="app/templates")


def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """세션에서 현재 로그인한 유저 정보 조회"""
    return request.session.get("user")


@router.get("/login")
def login_form(request: Request):
    """
    로그인 폼 표시.
    이미 로그인 상태면 /dashboard 로 보냄.
    """
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard/", status_code=303)

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
        },
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """
    로그인 처리:
    - ID/PW 는 config.settings.ADMIN_USERS 기준
      예) {"시호": "miyo", "메조": "hoshino"}
    """
    users = settings.ADMIN_USERS

    if username in users and users[username] == password:
        # 로그인 성공 → 세션에 저장
        request.session["user"] = {
            "id": username,
        }
        return RedirectResponse(url="/dashboard/", status_code=303)

    # 실패 시 다시 로그인 폼 + 에러 메시지
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": "아이디 또는 비밀번호가 올바르지 않습니다.",
        },
        status_code=401,
    )


@router.get("/logout")
def logout(request: Request):
    """로그아웃: 세션 비우고 로그인 페이지로"""
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)
