# app/routers/aby.py

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

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


def _pretty_json(s: Optional[str]) -> str:
    """Human-friendly JSON pretty print for templates."""
    obj = _json_load(s)
    if obj is None:
        return ""
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)
    except Exception:
        # Fallback: show raw string
        return s or ""


def _paginate(page: int, page_size: int, *, max_size: int = 200) -> Tuple[int, int]:
    p = 1 if page is None else max(int(page), 1)
    s = 50 if page_size is None else max(int(page_size), 1)
    s = min(s, max_size)
    return p, s


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


def _sanitize_pagination(page: int, page_size: int, *, max_size: int = 200) -> Tuple[int, int]:
    p = max(int(page or 1), 1)
    ps = max(int(page_size or 50), 1)
    ps = min(ps, max_size)
    return p, ps


@router.get("/guild/{guild_id}/explores", response_class=HTMLResponse)
def aby_explore_list(
    guild_id: str,
    request: Request,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_member_or_admin),
):
    p, ps = _sanitize_pagination(page, page_size)
    gid = str(guild_id)
    g = db.query(models.AbyGuildState).filter(models.AbyGuildState.guild_id == gid).first()
    if not g:
        raise HTTPException(status_code=404, detail="Guild not found")

    q = db.query(models.AbyExploreLog).filter(models.AbyExploreLog.guild_id == gid)
    total = q.count()
    rows = (
        q.order_by(models.AbyExploreLog.created_at.desc())
        .offset((p - 1) * ps)
        .limit(ps)
        .all()
    )

    return templates.TemplateResponse(
        "aby_explores.html",
        {
            "request": request,
            "current_user": current_user,
            "guild": g,
            "rows": rows,
            "page": p,
            "page_size": ps,
            "total": total,
            "pages": max((total + ps - 1) // ps, 1),
        },
    )


@router.get("/explore/{log_id}", response_class=HTMLResponse)
def aby_explore_detail(
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_member_or_admin),
):
    row = db.query(models.AbyExploreLog).filter(models.AbyExploreLog.id == int(log_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Log not found")

    g = db.query(models.AbyGuildState).filter(models.AbyGuildState.guild_id == row.guild_id).first()

    return templates.TemplateResponse(
        "aby_explore_detail.html",
        {
            "request": request,
            "current_user": current_user,
            "guild": g,
            "row": row,
            "details_json": _pretty_json(row.details_json),
            "encounters_json": _pretty_json(row.encounters_json),
            "rolls_json": _pretty_json(row.rolls_json),
            "words_used_json": _pretty_json(row.words_used_json),
        },
    )


@router.get("/guild/{guild_id}/incidents", response_class=HTMLResponse)
def aby_incident_list(
    guild_id: str,
    request: Request,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_member_or_admin),
):
    p, ps = _sanitize_pagination(page, page_size)
    gid = str(guild_id)
    g = db.query(models.AbyGuildState).filter(models.AbyGuildState.guild_id == gid).first()
    if not g:
        raise HTTPException(status_code=404, detail="Guild not found")

    q = db.query(models.AbyIncidentLog).filter(models.AbyIncidentLog.guild_id == gid)
    total = q.count()
    rows = (
        q.order_by(models.AbyIncidentLog.created_at.desc())
        .offset((p - 1) * ps)
        .limit(ps)
        .all()
    )

    return templates.TemplateResponse(
        "aby_incidents.html",
        {
            "request": request,
            "current_user": current_user,
            "guild": g,
            "rows": rows,
            "page": p,
            "page_size": ps,
            "total": total,
            "pages": max((total + ps - 1) // ps, 1),
        },
    )


@router.get("/incident/{log_id}", response_class=HTMLResponse)
def aby_incident_detail(
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_member_or_admin),
):
    row = db.query(models.AbyIncidentLog).filter(models.AbyIncidentLog.id == int(log_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Log not found")

    g = db.query(models.AbyGuildState).filter(models.AbyGuildState.guild_id == row.guild_id).first()

    return templates.TemplateResponse(
        "aby_incident_detail.html",
        {
            "request": request,
            "current_user": current_user,
            "guild": g,
            "row": row,
            "effect_json": _pretty_json(row.effect_json),
        },
    )


@router.get("/guild/{guild_id}/weekly", response_class=HTMLResponse)
def aby_weekly_list(
    guild_id: str,
    request: Request,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_member_or_admin),
):
    p, ps = _sanitize_pagination(page, page_size)
    gid = str(guild_id)
    g = db.query(models.AbyGuildState).filter(models.AbyGuildState.guild_id == gid).first()
    if not g:
        raise HTTPException(status_code=404, detail="Guild not found")

    q = db.query(models.AbyWeeklySummary).filter(models.AbyWeeklySummary.guild_id == gid)
    total = q.count()
    rows = (
        q.order_by(models.AbyWeeklySummary.week_key.desc())
        .offset((p - 1) * ps)
        .limit(ps)
        .all()
    )

    return templates.TemplateResponse(
        "aby_weekly.html",
        {
            "request": request,
            "current_user": current_user,
            "guild": g,
            "rows": rows,
            "page": p,
            "page_size": ps,
            "total": total,
            "pages": max((total + ps - 1) // ps, 1),
        },
    )


@router.get("/weekly/{row_id}", response_class=HTMLResponse)
def aby_weekly_detail(
    row_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_member_or_admin),
):
    row = db.query(models.AbyWeeklySummary).filter(models.AbyWeeklySummary.id == int(row_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Weekly summary not found")

    g = db.query(models.AbyGuildState).filter(models.AbyGuildState.guild_id == row.guild_id).first()

    return templates.TemplateResponse(
        "aby_weekly_detail.html",
        {
            "request": request,
            "current_user": current_user,
            "guild": g,
            "row": row,
            "debt_summary_json": _pretty_json(row.debt_summary_json),
            "points_ranking_json": _pretty_json(row.points_ranking_json),
            "explore_summary_json": _pretty_json(row.explore_summary_json),
            "incident_summary_json": _pretty_json(row.incident_summary_json),
            "report_json": _pretty_json(row.report_json),
        },
    )
