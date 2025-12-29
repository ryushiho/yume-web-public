# app/routers/aby.py

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_member_or_admin
from app import models


router = APIRouter(
    prefix="/aby",
    tags=["aby-ui"],
)

templates = Jinja2Templates(directory="app/templates")


def _json_load(s: Optional[str]) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


@router.get("/", response_class=HTMLResponse)
def aby_index(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_member_or_admin),
):
    guilds = db.query(models.AbyGuildState).order_by(models.AbyGuildState.updated_at.desc()).all()
    return templates.TemplateResponse(
        "aby_index.html",
        {
            "request": request,
            "current_user": current_user,
            "guilds": guilds,
        },
    )


@router.get("/guild/{guild_id}", response_class=HTMLResponse)
def aby_guild_detail(
    guild_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_member_or_admin),
):
    gid = str(guild_id).strip()
    g = db.query(models.AbyGuildState).filter(models.AbyGuildState.guild_id == gid).first()
    if g is None:
        raise HTTPException(status_code=404, detail="Guild not found")

    users = (
        db.query(models.AbyUserEconomy)
        .filter(models.AbyUserEconomy.guild_id == gid)
        .order_by(models.AbyUserEconomy.credits.desc(), models.AbyUserEconomy.water.desc())
        .all()
    )

    explores = (
        db.query(models.AbyExploreLog)
        .filter(models.AbyExploreLog.guild_id == gid)
        .order_by(models.AbyExploreLog.created_at.desc())
        .limit(30)
        .all()
    )

    incidents = (
        db.query(models.AbyIncidentLog)
        .filter(models.AbyIncidentLog.guild_id == gid)
        .order_by(models.AbyIncidentLog.created_at.desc())
        .limit(30)
        .all()
    )

    weekly_rows = (
        db.query(models.AbyWeeklySummary)
        .filter(models.AbyWeeklySummary.guild_id == gid)
        .order_by(models.AbyWeeklySummary.week_key.desc())
        .limit(8)
        .all()
    )

    weekly: List[Dict[str, Any]] = []
    for w in weekly_rows:
        weekly.append(
            {
                "week_key": w.week_key,
                "debt_summary": _json_load(w.debt_summary_json) or {},
                "points_ranking": _json_load(w.points_ranking_json) or [],
                "updated_at": w.updated_at,
            }
        )

    return templates.TemplateResponse(
        "aby_guild.html",
        {
            "request": request,
            "current_user": current_user,
            "guild": g,
            "users": users,
            "explores": explores,
            "incidents": incidents,
            "weekly": weekly,
        },
    )
