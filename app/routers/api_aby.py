# app/routers/api_aby.py

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from config import settings
from app.database import get_db
from app import models


router = APIRouter(
    prefix="/api/v1/aby",
    tags=["aby-api"],
)


def _expected_token() -> str:
    tok = (getattr(settings, "ABY_SYNC_TOKEN", "") or "").strip()
    return tok


def _verify_bearer_token(authorization: Optional[str] = Header(None, alias="Authorization")) -> None:
    expected = _expected_token()
    if not expected:
        # Never allow an open write endpoint.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Aby sync token is not configured",
        )

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
        )

    got = (parts[1] or "").strip()
    if not got or got != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


def _dt_from_any(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            # bot sends unix seconds
            return datetime.fromtimestamp(float(v), tz=timezone.utc).replace(tzinfo=None)
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            # accept "2025-12-30T12:34:56" or "...Z"
            s = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            # store naive UTC for sqlite consistency
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
    except Exception:
        return None
    return None


def _sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _make_source_id(kind: str, guild_id: str, obj: Dict[str, Any]) -> str:
    raw = json.dumps({"k": kind, "g": guild_id, "o": obj}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha1_hex(raw)[:40]


def _process_one_guild(db: Session, gd: Dict[str, Any]) -> Dict[str, int]:
    counts = {"guild_upsert": 0, "users_upsert": 0, "explore_added": 0, "incident_added": 0, "weekly_upsert": 0}

    gid = str(gd.get("guild_id") or "").strip()
    if not gid:
        return counts

    gname = (gd.get("guild_name") or gd.get("name") or "")
    gname = str(gname)[:120] if gname is not None else None

    # ---------- guild state ----------
    debt = int(gd.get("debt") or (gd.get("guild_state") or {}).get("debt") or 0)
    rate = gd.get("interest_rate")
    if rate is None:
        rate = (gd.get("guild_state") or {}).get("interest_rate")
    try:
        rate_f = float(rate) if rate is not None else 0.0
    except Exception:
        rate_f = 0.0

    last_ymd = gd.get("last_interest_ymd")
    if last_ymd is None:
        last_ymd = (gd.get("guild_state") or {}).get("last_interest_ymd")
    last_ymd = str(last_ymd)[:20] if last_ymd else None

    row = db.query(models.AbyGuildState).filter(models.AbyGuildState.guild_id == gid).first()
    if row is None:
        row = models.AbyGuildState(guild_id=gid)
        db.add(row)
        counts["guild_upsert"] += 1
    # Always update (bot is source of truth)
    row.guild_name = gname
    row.debt = debt
    row.interest_rate = rate_f
    row.last_interest_ymd = last_ymd

    # ---------- users ----------
    users = gd.get("users") or []
    if isinstance(users, list):
        for u in users:
            if not isinstance(u, dict):
                continue
            uid = str(u.get("user_id") or "").strip()
            if not uid:
                continue
            nick = u.get("nickname")
            nick = str(nick)[:120] if nick else None

            credits = int(u.get("credits") or 0)
            water = int(u.get("water") or 0)
            last_explore = u.get("last_explore_ymd")
            last_explore = str(last_explore)[:20] if last_explore else None

            ur = (
                db.query(models.AbyUserEconomy)
                .filter(models.AbyUserEconomy.guild_id == gid, models.AbyUserEconomy.user_id == uid)
                .first()
            )
            if ur is None:
                ur = models.AbyUserEconomy(guild_id=gid, user_id=uid)
                db.add(ur)
                counts["users_upsert"] += 1

            ur.nickname = nick
            ur.credits = credits
            ur.water = water
            ur.last_explore_ymd = last_explore

    # ---------- explore logs ----------
    explore_logs = gd.get("explore_logs") or []
    if isinstance(explore_logs, list):
        for item in explore_logs:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("source_id") or item.get("id") or "").strip()
            if not sid:
                sid = _make_source_id("explore", gid, item)

            exists = (
                db.query(models.AbyExploreLog)
                .filter(models.AbyExploreLog.guild_id == gid, models.AbyExploreLog.source_id == sid)
                .first()
            )
            if exists is not None:
                continue

            created_at = _dt_from_any(item.get("created_at")) or _dt_from_any(item.get("ts")) or datetime.utcnow()

            success = item.get("success")
            s_i = 1 if bool(success) else 0

            rec = models.AbyExploreLog(
                guild_id=gid,
                source_id=sid,
                user_id=str(item.get("user_id") or "").strip() or None,
                nickname=str(item.get("nickname") or "")[:120] if item.get("nickname") else None,
                date_ymd=str(item.get("date_ymd") or "")[:20] if item.get("date_ymd") else None,
                weather=str(item.get("weather") or "")[:20] if item.get("weather") else None,
                success=s_i,
                delta_credits=int(item.get("delta_credits") or 0),
                delta_water=int(item.get("delta_water") or 0),
                summary=str(item.get("summary") or "") if item.get("summary") else None,
                raw_json=json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                created_at=created_at,
            )
            db.add(rec)
            counts["explore_added"] += 1

    # ---------- incident logs ----------
    inc_logs = gd.get("incident_logs") or gd.get("incidents") or []
    if isinstance(inc_logs, list):
        for item in inc_logs:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("source_id") or item.get("id") or "").strip()
            if not sid:
                sid = _make_source_id("incident", gid, item)

            exists = (
                db.query(models.AbyIncidentLog)
                .filter(models.AbyIncidentLog.guild_id == gid, models.AbyIncidentLog.source_id == sid)
                .first()
            )
            if exists is not None:
                continue

            created_at = _dt_from_any(item.get("created_at")) or _dt_from_any(item.get("ts")) or datetime.utcnow()

            rec = models.AbyIncidentLog(
                guild_id=gid,
                source_id=sid,
                kind=str(item.get("kind") or "incident")[:40],
                title=str(item.get("title") or "")[:120],
                description=str(item.get("description") or ""),
                delta_debt=int(item.get("delta_debt") or 0),
                raw_json=json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                created_at=created_at,
            )
            db.add(rec)
            counts["incident_added"] += 1

    # ---------- weekly summary ----------
    weekly = gd.get("weekly")
    if isinstance(weekly, dict) and weekly.get("week_key"):
        wk = str(weekly.get("week_key") or "")[:20]
        wr = (
            db.query(models.AbyWeeklySummary)
            .filter(models.AbyWeeklySummary.guild_id == gid, models.AbyWeeklySummary.week_key == wk)
            .first()
        )
        if wr is None:
            wr = models.AbyWeeklySummary(guild_id=gid, week_key=wk)
            db.add(wr)
            counts["weekly_upsert"] += 1
        wr.debt_summary_json = json.dumps(weekly.get("debt_summary"), ensure_ascii=False) if weekly.get("debt_summary") is not None else None
        wr.points_ranking_json = json.dumps(weekly.get("points_ranking"), ensure_ascii=False) if weekly.get("points_ranking") is not None else None

    return counts


