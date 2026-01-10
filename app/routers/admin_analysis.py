# app/routers/admin_analysis.py

from __future__ import annotations

from typing import Any, Dict, Optional
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_admin_user
from app.utils.wordlists import WORDLIST_LABELS
from app.utils import dictpacks
from app.config import settings
from app.bluewar.analysis_engine import prepare_input, explain_syllable, explain_word
from app.bluewar.analysis_jobs import enqueue_rebuild_job
from app.bluewar.suggestion_export import build_suggestion_text, fetch_neutral_words
from app.bluewar import upload_store
from app.routers.api_wordlists import _parse_txt as parse_wordlist_txt
from app import models


router = APIRouter(prefix="/admin/analysis", tags=["admin-analysis"])
templates = Jinja2Templates(directory="app/templates")


def _pack_versions() -> list[str]:
    packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
    return dictpacks.list_versions(packs_dir)


@router.get("/upload")
def analysis_upload_page(
    request: Request,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    # Upload analysis intentionally focuses on the main game list.
    list_name = (request.query_params.get("list") or "blue_archive_words").strip()
    if list_name != "blue_archive_words":
        list_name = "blue_archive_words"

    uploads = upload_store.list_uploads(limit=30)
    return templates.TemplateResponse(
        "admin_analysis_upload.html",
        {
            "request": request,
            "list_labels": {"blue_archive_words": WORDLIST_LABELS.get("blue_archive_words", "루트전 단어")},
            "list_name": list_name,
            "uploads": uploads,
            "ok": (request.query_params.get("ok") or "").strip() or None,
            "error": (request.query_params.get("error") or "").strip() or None,
        },
    )


@router.post("/upload")
async def analysis_upload_submit(
    request: Request,
    list: str = Form("blue_archive_words"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    list_name = (list or "").strip() or "blue_archive_words"
    if list_name != "blue_archive_words":
        list_name = "blue_archive_words"

    if not file or not file.filename:
        return RedirectResponse(url="/admin/analysis/upload?error=no_file", status_code=303)

    # Read whole file (txt). Keep a simple safety limit.
    raw = await file.read()
    if not raw:
        return RedirectResponse(url="/admin/analysis/upload?error=empty", status_code=303)
    if len(raw) > 20 * 1024 * 1024:
        return RedirectResponse(url="/admin/analysis/upload?error=too_large", status_code=303)

    info = upload_store.save_upload(list_name=list_name, original_filename=file.filename, raw=raw)
    pack = f"upload:{info.upload_id}"

    # Immediately enqueue an async rebuild job.
    enqueue_rebuild_job(db, list_name=list_name, pack_version=pack)

    return RedirectResponse(
        url=f"/admin/analysis/?list={list_name}&pack={pack}&ok=upload_queued",
        status_code=303,
    )


def _read_text_robust(path: Path) -> str:
    """UTF-8 우선, 실패하면 CP949로 읽는다."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp949")


def _sort_words(words: list[str]) -> list[str]:
    out = [ (w or '').strip() for w in (words or []) if (w or '').strip() ]
    # deterministic (same as suggestion_export): short -> lexicographic
    out = sorted(set(out), key=lambda x: (len(x), x))
    return out


def _suggestion_diff(*, pack: str, packs_dir: str, neutral_words: list[str]) -> dict | None:
    """Compare generated neutral words with current pack's suggestion.txt.

    Returns summary dict or None if file is missing.
    """
    if not pack:
        return None
    try:
        p = dictpacks.pack_file_path(pack, 'suggestion.txt', env_override=packs_dir)
        if not p.exists() or not p.is_file():
            return None
        current_txt = _read_text_robust(p)
        current_words = parse_wordlist_txt(current_txt)
        cur_set = set(current_words)
        neu_set = set(neutral_words)

        added = sorted(list(neu_set - cur_set), key=lambda x: (len(x), x))
        removed = sorted(list(cur_set - neu_set), key=lambda x: (len(x), x))
        same = len(neu_set & cur_set)

        return {
            'pack_path': str(p),
            'current_count': len(cur_set),
            'neutral_count': len(neu_set),
            'same_count': int(same),
            'added_count': len(added),
            'removed_count': len(removed),
            'added_sample': added[:40],
            'removed_sample': removed[:40],
        }
    except Exception:
        return None


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


    # quick stats (for UI)
    word_counts: Dict[str, int] = {}
    syllable_counts: Dict[str, int] = {}
    neutral_word_count: int = 0

    if stored:
        try:
            wc_rows = (
                db.query(models.BlueWarWordStat.node_type, func.count(models.BlueWarWordStat.id))
                .filter(models.BlueWarWordStat.analysis_key == meta_input.analysis_key)
                .group_by(models.BlueWarWordStat.node_type)
                .all()
            )
            word_counts = {str(t): int(c) for (t, c) in wc_rows if t}

            sc_rows = (
                db.query(models.BlueWarSyllableStat.node_type, func.count(models.BlueWarSyllableStat.id))
                .filter(models.BlueWarSyllableStat.analysis_key == meta_input.analysis_key)
                .group_by(models.BlueWarSyllableStat.node_type)
                .all()
            )
            syllable_counts = {str(t): int(c) for (t, c) in sc_rows if t}
            neutral_word_count = int(word_counts.get("DRAW", 0))
        except Exception:
            # best-effort (never break admin page)
            word_counts = {}
            syllable_counts = {}
            neutral_word_count = 0

    # Suggestion preview/diff (best-effort)
    suggest_preview_plain = None
    suggest_preview_grouped = None
    suggest_diff = None

    if stored:
        try:
            neutral_words = fetch_neutral_words(db, meta_input.analysis_key)
            neutral_sorted = _sort_words(neutral_words)

            # preview: keep small to avoid heavy HTML
            head_plain = neutral_sorted[:120]
            if head_plain:
                suggest_preview_plain = "\n".join(head_plain)
                if len(neutral_sorted) > len(head_plain):
                    suggest_preview_plain += "\n..."

            # grouped preview: take first ~300 words then group
            head_group_words = neutral_sorted[:300]
            if head_group_words:
                suggest_preview_grouped = build_suggestion_text(head_group_words, fmt="grouped")
                lines2 = (suggest_preview_grouped or "").splitlines()
                if len(lines2) > 25:
                    suggest_preview_grouped = "\n".join(lines2[:25]) + "\n..."

            packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
            if meta_input.pack_version:
                suggest_diff = _suggestion_diff(pack=meta_input.pack_version, packs_dir=packs_dir, neutral_words=neutral_words)
        except Exception:
            suggest_preview_plain = None
            suggest_preview_grouped = None
            suggest_diff = None

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
            "word_counts": word_counts,
            "syllable_counts": syllable_counts,
            "neutral_word_count": neutral_word_count,
            "suggest_preview_plain": suggest_preview_plain,
            "suggest_preview_grouped": suggest_preview_grouped,
            "suggest_diff": suggest_diff,
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



@router.get("/suggestion.txt")
def analysis_suggestion_download(
    request: Request,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    list_name = (request.query_params.get("list") or "blue_archive_words").strip()
    pack = (request.query_params.get("pack") or "").strip() or None
    fmt = (request.query_params.get("fmt") or "grouped").strip().lower()

    meta_input, _ = prepare_input(db, list_name=list_name, pack_version=pack)

    stored = (
        db.query(models.BlueWarAnalysisMeta)
        .filter(models.BlueWarAnalysisMeta.analysis_key == meta_input.analysis_key)
        .order_by(models.BlueWarAnalysisMeta.created_at.desc())
        .first()
    )
    if not stored:
        return RedirectResponse(
            url=f"/admin/analysis/?list={list_name}&pack={pack or ''}&error=no_result",
            status_code=303,
        )

    words = fetch_neutral_words(db, meta_input.analysis_key)
    txt = build_suggestion_text(words, fmt=fmt)  # type: ignore[arg-type]

    safe_list = (meta_input.list_name or "").replace(":", "_")
    safe_pack = (meta_input.pack_version or "db").replace(":", "_")
    fn = f"suggestion_{safe_list}_{safe_pack}_{meta_input.analysis_key[:8]}.txt"
    headers = {"Content-Disposition": f'attachment; filename="{fn}"'}
    return Response(content=txt, media_type="text/plain; charset=utf-8", headers=headers)




@router.get("/suggestion/diff.txt")
def analysis_suggestion_diff_download(
    request: Request,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    """현재 팩 suggestion.txt와 '생성될 중립 단어'의 차이를 텍스트로 내려준다."""
    list_name = (request.query_params.get("list") or "blue_archive_words").strip()
    pack = (request.query_params.get("pack") or "").strip() or None

    meta_input, _ = prepare_input(db, list_name=list_name, pack_version=pack)

    stored = (
        db.query(models.BlueWarAnalysisMeta)
        .filter(models.BlueWarAnalysisMeta.analysis_key == meta_input.analysis_key)
        .order_by(models.BlueWarAnalysisMeta.created_at.desc())
        .first()
    )
    if not stored or not meta_input.pack_version:
        return RedirectResponse(
            url=f"/admin/analysis/?list={list_name}&pack={pack or ''}&error=no_result",
            status_code=303,
        )

    if meta_input.pack_version.startswith("upload:"):
        return RedirectResponse(
            url=f"/admin/analysis/?list={list_name}&pack={meta_input.pack_version}&error=upload_pack",
            status_code=303,
        )

    neutral_words = fetch_neutral_words(db, meta_input.analysis_key)

    packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
    diff = _suggestion_diff(pack=meta_input.pack_version, packs_dir=packs_dir, neutral_words=neutral_words)
    if not diff:
        return RedirectResponse(
            url=f"/admin/analysis/?list={list_name}&pack={meta_input.pack_version}&error=no_suggestion_file",
            status_code=303,
        )

    lines = []
    lines.append(f"analysis_key: {meta_input.analysis_key}")
    lines.append(f"pack: {meta_input.pack_version}")
    lines.append(f"current_count: {diff.get('current_count')}")
    lines.append(f"neutral_count: {diff.get('neutral_count')}")
    lines.append(f"same: {diff.get('same_count')}")
    lines.append(f"added: {diff.get('added_count')}")
    lines.append(f"removed: {diff.get('removed_count')}")
    lines.append("")

    lines.append("[added_sample]")
    for w in diff.get('added_sample') or []:
        lines.append(str(w))
    lines.append("")

    lines.append("[removed_sample]")
    for w in diff.get('removed_sample') or []:
        lines.append(str(w))
    lines.append("")

    content = "\n".join(lines)
    safe_pack = meta_input.pack_version.replace(":", "_")
    fn = f"suggestion_diff_{safe_pack}_{meta_input.analysis_key[:8]}.txt"
    headers = {"Content-Disposition": f'attachment; filename="{fn}"'}
    return Response(content=content, media_type="text/plain; charset=utf-8", headers=headers)


@router.post("/suggestion/apply")
def analysis_suggestion_apply(
    request: Request,
    fmt: str = Form("grouped"),
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    # Apply writes to a dict-pack on filesystem.
    list_name = (request.query_params.get("list") or "blue_archive_words").strip()
    pack = (request.query_params.get("pack") or "").strip() or None

    meta_input, _ = prepare_input(db, list_name=list_name, pack_version=pack)
    target_pack = (meta_input.pack_version or "").strip() or None
    if not target_pack:
        return RedirectResponse(
            url=f"/admin/analysis/?list={list_name}&error=need_pack",
            status_code=303,
        )

    # Uploaded packs are not real dict-pack versions (can't be applied).
    if target_pack.startswith("upload:"):
        return RedirectResponse(
            url=f"/admin/analysis/?list={list_name}&pack={target_pack}&error=upload_pack",
            status_code=303,
        )

    stored = (
        db.query(models.BlueWarAnalysisMeta)
        .filter(models.BlueWarAnalysisMeta.analysis_key == meta_input.analysis_key)
        .order_by(models.BlueWarAnalysisMeta.created_at.desc())
        .first()
    )
    if not stored:
        return RedirectResponse(
            url=f"/admin/analysis/?list={list_name}&pack={target_pack}&error=no_result",
            status_code=303,
        )

    words = fetch_neutral_words(db, meta_input.analysis_key)
    txt = build_suggestion_text(words, fmt=(fmt or "grouped").strip().lower())  # type: ignore[arg-type]

    packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
    version_dir = dictpacks.versions_dir(packs_dir) / target_pack
    if not version_dir.exists() or not version_dir.is_dir():
        return RedirectResponse(
            url=f"/admin/analysis/?list={list_name}&pack={target_pack}&error=bad_pack",
            status_code=303,
        )

    # Write to <pack>/suggestion.txt with atomic replace + backup
    p = dictpacks.pack_file_path(target_pack, "suggestion.txt", env_override=packs_dir)
    if not p.exists():
        return RedirectResponse(
            url=f"/admin/analysis/?list={list_name}&pack={target_pack}&error=no_suggestion_file",
            status_code=303,
        )

    try:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        bak = p.with_suffix(p.suffix + f".bak_{ts}")
        try:
            bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            # ignore backup failure (never block apply)
            pass

        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(txt, encoding="utf-8")
        tmp.replace(p)
    except Exception:
        return RedirectResponse(
            url=f"/admin/analysis/?list={list_name}&pack={target_pack}&error=write_failed",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/admin/analysis/?list={list_name}&pack={target_pack}&ok=suggestion_applied",
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


@router.get("/word/{word}")
def analysis_word_detail(
    word: str,
    request: Request,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    list_name = (request.query_params.get("list") or "blue_archive_words").strip()
    pack = (request.query_params.get("pack") or "").strip() or None
    max_steps = int((request.query_params.get("max_steps") or "10").strip() or "10")
    max_steps = max(3, min(30, max_steps))

    meta_input, words = prepare_input(db, list_name=list_name, pack_version=pack)

    w = (word or "").strip()
    row = (
        db.query(models.BlueWarWordStat)
        .filter(models.BlueWarWordStat.analysis_key == meta_input.analysis_key)
        .filter(models.BlueWarWordStat.word == w)
        .first()
    )

    info = explain_word(analysis_key=meta_input.analysis_key, words=words, word=w, max_steps=max_steps)

    return templates.TemplateResponse(
        "admin_analysis_word_detail.html",
        {
            "request": request,
            "list_labels": WORDLIST_LABELS,
            "list_name": list_name,
            "pack": pack,
            "analysis_key": meta_input.analysis_key,
            "word": w,
            "max_steps": max_steps,
            "row": row,
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
