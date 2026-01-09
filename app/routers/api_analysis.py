# app/routers/api_analysis.py

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.dependencies import get_db
from app.bluewar.analysis_engine import NodeType, prepare_input, explain_syllable, explain_word
from app.bluewar.analysis_jobs import enqueue_rebuild_job, JOB_PENDING, JOB_RUNNING
from app.bluewar.suggestion_export import build_suggestion_text, fetch_neutral_words
from app.routers.api_wordlists import _require_wordlist_token


router = APIRouter(prefix="/api/bluewar/analysis", tags=["bluewar-analysis-api"])


def _parse_json_list(text: Optional[str]) -> list:
    if not text:
        return []
    s = str(text).strip()
    if not s:
        return []
    try:
        obj = json.loads(s)
        if isinstance(obj, list):
            return obj
    except Exception:
        return []
    return []


def _word_counts(db: Session, analysis_key: str) -> Dict[str, int]:
    rows = (
        db.query(models.BlueWarWordStat.node_type, func.count(models.BlueWarWordStat.id))
        .filter(models.BlueWarWordStat.analysis_key == analysis_key)
        .group_by(models.BlueWarWordStat.node_type)
        .all()
    )
    out: Dict[str, int] = {"WIN": 0, "LOSE": 0, "DRAW": 0}
    for nt, cnt in rows:
        k = str(nt or "").upper()
        if k in out:
            out[k] = int(cnt or 0)
    return out


def _syllable_counts(db: Session, analysis_key: str) -> Dict[str, int]:
    rows = (
        db.query(models.BlueWarSyllableStat.node_type, func.count(models.BlueWarSyllableStat.id))
        .filter(models.BlueWarSyllableStat.analysis_key == analysis_key)
        .group_by(models.BlueWarSyllableStat.node_type)
        .all()
    )
    out: Dict[str, int] = {"WIN": 0, "LOSE": 0, "DRAW": 0}
    for nt, cnt in rows:
        k = str(nt or "").upper()
        if k in out:
            out[k] = int(cnt or 0)
    return out


@router.get("/suggestion.txt", response_class=PlainTextResponse)
def analysis_suggestion_txt(
    request: Request,
    list: str = "blue_archive_words",
    pack: Optional[str] = None,
    fmt: str = "plain",
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_wordlist_token),
):
    """Return suggestion.txt generated from latest stored analysis.

    - Protected by the same wordlist token header as /api/bluewar/wordlists/*
    - If analysis result is missing, respond with 404 (to avoid leaking).

    Query params:
      - list: wordlist name (default: blue_archive_words)
      - pack: pack version (optional; if omitted, server default pack is used)
      - fmt: plain|grouped (default: plain)
    """

    list_name = (list or "blue_archive_words").strip()
    pack_version = (pack or "").strip() or None
    fmt = (fmt or "plain").strip().lower()

    meta_input, _ = prepare_input(db, list_name=list_name, pack_version=pack_version)

    stored = (
        db.query(models.BlueWarAnalysisMeta)
        .filter(models.BlueWarAnalysisMeta.analysis_key == meta_input.analysis_key)
        .order_by(models.BlueWarAnalysisMeta.created_at.desc())
        .first()
    )
    if not stored:
        raise HTTPException(status_code=404, detail="not found")

    words = fetch_neutral_words(db, meta_input.analysis_key)
    txt = build_suggestion_text(words, fmt=fmt)  # type: ignore[arg-type]

    return PlainTextResponse(content=txt, media_type="text/plain; charset=utf-8")


@router.get("/latest")
def analysis_latest(
    request: Request,
    list: str = "blue_archive_words",
    pack: Optional[str] = None,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_wordlist_token),
) -> Dict[str, Any]:
    """Return latest analysis status/meta for a (list, pack).

    This endpoint is designed for bots to cheaply check:
    - whether analysis results exist
    - whether a job is currently running
    """

    list_name = (list or "blue_archive_words").strip()
    pack_version = (pack or "").strip() or None

    meta_input, _ = prepare_input(db, list_name=list_name, pack_version=pack_version)

    job = (
        db.query(models.BlueWarAnalysisJob)
        .filter(models.BlueWarAnalysisJob.analysis_key == meta_input.analysis_key)
        .order_by(models.BlueWarAnalysisJob.id.desc())
        .first()
    )

    meta = (
        db.query(models.BlueWarAnalysisMeta)
        .filter(models.BlueWarAnalysisMeta.analysis_key == meta_input.analysis_key)
        .order_by(models.BlueWarAnalysisMeta.created_at.desc())
        .first()
    )

    state = "MISSING"
    if job and job.status in (JOB_PENDING, JOB_RUNNING):
        state = str(job.status)
    elif meta:
        state = "DONE"

    out: Dict[str, Any] = {
        "analysis_key": meta_input.analysis_key,
        "list_name": meta_input.list_name,
        "pack_version": meta_input.pack_version,
        "state": state,
        "input": {
            "words_sha256": meta_input.words_sha256,
            "word_count": int(meta_input.word_count),
            "dooum_sha256": meta_input.dooum_sha256,
            "algo_version": meta_input.algo_version,
        },
    }

    if meta:
        out["meta"] = {
            "created_at": meta.created_at.isoformat() if meta.created_at else None,
            "words_sha256": meta.words_sha256,
            "word_count": int(meta.word_count or 0),
            "dooum_sha256": meta.dooum_sha256,
            "algo_version": meta.algo_version,
            "word_counts": _word_counts(db, meta_input.analysis_key),
            "syllable_counts": _syllable_counts(db, meta_input.analysis_key),
        }

    if job:
        out["job"] = {
            "id": int(job.id),
            "status": job.status,
            "progress_current": int(job.progress_current or 0),
            "progress_total": int(job.progress_total or 0),
            "message": (job.message or "")[:500],
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }

    return out


