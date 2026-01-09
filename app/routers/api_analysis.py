# app/routers/api_analysis.py

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.bluewar.analysis_engine import prepare_input
from app.bluewar.suggestion_export import build_suggestion_text, fetch_neutral_words
from app import models
from app.routers.api_wordlists import _require_wordlist_token


router = APIRouter(prefix="/api/bluewar/analysis", tags=["bluewar-analysis-api"])


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
