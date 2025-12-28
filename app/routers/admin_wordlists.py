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
from app.routers.api_wordlists import ALLOWED_LISTS, _assert_list_name, _parse_txt, _upsert_wordlist_overwrite


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
    return {
        "list_name": list_name,
        "filename": ALLOWED_LISTS[list_name],
        "count": str(count),
        "updated_at": last_updated.isoformat() if last_updated else None,
    }


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


@router.post("/upload/{list_name}")
async def admin_wordlists_upload(
    request: Request,
    list_name: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    """관리자 페이지 폼 업로드 처리(전체 덮어쓰기)."""
    try:
        name = _assert_list_name(list_name)
    except HTTPException:
        return RedirectResponse(url="/admin/wordlists/?error=unknown&which=" + str(list_name), status_code=303)

    raw = await file.read()
    if raw is None or len(raw) == 0:
        return RedirectResponse(url=f"/admin/wordlists/?error=empty&which={name}", status_code=303)
    if len(raw) > 5 * 1024 * 1024:
        return RedirectResponse(url=f"/admin/wordlists/?error=toolarge&which={name}", status_code=303)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp949")
        except Exception:
            return RedirectResponse(url=f"/admin/wordlists/?error=encoding&which={name}", status_code=303)

    words = _parse_txt(text)
    _upsert_wordlist_overwrite(db, name, words)
    return RedirectResponse(url=f"/admin/wordlists/?ok=1&which={name}", status_code=303)
