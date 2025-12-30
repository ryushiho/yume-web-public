from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_current_admin_user
from app.config import settings as app_settings
from config import settings as root_settings


router = APIRouter(prefix="/admin", tags=["admin-integration"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/integration", response_class=HTMLResponse)
def admin_integration(request: Request, me=Depends(get_current_admin_user)):
    """관리자 전용: 봇(Web/API) 연동에 필요한 값과 예시를 한 페이지에서 확인."""

    # 외부에서 접근 가능한 기본 URL (가능하면 .env의 PUBLIC_BASE_URL 사용)
    base_url = ((getattr(root_settings, "PUBLIC_BASE_URL", None) or "") or str(request.base_url).rstrip("/")).rstrip("/")

    # 토큰은 화면에 그대로 노출되므로, 관리자 페이지에서만 노출되도록 라우팅 자체를 admin-only로 둔다.
    wordlist_token = (app_settings.WORDLIST_TOKEN or "").strip()
    api_token = (getattr(root_settings, "API_TOKEN", None) or "").strip()

    ctx = {
        "request": request,
        "me": me,
        "base_url": base_url,
        "wordlist_token": wordlist_token,
        "api_token": api_token,
    }
    return templates.TemplateResponse("admin_integration.html", ctx)
