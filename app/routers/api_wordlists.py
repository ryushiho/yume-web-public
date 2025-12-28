# app/routers/api_wordlists.py

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status, Body
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_admin_user_api
from app import models


MAX_PAGE_SIZE = 1000


router = APIRouter(
    prefix="/api/bluewar/wordlists",
    tags=["api-bluewar-wordlists"],
)


# Phase 1 기준: 웹에서 관리할 리스트 2개(파일명 기준)
ALLOWED_LISTS = {
    "suggestion": "suggestion.txt",
    "blue_archive_words": "blue_archive_words.txt",
}


def _assert_list_name(list_name: str) -> str:
    v = (list_name or "").strip()
    if v not in ALLOWED_LISTS:
        raise HTTPException(status_code=404, detail="unknown wordlist")
    return v


def _build_txt(words: List[str]) -> str:
    # 파일 호환을 위해 마지막에 개행을 하나 붙여준다.
    if not words:
        return ""
    return "\n".join(words) + "\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_txt(text: str) -> List[str]:
    """txt(1줄=1단어) -> 단어 배열.

    - 공백/탭/개행 제거
    - 빈 줄 제거
    - 중복 제거(원본 순서 유지)
    """
    seen = set()
    out: List[str] = []
    for line in text.splitlines():
        w = (line or "").strip()
        if not w:
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def _clean_word(word: Optional[str]) -> str:
    """단어 입력값 검증/정규화.

    - 앞뒤 공백 제거
    - 빈 문자열 금지
    - 길이 제한(모델 컬럼 길이와 맞춤)
    - 내부 공백/탭 금지(끝말잇기 단어 호환)
    """
    w = (word or "").strip()
    if not w:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty word")
    if len(w) > 200:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="word too long")
    if any(ch.isspace() for ch in w):
        # strip 이후에도 남은 공백/탭/개행은 허용하지 않는다.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="whitespace not allowed")
    return w


def _upsert_wordlist_overwrite(db: Session, list_name: str, words: List[str]) -> None:
    """해당 리스트를 완전히 교체한다(덮어쓰기)."""
    db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == list_name).delete()
    now = datetime.utcnow()
    rows = [models.BlueWarWord(list_name=list_name, word=w, created_at=now, updated_at=now) for w in words]
    if rows:
        db.bulk_save_objects(rows)
    db.commit()


@router.post("/{list_name}/words")
def word_add(
    list_name: str,
    data: Dict[str, str] = Body(...),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_admin_user_api),
) -> Dict[str, object]:
    """관리자 전용: 단어 1개 추가."""
    name = _assert_list_name(list_name)
    w = _clean_word((data or {}).get("word", ""))
    row = models.BlueWarWord(list_name=name, word=w)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="duplicate")
    db.refresh(row)
    return {"id": int(row.id), "list_name": name, "word": row.word}


@router.patch("/{list_name}/words/{word_id}")
def word_update(
    list_name: str,
    word_id: int,
    data: Dict[str, str] = Body(...),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_admin_user_api),
) -> Dict[str, object]:
    """관리자 전용: 단어 1개 수정."""
    name = _assert_list_name(list_name)
    row = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .filter(models.BlueWarWord.id == int(word_id))
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    w = _clean_word((data or {}).get("word", ""))
    row.word = w
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="duplicate")
    db.refresh(row)
    return {"id": int(row.id), "list_name": name, "word": row.word}


@router.delete("/{list_name}/words/{word_id}")
def word_delete(
    list_name: str,
    word_id: int,
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_admin_user_api),
) -> Dict[str, object]:
    """관리자 전용: 단어 1개 삭제."""
    name = _assert_list_name(list_name)
    row = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .filter(models.BlueWarWord.id == int(word_id))
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(row)
    db.commit()
    return {"deleted": 1, "id": int(word_id), "list_name": name}


@router.post("/{list_name}/words/bulk_delete")
def word_bulk_delete(
    list_name: str,
    data: Dict[str, List[int]] = Body(...),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_admin_user_api),
) -> Dict[str, object]:
    """관리자 전용: 여러 단어 삭제.

    body 예)
      {"ids": [1,2,3]}
    """
    name = _assert_list_name(list_name)
    ids = (data or {}).get("ids") or []
    ids = [int(x) for x in ids if str(x).strip().isdigit()]
    # 안전장치: 한 번에 너무 많이 지우는 실수 방지
    if len(ids) > 5000:
        raise HTTPException(status_code=400, detail="too many ids")
    if not ids:
        return {"deleted": 0, "list_name": name}

    deleted = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .filter(models.BlueWarWord.id.in_(ids))
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted": int(deleted), "list_name": name}


