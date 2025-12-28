# app/routers/records.py

from __future__ import annotations

from typing import Dict, List, Optional, Set, TypedDict

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_admin_user
from app import models
from app.utils.time import fmt_kst


router = APIRouter(
    prefix="/records",
    tags=["records"],
)

templates = Jinja2Templates(directory="app/templates")


class MatchRow(TypedDict):
    match: models.BlueWarMatch
    starter_name: str
    winner_name: str
    loser_name: str
    started_at: str
    finished_at: str


def _resolve_display_name(
    *,
    discord_id: Optional[str],
    users_by_discord: Dict[str, models.User],
    fallback_names_by_discord: Dict[str, str],
) -> str:
    if not discord_id:
        return "-"
    if str(discord_id).strip().lower() == "yume":
        return "유메"
    u = users_by_discord.get(discord_id)
    if u and u.nickname:
        return u.nickname
    if discord_id in fallback_names_by_discord:
        return fallback_names_by_discord[discord_id]
    return discord_id


@router.get("/", response_class=HTMLResponse)
def list_records(
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin_user),
    limit: int = 200,
    mode: str = Query(default="pvp", description="pvp|practice|all"),
):
    """블루전 매치 목록 페이지(최신순)."""

    mode_norm = (mode or "pvp").strip().lower()
    if mode_norm not in {"pvp", "practice", "all"}:
        mode_norm = "pvp"

    # 탭 표시용 카운트
    pvp_count = db.query(models.BlueWarMatch).filter(models.BlueWarMatch.mode == "pvp").count()
    practice_count = db.query(models.BlueWarMatch).filter(models.BlueWarMatch.mode == "practice").count()

    q = db.query(models.BlueWarMatch)
    if mode_norm in {"pvp", "practice"}:
        q = q.filter(models.BlueWarMatch.mode == mode_norm)

    total_matches = q.count()

    matches: List[models.BlueWarMatch] = (
        q
        .order_by(models.BlueWarMatch.id.desc())
        .limit(max(1, min(int(limit), 1000)))
        .all()
    )

    # 한 번에 표시 이름을 resolve 하기 위해 필요한 discord_id들을 모은다.
    discord_ids: Set[str] = set()
    match_ids: List[int] = []
    for m in matches:
        match_ids.append(m.id)
        if m.starter_discord_id:
            discord_ids.add(m.starter_discord_id)
        if m.winner_discord_id:
            discord_ids.add(m.winner_discord_id)
        if m.loser_discord_id:
            discord_ids.add(m.loser_discord_id)

    users_by_discord: Dict[str, models.User] = {}
    if discord_ids:
        users = (
            db.query(models.User)
            .filter(models.User.discord_id.in_(list(discord_ids)))
            .all()
        )
        users_by_discord = {u.discord_id: u for u in users}

    # users 테이블에 없더라도 participant.name이 있는 경우가 있으므로 fallback으로 쓴다.
    fallback_names_by_discord: Dict[str, str] = {}
    winner_guess_by_match_id: Dict[int, str] = {}
    if match_ids:
        parts = (
            db.query(models.BlueWarParticipant)
            .filter(models.BlueWarParticipant.match_id.in_(match_ids))
            .all()
        )
        for p in parts:
            if p.discord_id and p.name and p.discord_id not in fallback_names_by_discord:
                fallback_names_by_discord[p.discord_id] = p.name

            # winner_discord_id가 비어있는(특히 연습) 매치에서, 참가자 플래그로 승자를 추정한다.
            if p.match_id and p.is_winner and p.match_id not in winner_guess_by_match_id:
                if p.user and p.user.nickname:
                    winner_guess_by_match_id[p.match_id] = p.user.nickname
                elif p.ai_name:
                    winner_guess_by_match_id[p.match_id] = p.ai_name
                elif p.name:
                    winner_guess_by_match_id[p.match_id] = p.name
                elif p.discord_id:
                    winner_guess_by_match_id[p.match_id] = p.discord_id

    rows: List[MatchRow] = []
    for m in matches:
        winner_name = _resolve_display_name(
            discord_id=m.winner_discord_id,
            users_by_discord=users_by_discord,
            fallback_names_by_discord=fallback_names_by_discord,
        )
        # 특히 연습 매치에서 winner_discord_id가 None으로 올 수 있다. -> 참가자 정보로 보정
        if winner_name == "-" and m.status == "finished":
            guessed = winner_guess_by_match_id.get(int(m.id), "-")
            if isinstance(guessed, str) and guessed.strip().lower() == "yume":
                guessed = "유메"
            if guessed != "-":
                winner_name = guessed
            elif m.mode == "practice" and m.loser_discord_id:
                # 최후의 fallback: 패자가 있으면 승자는 유메(봇)로 표시
                winner_name = "유메"

        rows.append(
            {
                "match": m,
                "starter_name": _resolve_display_name(
                    discord_id=m.starter_discord_id,
                    users_by_discord=users_by_discord,
                    fallback_names_by_discord=fallback_names_by_discord,
                ),
                "winner_name": winner_name,
                "loser_name": _resolve_display_name(
                    discord_id=m.loser_discord_id,
                    users_by_discord=users_by_discord,
                    fallback_names_by_discord=fallback_names_by_discord,
                ),
                "started_at": fmt_kst(m.started_at, "%Y-%m-%d %H:%M:%S"),
                "finished_at": fmt_kst(m.finished_at, "%Y-%m-%d %H:%M:%S"),
            }
        )

    return templates.TemplateResponse(
        "records_list.html",
        {
            "request": request,
            "total_matches": total_matches,
            "rows": rows,
            "mode": mode_norm,
            "pvp_count": pvp_count,
            "practice_count": practice_count,
        },
    )


@router.get("/{match_id}", response_class=HTMLResponse)
def record_detail(
    match_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin_user),
):
    match: Optional[models.BlueWarMatch] = (
        db.query(models.BlueWarMatch)
        .filter(models.BlueWarMatch.id == match_id)
        .first()
    )

    if not match:
        # 템플릿에서 graceful 하게 처리
        return templates.TemplateResponse(
            "record_detail.html",
            {
                "request": request,
                "match": None,
            },
            status_code=404,
        )

    participants: List[models.BlueWarParticipant] = (
        db.query(models.BlueWarParticipant)
        .filter(models.BlueWarParticipant.match_id == match_id)
        .order_by(models.BlueWarParticipant.side.asc(), models.BlueWarParticipant.id.asc())
        .all()
    )

    return templates.TemplateResponse(
        "record_detail.html",
        {
            "request": request,
            "match": match,
            "participants": participants,
            "started_at": fmt_kst(match.started_at, "%Y-%m-%d %H:%M:%S"),
            "finished_at": fmt_kst(match.finished_at, "%Y-%m-%d %H:%M:%S"),
        },
    )
