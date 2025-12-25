# app/routers/bluewar.py
from __future__ import annotations

import math
from typing import Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_member_or_admin
from app import models


router = APIRouter(
    prefix="/bluewar",
    tags=["bluewar"],
)

templates = Jinja2Templates(directory="app/templates")


def _resolve_display_name(
    *,
    discord_id: Optional[str],
    users_by_discord: Dict[str, models.User],
    fallback_names_by_discord: Dict[str, str],
) -> str:
    if not discord_id:
        return "-"
    u = users_by_discord.get(discord_id)
    if u and u.nickname:
        return u.nickname
    if discord_id in fallback_names_by_discord:
        return fallback_names_by_discord[discord_id]
    return discord_id


@router.get("/matches/", response_class=HTMLResponse)
def list_bluewar_matches(
    request: Request,
    db: Session = Depends(get_db),
    viewer=Depends(get_current_member_or_admin),
    mode: str = Query(default="all", description="all|pvp|practice"),
    status: str = Query(default="all", description="all|finished|aborted|running"),
    q: str = Query(default="", description="검색어(Discord ID/메모/복기 로그)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=200),
):
    """
    블루전 매치 목록 페이지 (/bluewar/matches/)

    - 필터: mode/status
    - 검색: starter/winner/loser discord_id, note, review_log
    - 페이지네이션
    - 참가자 수 표시
    """

    # 참가자 수 서브쿼리(매치 1건당 1row)
    pcount_subq = (
        db.query(
            models.BlueWarParticipant.match_id.label("match_id"),
            func.count(models.BlueWarParticipant.id).label("pcount"),
        )
        .group_by(models.BlueWarParticipant.match_id)
        .subquery()
    )

    query = (
        db.query(
            models.BlueWarMatch,
            func.coalesce(pcount_subq.c.pcount, 0).label("pcount"),
        )
        .outerjoin(pcount_subq, pcount_subq.c.match_id == models.BlueWarMatch.id)
    )

    mode = (mode or "all").strip().lower()
    if mode in ("pvp", "practice"):
        query = query.filter(models.BlueWarMatch.mode == mode)

    status = (status or "all").strip().lower()
    if status in ("finished", "aborted", "running"):
        query = query.filter(models.BlueWarMatch.status == status)

    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                models.BlueWarMatch.starter_discord_id.ilike(like),
                models.BlueWarMatch.winner_discord_id.ilike(like),
                models.BlueWarMatch.loser_discord_id.ilike(like),
                models.BlueWarMatch.note.ilike(like),
                models.BlueWarMatch.review_log.ilike(like),
            )
        )

    query = query.order_by(
        models.BlueWarMatch.created_at.desc().nullslast(),
        models.BlueWarMatch.id.desc(),
    )

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))
    if page > total_pages:
        page = total_pages

    rows: List[Tuple[models.BlueWarMatch, int]] = (
        query.offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # 표시 이름 resolve (User.nickname 우선, 없으면 Participant.name fallback)
    discord_ids: Set[str] = set()
    for match, _pcount in rows:
        if match.starter_discord_id:
            discord_ids.add(match.starter_discord_id)
        if match.winner_discord_id:
            discord_ids.add(match.winner_discord_id)
        if match.loser_discord_id:
            discord_ids.add(match.loser_discord_id)

    users_by_discord: Dict[str, models.User] = {}
    if discord_ids:
        for u in db.query(models.User).filter(models.User.discord_id.in_(list(discord_ids))).all():
            users_by_discord[u.discord_id] = u

    fallback_names_by_discord: Dict[str, str] = {}
    if discord_ids:
        # 같은 discord_id가 여러 번 있을 수 있으니, 최신(큰 id) 것을 우선으로 잡는다.
        parts = (
            db.query(models.BlueWarParticipant)
            .filter(models.BlueWarParticipant.discord_id.in_(list(discord_ids)))
            .order_by(models.BlueWarParticipant.id.desc())
            .all()
        )
        for p in parts:
            if not p.discord_id:
                continue
            if p.discord_id in fallback_names_by_discord:
                continue
            if p.name:
                fallback_names_by_discord[p.discord_id] = p.name

    matches: List[Dict[str, object]] = []
    for match, pcount in rows:
        matches.append(
            {
                "id": match.id,
                "mode": match.mode,
                "status": match.status,
                "starter": _resolve_display_name(
                    discord_id=match.starter_discord_id,
                    users_by_discord=users_by_discord,
                    fallback_names_by_discord=fallback_names_by_discord,
                ),
                "winner": _resolve_display_name(
                    discord_id=match.winner_discord_id,
                    users_by_discord=users_by_discord,
                    fallback_names_by_discord=fallback_names_by_discord,
                ),
                "loser": _resolve_display_name(
                    discord_id=match.loser_discord_id,
                    users_by_discord=users_by_discord,
                    fallback_names_by_discord=fallback_names_by_discord,
                ),
                "win_gap": match.win_gap,
                "total_rounds": match.total_rounds,
                "started_at": match.started_at,
                "finished_at": match.finished_at,
                "created_at": match.created_at,
                "pcount": int(pcount),
                "note": match.note,
            }
        )

    return templates.TemplateResponse(
        "bluewar/matches.html",
        {
            "request": request,
            "viewer": viewer,
            "matches": matches,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "mode": mode,
            "status": status,
            "q": q,
        },
    )


@router.get("/matches/{match_id}", response_class=HTMLResponse)
def bluewar_match_detail(
    match_id: int,
    request: Request,
    db: Session = Depends(get_db),
    viewer=Depends(get_current_member_or_admin),
):
    match = db.query(models.BlueWarMatch).filter(models.BlueWarMatch.id == match_id).first()
    if not match:
        return templates.TemplateResponse(
            "bluewar/match_detail.html",
            {"request": request, "match": None, "error": "매치를 찾을 수 없어."},
            status_code=404,
        )

    participants = (
        db.query(models.BlueWarParticipant)
        .filter(models.BlueWarParticipant.match_id == match_id)
        .order_by(models.BlueWarParticipant.side.asc())
        .all()
    )

    # discord_id -> 표시 이름 매핑 (users 테이블 우선)
    discord_ids: Set[str] = set()
    for p in participants:
        if p.discord_id:
            discord_ids.add(p.discord_id)
    users_by_discord: Dict[str, models.User] = {}
    if discord_ids:
        for u in db.query(models.User).filter(models.User.discord_id.in_(list(discord_ids))).all():
            users_by_discord[u.discord_id] = u

    def resolve_name(p: models.BlueWarParticipant) -> str:
        if p.user_id and p.user and p.user.nickname:
            return p.user.nickname
        if p.discord_id and p.discord_id in users_by_discord and users_by_discord[p.discord_id].nickname:
            return users_by_discord[p.discord_id].nickname
        if p.name:
            return p.name
        if p.ai_name:
            return p.ai_name
        if p.discord_id:
            return p.discord_id
        return "-"

    view_parts = []
    for p in participants:
        view_parts.append(
            {
                "side": p.side,
                "name": resolve_name(p),
                "is_winner": bool(p.is_winner),
                "score": p.score,
                "turns": p.turns,
            }
        )

    is_admin = request.session.get("user") is not None

    return templates.TemplateResponse(
        "bluewar/match_detail.html",
        {
            "request": request,
            "match": match,
            "participants": view_parts,
            "is_admin": is_admin,
            "error": None,
        },
    )
