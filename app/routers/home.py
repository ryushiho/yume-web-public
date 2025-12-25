# app/routers/home.py

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from config import settings

router = APIRouter(tags=["home"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/")
def home(request: Request):
    admin = request.session.get("user")
    member = request.session.get("member")

    # 대시보드 버튼의 목적지를 결정
    if admin:
        dashboard_url = "/dashboard/"
    elif member:
        dashboard_url = "/member/dashboard"
    else:
        dashboard_url = "/member/login"

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "invite_url": settings.INVITE_URL,
            "dashboard_url": dashboard_url,
            "admin_url": "/dashboard/" if admin else "/auth/login",
        },
    )