@router.post("/ensure")
def analysis_ensure(
    request: Request,
    list: str = "blue_archive_words",
    pack: Optional[str] = None,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_wordlist_token),
) -> Dict[str, Any]:
    """Ensure analysis exists.

    - If analysis is already available, returns DONE.
    - Otherwise, enqueue a rebuild job and return its status.

    Token-protected (same as wordlists) because this can trigger CPU work.
    """

    list_name = (list or "blue_archive_words").strip()
    pack_version = (pack or "").strip() or None

    meta_input, _ = prepare_input(db, list_name=list_name, pack_version=pack_version)

    meta = (
        db.query(models.BlueWarAnalysisMeta)
        .filter(models.BlueWarAnalysisMeta.analysis_key == meta_input.analysis_key)
        .order_by(models.BlueWarAnalysisMeta.created_at.desc())
        .first()
    )
    if meta:
        return {
            "analysis_key": meta_input.analysis_key,
            "state": "DONE",
        }

    job = enqueue_rebuild_job(db, list_name=list_name, pack_version=pack_version)
    return {
        "analysis_key": meta_input.analysis_key,
        "state": str(job.status),
        "job_id": int(job.id),
    }


@router.get("/syllable/{syl}")
def analysis_lookup_syllable(
    request: Request,
    syl: str,
    list: str = "blue_archive_words",
    pack: Optional[str] = None,
    explain: int = 0,
    max_steps: int = 10,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_wordlist_token),
) -> Dict[str, Any]:
    """Lookup syllable classification (WIN/LOSE/DRAW) for bots."""

    syllable = (syl or "").strip()
    if not syllable:
        raise HTTPException(status_code=400, detail="empty")

    list_name = (list or "blue_archive_words").strip()
    pack_version = (pack or "").strip() or None

    meta_input, words = prepare_input(db, list_name=list_name, pack_version=pack_version)

    meta = (
        db.query(models.BlueWarAnalysisMeta)
        .filter(models.BlueWarAnalysisMeta.analysis_key == meta_input.analysis_key)
        .first()
    )
    if not meta:
        raise HTTPException(status_code=404, detail="analysis missing")

    row = (
        db.query(models.BlueWarSyllableStat)
        .filter(models.BlueWarSyllableStat.analysis_key == meta_input.analysis_key)
        .filter(models.BlueWarSyllableStat.syllable == syllable)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="not found")

    out = {
        "analysis_key": meta_input.analysis_key,
        "syllable": row.syllable,
        "node_type": row.node_type,
        "out_moves": int(row.out_moves or 0),
        "in_moves": int(row.in_moves or 0),
        "win_moves": int(row.win_moves or 0),
        "lose_moves": int(row.lose_moves or 0),
        "draw_moves": int(row.draw_moves or 0),
        "sample_win_words": _parse_json_list(row.sample_win_words),
    }

    if int(explain or 0) == 1:
        ms = max(3, min(30, int(max_steps or 10)))
        out["explain"] = explain_syllable(
            analysis_key=meta_input.analysis_key,
            words=words,
            syllable=syllable,
            max_steps=ms,
            start_player=0,
        )

    return out


@router.get("/word/{word}")
def analysis_lookup_word(
    request: Request,
    word: str,
    list: str = "blue_archive_words",
    pack: Optional[str] = None,
    explain: int = 0,
    max_steps: int = 10,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_wordlist_token),
) -> Dict[str, Any]:
    """Lookup word classification (WIN/LOSE/DRAW) for bots."""

    w = (word or "").strip()
    if not w:
        raise HTTPException(status_code=400, detail="empty")

    list_name = (list or "blue_archive_words").strip()
    pack_version = (pack or "").strip() or None

    meta_input, words = prepare_input(db, list_name=list_name, pack_version=pack_version)

    meta = (
        db.query(models.BlueWarAnalysisMeta)
        .filter(models.BlueWarAnalysisMeta.analysis_key == meta_input.analysis_key)
        .first()
    )
    if not meta:
        raise HTTPException(status_code=404, detail="analysis missing")

    row = (
        db.query(models.BlueWarWordStat)
        .filter(models.BlueWarWordStat.analysis_key == meta_input.analysis_key)
        .filter(models.BlueWarWordStat.word == w)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="not found")

    out = {
        "analysis_key": meta_input.analysis_key,
        "word": row.word,
        "node_type": row.node_type,
        "start_syllable": row.start_syllable,
        "end_syllable": row.end_syllable,
    }

    if int(explain or 0) == 1:
        ms = max(3, min(30, int(max_steps or 10)))
        out["explain"] = explain_word(
            analysis_key=meta_input.analysis_key,
            words=words,
            word=w,
            max_steps=ms,
        )

    return out