@router.get("/meta")
def wordlists_meta(db: Session = Depends(get_db)) -> Dict[str, Dict[str, Optional[str]]]:
    """봇/클라이언트가 캐시 갱신 판단에 쓰는 메타."""
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for name in ALLOWED_LISTS.keys():
        q = db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == name)
        count = q.count()
        last_updated: Optional[datetime] = (
            db.query(func.max(models.BlueWarWord.updated_at))
            .filter(models.BlueWarWord.list_name == name)
            .scalar()
        )

        words = [w.word for w in q.order_by(models.BlueWarWord.id.asc()).all()]
        txt = _build_txt(words)
        out[name] = {
            "filename": ALLOWED_LISTS[name],
            "count": str(count),
            "updated_at": last_updated.isoformat() if last_updated else None,
            "sha256": _sha256(txt),
        }
    return out


@router.get("/{list_name}.txt", response_class=PlainTextResponse)
def wordlist_txt(list_name: str, db: Session = Depends(get_db)) -> PlainTextResponse:
    """단어 리스트를 txt 형태로 제공한다.

    예)
      - /api/bluewar/wordlists/suggestion.txt
      - /api/bluewar/wordlists/blue_archive_words.txt
    """
    name = _assert_list_name(list_name)
    words = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .order_by(models.BlueWarWord.id.asc())
        .all()
    )
    txt = _build_txt([w.word for w in words])

    # content-type은 PlainTextResponse가 알아서 text/plain
    return PlainTextResponse(content=txt)


@router.get("/{list_name}/download")
def wordlist_download(list_name: str, db: Session = Depends(get_db)) -> Response:
    """브라우저에서 바로 다운로드되도록 Content-Disposition을 포함한다."""
    name = _assert_list_name(list_name)
    words = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .order_by(models.BlueWarWord.id.asc())
        .all()
    )
    txt = _build_txt([w.word for w in words])
    filename = ALLOWED_LISTS[name]
    return Response(
        content=txt,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{list_name}/words")
def wordlist_words(
    list_name: str,
    q: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
) -> Dict[str, object]:
    """단어 리스트를 페이지 단위(JSON)로 제공한다.

    Phase 3(관리자 UI 검색/페이지네이션)에서 사용.

    NOTE: .txt 엔드포인트가 이미 공개이므로, 이 엔드포인트도 공개(읽기)로 둔다.
    """
    name = _assert_list_name(list_name)
    query = (q or "").strip()

    base = db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == name)
    if query:
        # SQLite에서 ILIKE가 애매할 수 있어 contains로 간단히 처리한다(한글 단어 위주).
        base = base.filter(models.BlueWarWord.word.contains(query))

    total = int(base.count())
    offset = int((page - 1) * page_size)
    rows = (
        base.order_by(models.BlueWarWord.id.asc())
        .offset(offset)
        .limit(int(page_size))
        .all()
    )
    items = [{"id": int(w.id), "word": w.word} for w in rows]

    return {
        "list_name": name,
        "q": query,
        "total": total,
        "page": int(page),
        "page_size": int(page_size),
        "items": items,
    }


@router.post("/{list_name}/upload")
async def wordlist_upload(
    list_name: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_admin_user_api),
) -> Dict[str, str]:
    """관리자 전용: txt 업로드로 전체 덮어쓰기.

    - multipart/form-data
    - file: UploadFile (.txt)
    """
    name = _assert_list_name(list_name)

    raw = await file.read()
    if raw is None or len(raw) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty file")
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="file too large")

    # 원칙: UTF-8 권장. 다만 업로드 UX를 위해 CP949 한 번 더 시도한다.
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp949")
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported encoding (use UTF-8)")

    words = _parse_txt(text)
    _upsert_wordlist_overwrite(db, name, words)

    txt = _build_txt(words)
    return {"list_name": name, "filename": ALLOWED_LISTS[name], "count": str(len(words)), "sha256": _sha256(txt)}
