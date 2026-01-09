# app/routers/admin_analysis.py

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_admin_user
from app.utils.wordlists import WORDLIST_LABELS
from app.utils import dictpacks
from app.config import settings
from app.bluewar.analysis_engine import prepare_input, explain_syllable
from app.bluewar.analysis_jobs import enqueue_rebuild_job
from app import models


router = APIRouter(prefix="/admin/analysis", tags=["admin-analysis"])
templates = Jinja2Templates(directory="app/templates")


def _pack_versions() -> list[str]:
    packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
    return dictpacks.list_versions(packs_dir)


@router.get("/")
def analysis_index(
    request: Request,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    list_name = (request.query_params.get("list") or "blue_archive_words").strip()
    pack = (request.query_params.get("pack") or "").strip() or None

    # state message
    ok = request.query_params.get("ok")
    error = request.query_params.get("error")

    # what would be analyzed right now?
    meta_input, _ = prepare_input(db, list_name=list_name, pack_version=pack)

    # existing stored meta
    stored = (
        db.query(models.BlueWarAnalysisMeta)
        .filter(models.BlueWarAnalysisMeta.analysis_key == meta_input.analysis_key)
        .order_by(models.BlueWarAnalysisMeta.created_at.desc())
        .first()
    )

    # latest job for this key (may be None)
    job = (
        db.query(models.BlueWarAnalysisJob)
        .filter(models.BlueWarAnalysisJob.analysis_key == meta_input.analysis_key)
        .order_by(models.BlueWarAnalysisJob.id.desc())
        .first()
    )

    packs = _pack_versions()
    packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
    packs_default = (settings.WORDLIST_PACKS_DEFAULT or "").strip()
    default_pack = dictpacks.get_default_version(env_override=packs_dir, env_default=packs_default)

    return templates.TemplateResponse(
        "admin_analysis.html",
        {
            "request": request,
            "list_labels": WORDLIST_LABELS,
            "list_name": list_name,
            "pack": pack,
            "packs": packs,
            "default_pack": default_pack,
            "meta_input": meta_input,
            "stored": stored,
            "job": job,
            "ok": ok,
            "error": error,
        },
    )


@router.post("/rebuild")
def analysis_rebuild(
    request: Request,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    # We keep this endpoint simple and rely on query params.
    list_name = (request.query_params.get("list") or "blue_archive_words").strip()
    pack = (request.query_params.get("pack") or "").strip() or None

    try:
        enqueue_rebuild_job(db, list_name=list_name, pack_version=pack)
    except Exception:
        return RedirectResponse(
            url=f"/admin/analysis/?list={list_name}&pack={pack or ''}&error=queue",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/admin/analysis/?list={list_name}&pack={pack or ''}&ok=queued",
        status_code=303,
    )


@router.get("/syllables")
def analysis_syllables(
    request: Request,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    list_name = (request.query_params.get("list") or "blue_archive_words").strip()
    pack = (request.query_params.get("pack") or "").strip() or None

    meta_input, _ = prepare_input(db, list_name=list_name, pack_version=pack)

    q = (request.query_params.get("q") or "").strip()
    t = (request.query_params.get("t") or "").strip().upper()  # WIN/LOSE/DRAW
    limit = int((request.query_params.get("limit") or "200").strip() or 200)
    if limit < 10:
        limit = 10
    if limit > 2000:
        limit = 2000

    qs = db.query(models.BlueWarSyllableStat).filter(models.BlueWarSyllableStat.analysis_key == meta_input.analysis_key)
    if q:
        qs = qs.filter(models.BlueWarSyllableStat.syllable.contains(q))
    if t in ("WIN", "LOSE", "DRAW"):
        qs = qs.filter(models.BlueWarSyllableStat.node_type == t)

    rows = qs.order_by(models.BlueWarSyllableStat.syllable.asc()).limit(limit).all()

    return templates.TemplateResponse(
        "admin_analysis_syllables.html",
        {
            "request": request,
            "list_labels": WORDLIST_LABELS,
            "list_name": list_name,
            "pack": pack,
            "analysis_key": meta_input.analysis_key,
            "q": q,
            "t": t,
            "limit": limit,
            "rows": rows,
        },
    )


@router.get("/syllable/{syllable}")
def analysis_syllable_detail(
    syllable: str,
    request: Request,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    list_name = (request.query_params.get("list") or "blue_archive_words").strip()
    pack = (request.query_params.get("pack") or "").strip() or None
    max_steps = int((request.query_params.get("max_steps") or "10").strip() or "10")
    max_steps = max(3, min(30, max_steps))

    meta_input, words = prepare_input(db, list_name=list_name, pack_version=pack)
    info = explain_syllable(analysis_key=meta_input.analysis_key, words=words, syllable=syllable, max_steps=max_steps)

    return templates.TemplateResponse(
        "admin_analysis_syllable_detail.html",
        {
            "request": request,
            "list_labels": WORDLIST_LABELS,
            "list_name": list_name,
            "pack": pack,
            "analysis_key": meta_input.analysis_key,
            "syllable": syllable,
            "max_steps": max_steps,
            "info": info,
        },
    )

@router.get("/words")
def analysis_words(
    request: Request,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    list_name = (request.query_params.get("list") or "blue_archive_words").strip()
    pack = (request.query_params.get("pack") or "").strip() or None
    meta_input, _ = prepare_input(db, list_name=list_name, pack_version=pack)

    q = (request.query_params.get("q") or "").strip()
    start = (request.query_params.get("s") or "").strip()
    end = (request.query_params.get("e") or "").strip()
    t = (request.query_params.get("t") or "").strip().upper()

    limit = int((request.query_params.get("limit") or "200").strip() or 200)
    if limit < 10:
        limit = 10
    if limit > 2000:
        limit = 2000

    qs = db.query(models.BlueWarWordStat).filter(models.BlueWarWordStat.analysis_key == meta_input.analysis_key)
    if q:
        qs = qs.filter(models.BlueWarWordStat.word.contains(q))
    if start:
        qs = qs.filter(models.BlueWarWordStat.start_syllable == start)
    if end:
        qs = qs.filter(models.BlueWarWordStat.end_syllable == end)
    if t in ("WIN", "LOSE", "DRAW"):
        qs = qs.filter(models.BlueWarWordStat.node_type == t)

    rows = qs.order_by(models.BlueWarWordStat.word.asc()).limit(limit).all()

    return templates.TemplateResponse(
        "admin_analysis_words.html",
        {
            "request": request,
            "list_labels": WORDLIST_LABELS,
            "list_name": list_name,
            "pack": pack,
            "analysis_key": meta_input.analysis_key,
            "q": q,
            "s": start,
            "e": end,
            "t": t,
            "limit": limit,
            "rows": rows,
        },
    )
