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
from app.utils.time import fmt_kst


BOT_NAME_MAP = {
    "shiho": "시호시호",
    "yume": "유메",
}


def _source_label(v: str) -> str:
    s = (v or "").strip()
    key = s.lower()
    return BOT_NAME_MAP.get(key, s or "-")


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
    key = str(discord_id).strip().lower()
    if key in BOT_NAME_MAP:
        return BOT_NAME_MAP[key]
    u = users_by_discord.get(discord_id)
    if u and u.nickname:
        return u.nickname
    if discord_id in fallback_names_by_discord:
        return fallback_names_by_discord[discord_id]
    return discord_id
def _mode_label(mode: str, *, ai_label: Optional[str] = None) -> str:
    """UI 표시용 모드 라벨.
    - pvp -> PVP
    - practice -> 연습 (AI/난이도가 있으면 괄호로 표시)
    """
    m = (mode or "").strip().lower()
    if m == "pvp":
        return "PVP"
    if m == "practice":
        if ai_label:
            label = ai_label
            if label.strip().lower() == "yume":
                label = "유메"
            return f"연습 ({label})"
        return "연습"
    return mode
def _display_id_number(mode: str, seq: int) -> int:
    """요구사항: PVP는 #1부터, PVBOT(연습)은 #10001부터."""
    m = (mode or "").strip().lower()
    if m == "practice":
        return 10000 + int(seq)
    return int(seq)
@router.get("/matches/", response_class=HTMLResponse)
def list_bluewar_matches(
    request: Request,
    db: Session = Depends(get_db),
    viewer=Depends(get_current_member_or_admin),
    mode: str = Query(default="all", description="all|pvp|practice"),
    status: str = Query(default="all", description="all|finished|aborted|running"),
    source_app: str = Query(default="shiho", description="all|shiho|..."),
    q: str = Query(default="", description="검색어(Discord ID/복기 로그)"),
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
    # mode 필터: all이면 전체, 그 외(pvp/practice)는 해당 모드만
    if (mode or '').strip().lower() != 'all':
        query = query.filter(models.BlueWarMatch.mode == mode)
    status = (status or "all").strip().lower()
    if status in ("finished", "aborted", "running"):
        query = query.filter(models.BlueWarMatch.status == status)
    source_app = (source_app or "shiho").strip().lower()
    if source_app != "all":
        query = query.filter(models.BlueWarMatch.source_app == source_app)
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
    # 표시용: 모드별 시퀀스 번호 (PVP는 1부터, 연습은 1부터 -> 화면에서는 10001부터)
    match_ids_for_seq = [int(m.id) for m, _pc in rows]
    seq_by_match_id: Dict[int, int] = {}
    if match_ids_for_seq:
        seq_subq = (
            db.query(
                models.BlueWarMatch.id.label("id"),
                models.BlueWarMatch.mode.label("mode"),
                func.row_number()
                .over(
                    partition_by=models.BlueWarMatch.mode,
                    order_by=models.BlueWarMatch.id.asc(),
                )
                .label("seq"),
            )
        ).subquery()
        rows_seq = (
            db.query(seq_subq.c.id, seq_subq.c.seq)
            .filter(seq_subq.c.id.in_(match_ids_for_seq))
            .all()
        )
        seq_by_match_id = {int(r.id): int(r.seq) for r in rows_seq}
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
        seq = seq_by_match_id.get(int(match.id), int(match.id))
        disp_num = _display_id_number(match.mode, seq)
        matches.append(
            {
                "id": match.id,
                "display_id": f"#{disp_num}",
                "mode": match.mode,
                "mode_label": _mode_label(match.mode),
                "source_app": getattr(match, "source_app", None) or "-",
                "source_label": _source_label(getattr(match, "source_app", None) or "-"),
                "status": match.status,
                "status_label": _status_label(match.status),
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
                "started_at": fmt_kst(match.started_at, "%Y-%m-%d %H:%M:%S"),
                "finished_at": fmt_kst(match.finished_at, "%Y-%m-%d %H:%M:%S"),
                "created_at": fmt_kst(match.created_at, "%Y-%m-%d %H:%M:%S"),
                "pcount": int(pcount),
            }
        )
    return templates.TemplateResponse(
        "bluewar/matches.html",
        {
            "request": request,
            "viewer": viewer,
            "mode": mode,
            "matches": matches,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "status": status,
            "source_app": source_app,
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
    # 표시용: 모드별 시퀀스 번호 및 ID 표기
    seq_subq = (
        db.query(
            models.BlueWarMatch.id.label("id"),
            models.BlueWarMatch.mode.label("mode"),
            func.row_number()
            .over(
                partition_by=models.BlueWarMatch.mode,
                order_by=models.BlueWarMatch.id.asc(),
            )
            .label("seq"),
        )
    ).subquery()
    seq = (
        db.query(seq_subq.c.seq)
        .filter(seq_subq.c.id == int(match.id))
        .scalar()
    )
    seq_i = int(seq) if seq is not None else int(match.id)
    display_id = f"#{_display_id_number(match.mode, seq_i)}"
    # 연습 모드라면 AI/난이도 표시를 위해 참가자에서 ai_name 추정
    ai_label: Optional[str] = None
    if (match.mode or "").strip().lower() == "practice":
        for p in participants:
            if p.ai_name:
                ai_label = p.ai_name
                break
            if p.name:
                ai_label = p.name
                break
    mode_label = _mode_label(match.mode, ai_label=ai_label)
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
            key = str(p.name).strip().lower()
            if key in BOT_NAME_MAP:
                return BOT_NAME_MAP[key]
            return p.name
        if p.ai_name:
            key = str(p.ai_name).strip().lower()
            if key in BOT_NAME_MAP:
                return BOT_NAME_MAP[key]
            return p.ai_name
        if p.discord_id:
            key = str(p.discord_id).strip().lower()
            if key in BOT_NAME_MAP:
                return BOT_NAME_MAP[key]
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

    # ensure winner/loser appear in participants view (legacy records may not store bot as participant)
    existing_names = {vp.get("name") for vp in view_parts if vp.get("name")}
    existing_ids = {str(p.discord_id).strip().lower() for p in participants if p.discord_id}
    def _append_virtual(discord_id: Optional[str], is_winner: bool):
        if not discord_id:
            return
        key = str(discord_id).strip().lower()
        disp = BOT_NAME_MAP.get(key) or str(discord_id)
        # avoid duplicates
        if disp in existing_names:
            return
        if key in existing_ids:
            return
        view_parts.append(
            {
                "side": "BOT" if key in BOT_NAME_MAP else "-",
                "name": disp,
                "is_winner": bool(is_winner),
                "score": None,
                "turns": None,
            }
        )

    _append_virtual(match.winner_discord_id, True)
    _append_virtual(match.loser_discord_id, False)

    is_admin = request.session.get("user") is not None
    return templates.TemplateResponse(
        "bluewar/match_detail.html",
        {
            "request": request,
            "match": match,
            "participants": view_parts,
            "is_admin": is_admin,
            "display_id": display_id,
            "mode_label": mode_label,
            "source_label": _source_label(getattr(match, "source_app", None) or "-"),
            "status_label": _status_label(match.status),
            "started_at": fmt_kst(match.started_at, "%Y-%m-%d %H:%M:%S"),
            "finished_at": fmt_kst(match.finished_at, "%Y-%m-%d %H:%M:%S"),
            "error": None,
        },
    )