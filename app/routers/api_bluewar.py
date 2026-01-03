# app/routers/api_bluewar.py

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from config import settings
from app.database import get_db
from app import models


# -----------------------------------------------------------------------------
# NOTE
# - 기존(legacy) 봇/문서가 `/api/bluewar/matches` 로 전송하는 경우가 있어서
#   `/bluewar/matches` + `/api/bluewar/matches` 를 둘 다 지원한다.
# - Payload도 과거 형태(starter/winner/loser 등)를 최대한 흡수한다.
# -----------------------------------------------------------------------------


router = APIRouter(prefix="/bluewar", tags=["bluewar_api"])
api_router = APIRouter(prefix="/api/bluewar", tags=["bluewar_api"])


# ============================
#   인증 (봇 → 웹)
# ============================


def get_expected_api_token() -> Optional[str]:
    """config.settings에서 API 토큰 값을 가져온다.

    - API_TOKEN (권장)
    - 없으면 토큰 검증을 하지 않는다(=개발용 오픈 상태)
    """

    return getattr(settings, "API_TOKEN", None)


async def verify_api_token(x_api_token: Optional[str] = Header(None, alias="X-API-Token")) -> None:
    expected = get_expected_api_token()
    if expected is None:
        # 설정 안 돼 있으면 검증 생략 (개발용)
        return

    if not x_api_token or x_api_token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")


# ============================
#   유틸
# ============================


def _parse_dt(v: Any) -> Optional[datetime]:
    """datetime 파서(봇/툴체인 변형에 최대한 관대하게)."""
    if v is None:
        return None

    if isinstance(v, datetime):
        # DB는 naive로 저장(운영에서 TZ 엄격성을 요구하지 않음)
        return v.replace(tzinfo=None) if v.tzinfo else v

    if isinstance(v, (int, float)):
        # unix seconds
        try:
            return datetime.fromtimestamp(float(v), tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            return None

    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None

        # "1700000000" 같은 문자열
        if s.isdigit():
            try:
                return datetime.fromtimestamp(float(s), tz=timezone.utc).replace(tzinfo=None)
            except Exception:
                pass

        # ISO string
        # - "Z" 를 fromisoformat이 이해할 수 있게 변환
        s2 = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s2)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except Exception:
            return None

    return None


def _norm_mode(mode: str) -> str:
    m = (mode or "").strip().lower()
    if m in {"pv", "pve", "ai", "practice"}:
        return "practice"
    if m in {"pvp", "versus", "vs"}:
        return "pvp"
    return m or "unknown"


def _norm_status(status_: str) -> str:
    s = (status_ or "").strip().lower()
    return s or "unknown"


def _norm_source_app(source_app: Optional[str]) -> str:
    s = (source_app or "").strip().lower() or "shiho"
    s = re.sub(r"[^a-z0-9_-]", "", s)[:32] or "shiho"
    return s


def _coerce_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except Exception:
        return None


def _normalize_payload(body: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """여러 형태의 payload를 DB 저장용 형태로 정규화."""

    mode = _norm_mode(str(body.get("mode") or body.get("game_mode") or ""))
    status_ = _norm_status(str(body.get("status") or body.get("state") or ""))
    source_app = _norm_source_app(body.get("source_app") or body.get("source") or body.get("app"))

    starter = (body.get("starter_discord_id") or body.get("starter") or body.get("starter_id") or "")
    starter = str(starter).strip()

    winner = body.get("winner_discord_id") or body.get("winner") or body.get("winner_id")
    loser = body.get("loser_discord_id") or body.get("loser") or body.get("loser_id")
    winner = str(winner).strip() if winner is not None else None
    loser = str(loser).strip() if loser is not None else None

    win_gap = _coerce_int(body.get("win_gap") or body.get("gap"))
    total_rounds = _coerce_int(body.get("total_rounds") or body.get("rounds") or body.get("turns"))

    started_at = _parse_dt(body.get("started_at") or body.get("start_at") or body.get("started"))
    finished_at = _parse_dt(body.get("finished_at") or body.get("end_at") or body.get("finished"))
    now = datetime.utcnow()
    if started_at is None and finished_at is not None:
        started_at = finished_at
    if finished_at is None and started_at is not None:
        finished_at = started_at
    if started_at is None:
        started_at = now
    if finished_at is None:
        finished_at = now

    note = body.get("note")
    if note is not None:
        note = str(note)

    review_log = body.get("review_log") or body.get("review") or body.get("word_history")
    if review_log is not None:
        review_log = str(review_log)

    parts_raw = body.get("participants")
    participants: List[Dict[str, Any]] = []
    if isinstance(parts_raw, list):
        for i, p in enumerate(parts_raw, start=1):
            if not isinstance(p, dict):
                continue
            side = _coerce_int(p.get("side"))
            if side is None:
                side = i
            participants.append(
                {
                    "discord_id": (str(p.get("discord_id")).strip() if p.get("discord_id") else None),
                    "name": (str(p.get("name")).strip() if p.get("name") else None),
                    "ai_name": (str(p.get("ai_name")).strip() if p.get("ai_name") else None),
                    "side": side,
                    "is_winner": bool(p.get("is_winner")) if ("is_winner" in p) else False,
                    "score": _coerce_int(p.get("score")),
                    "turns": _coerce_int(p.get("turns")),
                }
            )

    match_fields = {
        "mode": mode,
        "status": status_,
        "source_app": source_app,
        "starter_discord_id": starter,
        "winner_discord_id": winner,
        "loser_discord_id": loser,
        "win_gap": win_gap,
        "total_rounds": total_rounds,
        "started_at": started_at,
        "finished_at": finished_at,
        "note": note,
        "review_log": review_log,
    }
    return match_fields, participants


# ============================
#   엔드포인트
# ============================


async def create_match(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """디스코드 봇이 한 판 끝났을 때 호출하는 엔드포인트.

    저장:
    - BlueWarMatch 1줄
    - BlueWarParticipant N줄
    - 필요 시 users 테이블 upsert(discord_id 기준)
    """

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Invalid payload")

    match_fields, participants = _normalize_payload(payload)

    if not match_fields["starter_discord_id"]:
        raise HTTPException(status_code=422, detail="starter_discord_id is required")

    match = models.BlueWarMatch(**match_fields)
    db.add(match)
    db.flush()  # match.id 확보

    for p in participants:
        user_obj = None
        if p.get("discord_id"):
            user_obj = db.query(models.User).filter(models.User.discord_id == p["discord_id"]).first()
            if user_obj is None:
                user_obj = models.User(
                    discord_id=p["discord_id"],
                    nickname=p.get("name"),
                    note=None,
                    base_wins=0,
                    base_losses=0,
                )
                db.add(user_obj)
                db.flush()
            else:
                if (not user_obj.nickname) and p.get("name"):
                    user_obj.nickname = p["name"]

        db.add(
            models.BlueWarParticipant(
                match=match,
                user=user_obj,
                discord_id=p.get("discord_id"),
                name=p.get("name"),
                ai_name=p.get("ai_name"),
                side=int(p.get("side") or 1),
                is_winner=bool(p.get("is_winner")),
                score=p.get("score"),
                turns=p.get("turns"),
            )
        )

    db.commit()
    db.refresh(match)
    return {"ok": True, "match_id": match.id}


# Register the same endpoint on both prefixes.
for _r in (router, api_router):
    _r.add_api_route(
        "/matches",
        create_match,
        methods=["POST"],
        dependencies=[Depends(verify_api_token)],
    )