@router.post("/sync")
async def sync_aby(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(_verify_bearer_token),
) -> Dict[str, Any]:
    """Bot -> Web: Abydos state sync.

    Accepts either:
    - { "guild_id": ..., "users": [...], ... } (single guild)
    - { "aby": { "guilds": [ ... ] } }        (envelope with many guilds)
    - { "guilds": [ ... ] }                  (flat many guilds)
    """
    body = await request.json()

    # Simple health-check for quick verification via curl.
    # Example:
    #   curl -X POST /api/v1/aby/sync -d '{"ping": true}'
    if isinstance(body, dict) and bool(body.get("ping")):
        return {"ok": True, "pong": True}

    data = body.get("aby") if isinstance(body, dict) else None
    if data is None:
        data = body

    total = {"guild_upsert": 0, "users_upsert": 0, "explore_added": 0, "incident_added": 0, "weekly_upsert": 0}

    guilds = None
    if isinstance(data, dict):
        guilds = data.get("guilds")
    if isinstance(guilds, list):
        for gd in guilds:
            if not isinstance(gd, dict):
                continue
            c = _process_one_guild(db, gd)
            for k in total:
                total[k] += int(c.get(k) or 0)
    elif isinstance(data, dict) and data.get("guild_id"):
        c = _process_one_guild(db, data)
        for k in total:
            total[k] += int(c.get(k) or 0)
    else:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid payload")

    db.commit()
    return {"ok": True, "counts": total}
