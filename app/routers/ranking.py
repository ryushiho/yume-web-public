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


# 랭킹에서 제외할 내부 계정(봇/테스트 등)
EXCLUDED_DISCORD_IDS = {"yume"}


def _is_excluded_discord_id(discord_id: Optional[str]) -> bool:
    if not discord_id:
        return False
    return (discord_id or "").strip().lower() in EXCLUDED_DISCORD_IDS


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
    net: int


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
    limit: int = 50,
    source_app: str = "shiho",
):
    """블루전 랭킹.

    정렬 기준:
    1) 승차(총 승 - 총 패) DESC
    2) 총 승리(기본 전적 포함) DESC
    3) 총 전적(기본 전적 포함) DESC

    주의:
    - 화면에서는 PVP만 제공한다.
    - 0전(총 전적이 0) 유저는 항상 맨 아래로 보낸다.
    """

    mode = "pvp"

    q = db.query(models.BlueWarMatch)

    source_app = (source_app or "shiho").strip().lower()
    if source_app != "all":
        q = q.filter(models.BlueWarMatch.source_app == source_app)

    # "완료"된 매치만 집계 (winner/loser가 있는 경우)
    q = q.filter(models.BlueWarMatch.winner_discord_id.isnot(None))
    q = q.filter(models.BlueWarMatch.loser_discord_id.isnot(None))

    # ✅ PVP만 집계
    q = q.filter(models.BlueWarMatch.mode == mode)

    matches_all: List[models.BlueWarMatch] = q.order_by(models.BlueWarMatch.id.desc()).all()

    # 방어: 혹시라도 PVP로 잘못 기록된 내부 계정 매치가 섞여 있으면 랭킹에서 통째로 제외한다.
    matches: List[models.BlueWarMatch] = []
    for m in matches_all:
        if _is_excluded_discord_id(m.winner_discord_id) or _is_excluded_discord_id(m.loser_discord_id):
            continue
        matches.append(m)

    ids_from_matches: Set[str] = set()
    match_ids: List[int] = []
    for m in matches:
        match_ids.append(m.id)
        if m.winner_discord_id:
            ids_from_matches.add(m.winner_discord_id)
        if m.loser_discord_id:
            ids_from_matches.add(m.loser_discord_id)

    # ✅ 랭킹은 "유저 테이블"도 같이 집계해야 한다.
    # - blue_records.json(기본 전적)만 있어도 랭킹이 떠야 함
    users: List[models.User] = [u for u in db.query(models.User).all() if not _is_excluded_discord_id(u.discord_id)]
    users_by_discord: Dict[str, models.User] = {u.discord_id: u for u in users}
    ids_from_users: Set[str] = set(users_by_discord.keys())

    # 최종 집계 대상: (유저 테이블 + 매치에서 등장한 디스코드 ID)
    ids: Set[str] = set(ids_from_users) | set(ids_from_matches)

    # 최종 방어: 제외 대상은 완전히 제거
    ids = {did for did in ids if not _is_excluded_discord_id(did)}

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
            "base_wins": int(u.base_wins) if u else 0,
            "base_losses": int(u.base_losses) if u else 0,
        }

    for m in matches:
        if m.winner_discord_id:
            s = stats.setdefault(
                m.winner_discord_id,
                {"wins": 0, "losses": 0, "base_wins": 0, "base_losses": 0},
            )
            s["wins"] += 1

        if m.loser_discord_id:
            s = stats.setdefault(
                m.loser_discord_id,
                {"wins": 0, "losses": 0, "base_wins": 0, "base_losses": 0},
            )
            s["losses"] += 1

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

        net = total_wins - total_losses

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
                "net": net,
            }
        )

    # ✅ 정렬:
    # - 0전(총 전적 0) 유저는 무조건 맨 아래
    # - 그 외에는 승차(net) DESC → 총 승 DESC → 총 매치 DESC → 이름
    rows.sort(
        key=lambda r: (
            1 if (r["total_wins"] + r["total_losses"]) == 0 else 0,
            -r["net"],
            -r["total_wins"],
            -(r["total_wins"] + r["total_losses"]),
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
            "limit": limit,
            "source_app": source_app,
        },
    )
