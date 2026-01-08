# app/routers/dashboard.py

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_admin_user
from app.models import User, BlueWarMatch
from app.utils.time import fmt_kst


def _status_label(s: str) -> str:
    v = (s or "").strip().lower()
    if v == "finished":
        return "종료"
    if v == "running":
        return "진행중"
    if v == "aborted":
        return "중단"
    return s or "-"


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
    recent_matches_raw = (
        db.query(BlueWarMatch)
        .order_by(BlueWarMatch.started_at.desc())
        .limit(10)
        .all()
    )

    def mode_label(mode: str) -> str:
        m = (mode or "").strip().lower()
        if m == "pvp":
            return "PVP"
        if m == "practice":
            return "연습"
        return mode

    # 표시용: 모드별 시퀀스 번호 -> PVP는 #1부터, 연습은 #10001부터
    match_ids = [int(m.id) for m in recent_matches_raw]
    seq_by_match_id = {}
    if match_ids:
        seq_subq = (
            db.query(
                BlueWarMatch.id.label("id"),
                BlueWarMatch.mode.label("mode"),
                func.row_number()
                .over(partition_by=BlueWarMatch.mode, order_by=BlueWarMatch.id.asc())
                .label("seq"),
            )
        ).subquery()
        seq_rows = (
            db.query(seq_subq.c.id, seq_subq.c.seq)
            .filter(seq_subq.c.id.in_(match_ids))
            .all()
        )
        seq_by_match_id = {int(r.id): int(r.seq) for r in seq_rows}

    recent_matches = []
    for m in recent_matches_raw:
        seq = seq_by_match_id.get(int(m.id), int(m.id))
        disp = (10000 + seq) if (m.mode or "").strip().lower() == "practice" else seq
        recent_matches.append(
            {
                "id": m.id,
                "display_id": f"#{disp}",
                "mode_label": mode_label(m.mode),
                "status_label": _status_label(m.status),
                "win_gap": m.win_gap,
                "total_rounds": m.total_rounds,
                "started_at": fmt_kst(m.started_at, "%Y-%m-%d %H:%M:%S"),
            }
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
