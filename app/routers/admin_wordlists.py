# app/routers/admin_wordlists.py

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.dependencies import get_db, get_current_admin_user
from app.routers.api_wordlists import (
    ALLOWED_LISTS,
    _assert_list_name,
    _build_txt,
    _parse_txt,
    _sha256,
    _upsert_wordlist_overwrite,
)


router = APIRouter(prefix="/admin/wordlists", tags=["admin-wordlists"])
templates = Jinja2Templates(directory="app/templates")


def _meta_for(db: Session, list_name: str) -> Dict[str, Optional[str]]:
    q = db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == list_name)
    count = q.count()
    last_updated: Optional[datetime] = (
        db.query(func.max(models.BlueWarWord.updated_at))
        .filter(models.BlueWarWord.list_name == list_name)
        .scalar()
    )
    words = [w.word for w in q.order_by(models.BlueWarWord.id.asc()).all()]
    txt = _build_txt(words)
    return {
        "list_name": list_name,
        "filename": ALLOWED_LISTS[list_name],
        "count": str(count),
        "updated_at": last_updated.isoformat() if last_updated else None,
        "sha256": _sha256(txt),
    }


def _safe_int(v: Optional[str], default: int) -> int:
    try:
        if v is None:
            return default
        return int(str(v).strip())
    except Exception:
        return default


@router.get("/")
def admin_wordlists_page(
    request: Request,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    # 상태 메시지
    ok = request.query_params.get("ok")
    error = request.query_params.get("error")
    which = request.query_params.get("which")

    metas = {name: _meta_for(db, name) for name in ALLOWED_LISTS.keys()}
    return templates.TemplateResponse(
        "admin_wordlists.html",
        {
            "request": request,
            "metas": metas,
            "ok": ok,
            "error": error,
            "which": which,
        },
    )


@router.get("/{list_name}")
def admin_wordlist_detail(
    request: Request,
    list_name: str,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    """Phase 3: 검색/페이지네이션 포함 단어 리스트 보기."""
    try:
        name = _assert_list_name(list_name)
    except HTTPException:
        return RedirectResponse(url="/admin/wordlists/?error=unknown&which=" + str(list_name), status_code=303)

    # 상태 메시지
    ok = request.query_params.get("ok")
    error = request.query_params.get("error")

    q = (request.query_params.get("q") or "").strip()
    page = _safe_int(request.query_params.get("page"), 1)
    page_size = _safe_int(request.query_params.get("page_size"), 200)
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 200
    if page_size > 1000:
        page_size = 1000

    meta = _meta_for(db, name)

    base = db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == name)
    if q:
        base = base.filter(models.BlueWarWord.word.contains(q))

    total = int(base.count())
    # 총 페이지 수(최소 1)
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size
    rows = (
        base.order_by(models.BlueWarWord.id.asc())
        .offset(int(offset))
        .limit(int(page_size))
        .all()
    )

    return templates.TemplateResponse(
        "admin_wordlist_detail.html",
        {
            "request": request,
            "list_name": name,
            "meta": meta,
            "q": q,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "rows": rows,
            "offset": offset,
            "ok": ok,
            "error": error,
        },
    )


@router.post("/upload/{list_name}")
async def admin_wordlists_upload(
    request: Request,
    list_name: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    """관리자 페이지 폼 업로드 처리(전체 덮어쓰기).

    - overview(/admin/wordlists/)에서 호출되면 overview로 돌아감
    - detail(/admin/wordlists/{list_name})에서 호출되면 next=... 쿼리로 돌아감
    """
    next_url = (request.query_params.get("next") or "").strip()
    # open redirect 방지: 내부 경로만 허용
    if next_url and not next_url.startswith("/admin/wordlists"):
        next_url = ""

    def _redir(url: str) -> RedirectResponse:
        return RedirectResponse(url=url, status_code=303)

    def _redir_error(err: str, which_name: str) -> RedirectResponse:
        if next_url:
            sep = "&" if "?" in next_url else "?"
            return _redir(url=f"{next_url}{sep}error={err}")
        return _redir(url=f"/admin/wordlists/?error={err}&which={which_name}")

    try:
        name = _assert_list_name(list_name)
    except HTTPException:
        return _redir(url="/admin/wordlists/?error=unknown&which=" + str(list_name))

    raw = await file.read()
    if raw is None or len(raw) == 0:
        return _redir_error("empty", name)
    if len(raw) > 5 * 1024 * 1024:
        return _redir_error("toolarge", name)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp949")
        except Exception:
            return _redir_error("encoding", name)

    words = _parse_txt(text)
    _upsert_wordlist_overwrite(db, name, words)

    if next_url:
        sep = "&" if "?" in next_url else "?"
        return _redir(url=f"{next_url}{sep}ok=1")
    return _redir(url=f"/admin/wordlists/?ok=1&which={name}")
