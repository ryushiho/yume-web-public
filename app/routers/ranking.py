# app/routers/ranking.py

from __future__ import annotations

from typing import Dict, List, Optional, Set, TypedDict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_member_or_admin
from app import models


router = APIRouter(
    prefix="/ranking",
    tags=["ranking"],
)

templates = Jinja2Templates(directory="app/templates")


class RankingRow(TypedDict):
    rank: int
    discord_id: str
    name: str
    mode: str
    matches: int
    wins: int
    losses: int
    base_wins: int
    base_losses: int
    total_wins: int
    total_losses: int
    win_rate: float
    gap_plus: int
    gap_minus: int
    net_gap: int


def _resolve_display_name(
    *,
    discord_id: str,
    users_by_discord: Dict[str, models.User],
    fallback_names_by_discord: Dict[str, str],
) -> str:
    u = users_by_discord.get(discord_id)
    if u and u.nickname:
        return u.nickname
    if discord_id in fallback_names_by_discord:
        return fallback_names_by_discord[discord_id]
    return discord_id


@router.get("/", response_class=HTMLResponse)
def ranking_page(
    request: Request,
    db: Session = Depends(get_db),
    _viewer=Depends(get_current_member_or_admin),
    mode: str = "pvp",
    limit: int = 50,
):
    """블루전 랭킹.

    정렬 기준:
    1) 순수 승차(net_gap) DESC
    2) 총 승리(기본 전적 포함) DESC
    3) 총 매치 수 DESC
    """

    mode = (mode or "pvp").strip().lower()
    if mode not in {"pvp", "practice", "all"}:
        mode = "pvp"

    q = db.query(models.BlueWarMatch)
    # "완료"된 매치만 집계 (winner/loser가 있는 경우)
    q = q.filter(models.BlueWarMatch.winner_discord_id.isnot(None))
    q = q.filter(models.BlueWarMatch.loser_discord_id.isnot(None))

    if mode != "all":
        q = q.filter(models.BlueWarMatch.mode == mode)

    matches: List[models.BlueWarMatch] = q.order_by(models.BlueWarMatch.id.desc()).all()

    ids: Set[str] = set()
    match_ids: List[int] = []
    for m in matches:
        match_ids.append(m.id)
        if m.winner_discord_id:
            ids.add(m.winner_discord_id)
        if m.loser_discord_id:
            ids.add(m.loser_discord_id)

    users_by_discord: Dict[str, models.User] = {}
    if ids:
        users = (
            db.query(models.User)
            .filter(models.User.discord_id.in_(list(ids)))
            .all()
        )
        users_by_discord = {u.discord_id: u for u in users}

    fallback_names_by_discord: Dict[str, str] = {}
    if match_ids:
        parts = (
            db.query(models.BlueWarParticipant)
            .filter(models.BlueWarParticipant.match_id.in_(match_ids))
            .all()
        )
        for p in parts:
            if p.discord_id and p.name and p.discord_id not in fallback_names_by_discord:
                fallback_names_by_discord[p.discord_id] = p.name

    # stats keyed by discord_id
    stats: Dict[str, Dict[str, int]] = {}
    for did in ids:
        u = users_by_discord.get(did)
        stats[did] = {
            "wins": 0,
            "losses": 0,
            "gap_plus": 0,
            "gap_minus": 0,
            "base_wins": int(u.base_wins) if u else 0,
            "base_losses": int(u.base_losses) if u else 0,
        }

    for m in matches:
        gap = int(m.win_gap or 0)

        if m.winner_discord_id:
            s = stats.setdefault(
                m.winner_discord_id,
                {"wins": 0, "losses": 0, "gap_plus": 0, "gap_minus": 0, "base_wins": 0, "base_losses": 0},
            )
            s["wins"] += 1
            s["gap_plus"] += gap

        if m.loser_discord_id:
            s = stats.setdefault(
                m.loser_discord_id,
                {"wins": 0, "losses": 0, "gap_plus": 0, "gap_minus": 0, "base_wins": 0, "base_losses": 0},
            )
            s["losses"] += 1
            s["gap_minus"] += gap

    rows: List[RankingRow] = []
    for did, s in stats.items():
        wins = int(s.get("wins", 0))
        losses = int(s.get("losses", 0))
        matches_cnt = wins + losses

        base_wins = int(s.get("base_wins", 0))
        base_losses = int(s.get("base_losses", 0))

        total_wins = wins + base_wins
        total_losses = losses + base_losses
        total_battles = total_wins + total_losses

        gap_plus = int(s.get("gap_plus", 0))
        gap_minus = int(s.get("gap_minus", 0))
        net_gap = gap_plus - gap_minus

        win_rate = (total_wins / total_battles * 100.0) if total_battles > 0 else 0.0

        rows.append(
            {
                "rank": 0,
                "discord_id": did,
                "name": _resolve_display_name(
                    discord_id=did,
                    users_by_discord=users_by_discord,
                    fallback_names_by_discord=fallback_names_by_discord,
                ),
                "mode": mode,
                "matches": matches_cnt,
                "wins": wins,
                "losses": losses,
                "base_wins": base_wins,
                "base_losses": base_losses,
                "total_wins": total_wins,
                "total_losses": total_losses,
                "win_rate": win_rate,
                "gap_plus": gap_plus,
                "gap_minus": gap_minus,
                "net_gap": net_gap,
            }
        )

    # 정렬: net_gap DESC, total_wins DESC, matches DESC, name ASC
    rows.sort(
        key=lambda r: (
            -r["net_gap"],
            -r["total_wins"],
            -r["matches"],
            r["name"],
        )
    )

    # rank 부여 + limit 적용
    limit_i = max(1, min(int(limit), 200))
    ranked: List[RankingRow] = []
    for idx, r in enumerate(rows[:limit_i], start=1):
        r2 = dict(r)
        r2["rank"] = idx
        ranked.append(r2)  # type: ignore[arg-type]

    return templates.TemplateResponse(
        "ranking.html",
        {
            "request": request,
            "rows": ranked,
            "mode": mode,
        },
    )
