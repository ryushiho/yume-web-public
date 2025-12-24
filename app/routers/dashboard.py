# app/routers/dashboard.py

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_admin_user
from app.models import User, BlueWarMatch

router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
)

templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin_user),
):
    """
    관리자 대시보드 화면.

    - 세션에 로그인 정보가 없으면 /auth/login 으로 리다이렉트
    - 간단한 통계 + 최근 매치 목록을 보여준다.
    """

    # 유저 수
    total_users = db.query(User).count()

    # 블루전 매치 수
    total_matches = db.query(BlueWarMatch).count()

    # 최근 매치 10개 (최신순)
    recent_matches = (
        db.query(BlueWarMatch)
        .order_by(BlueWarMatch.started_at.desc())
        .limit(10)
        .all()
    )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "total_users": total_users,
            "total_matches": total_matches,
            "recent_matches": recent_matches,
        },
    )
