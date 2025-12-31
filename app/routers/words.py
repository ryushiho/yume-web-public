# app/routers/words.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.utils import dictpacks
from app.utils.wordlists import WORDLIST_LABELS


router = APIRouter(prefix="/words", tags=["words"])
templates = Jinja2Templates(directory="app/templates")


@dataclass
class WordRow:
    word: str


def _safe_int(v: Optional[str], default: int) -> int:
    try:
        if v is None:
            return default
        return int(str(v).strip())
    except Exception:
        return default


def _packs_tab_versions() -> List[Dict[str, str]]:
    """공개 단어 보기에서 노출할 고정 월별 탭."""
    tabs_raw = (getattr(settings, "WORDLIST_PACKS_TABS", "") or "").strip()
    if tabs_raw:
        versions = [v.strip() for v in tabs_raw.split(",") if v.strip()]
    else:
        versions = ["2025-10", "2025-12", "2026-01"]

    out: List[Dict[str, str]] = []
    for v in versions:
        if not dictpacks.is_valid_version(v):
            continue
        mm = v.split("-")[1]
        label = f"{int(mm)}월"
        out.append({"version": v, "label": label})
    return out


def _read_text_robust(path) -> str:
    """UTF-8 우선, 실패하면 CP949."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp949")


def _parse_txt_dedupe(text: str) -> List[str]:
    """txt(1줄=1단어) -> 단어 리스트 (중복 제거, 원본 순서 유지)."""
    seen = set()
    out: List[str] = []
    for line in (text or "").splitlines():
        w = (line or "").strip()
        if not w:
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


# 버전별 캐시: (mtime_ns, size) 기반
_BASE_CACHE: Dict[str, Tuple[Tuple[int, int], List[str]]] = {}
_SORT_CACHE: Dict[Tuple[str, str], Tuple[Tuple[int, int], List[str]]] = {}


def _load_public_words(dict_version: str) -> Tuple[List[str], Dict[str, Optional[str]], bool]:
    """(words, meta, exists)"""
    packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
    p = dictpacks.pack_file_path(dict_version, "public_words.txt", env_override=packs_dir)
    if not p.exists() or not p.is_file():
        return (
            [],
            {
                "dict_version": dict_version,
                "filename": "public_words.txt",
                "count": "0",
                "updated_at": None,
            },
            False,
        )

    st = p.stat()
    sig = (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))), int(st.st_size))

    cached = _BASE_CACHE.get(dict_version)
    if cached and cached[0] == sig:
        words = cached[1]
    else:
        txt = _read_text_robust(p)
        words = _parse_txt_dedupe(txt)
        _BASE_CACHE[dict_version] = (sig, words)
        # 정렬 캐시는 무효화
        for key in [k for k in list(_SORT_CACHE.keys()) if k[0] == dict_version]:
            _SORT_CACHE.pop(key, None)

    updated_at = datetime.fromtimestamp(st.st_mtime).isoformat()
    meta = {
        "dict_version": dict_version,
        "filename": "public_words.txt",
        "count": str(len(words)),
        "updated_at": updated_at,
    }
    return (words, meta, True)


def _sorted_words(words: List[str], dict_version: str, sort: str, sig: Tuple[int, int]) -> List[str]:
    ck = (dict_version, sort)
    cached = _SORT_CACHE.get(ck)
    if cached and cached[0] == sig:
        return cached[1]

    if sort == "alpha":
        out = sorted(words)
    elif sort == "alpha_desc":
        out = sorted(words, reverse=True)
    elif sort == "len":
        out = sorted(words, key=lambda w: (len(w), w))
    elif sort == "len_desc":
        out = sorted(words, key=lambda w: (-len(w), w))
    else:  # "source"
        out = list(words)

    _SORT_CACHE[ck] = (sig, out)
    return out


def _build_pager(page: int, total_pages: int, window: int = 2) -> List[Optional[int]]:
    """페이지 링크에 표시할 번호 목록을 만든다. None은 '…' 표시."""
    if total_pages <= 1:
        return [1]

    items: List[Optional[int]] = []
    for p in range(1, total_pages + 1):
        show = (p == 1) or (p == total_pages) or (abs(p - page) <= window)
        if show:
            items.append(p)
        else:
            if not items or items[-1] is not None:
                items.append(None)
    return items


@router.get("/", response_class=HTMLResponse)
def words_page(request: Request):
    """공개 단어 보기/검색 페이지.

    - 누구나 접근 가능
    - public_words.txt만 보기/검색 가능
    - 월별(버전) 탭: 10월/12월/1월
    """

    tabs = _packs_tab_versions()
    q_raw = (request.query_params.get("q") or "").strip()

    # 선택 버전
    v = (request.query_params.get("v") or "").strip()
    if not dictpacks.is_valid_version(v):
        # 기본값: default_version.txt -> 첫 탭
        packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
        dv = dictpacks.get_default_version(env_override=packs_dir, env_default=(settings.WORDLIST_PACKS_DEFAULT or "").strip())
        v = dv if (dv and dictpacks.is_valid_version(dv)) else (tabs[0]["version"] if tabs else "")

    # pagination
    page = _safe_int(request.query_params.get("page"), 1)
    page_size = _safe_int(request.query_params.get("page_size"), 200)
    if page < 1:
        page = 1
    if page_size < 20:
        page_size = 20
    if page_size > 1000:
        page_size = 1000

    # sort
    sort = (request.query_params.get("sort") or "alpha").strip().lower()
    sort_options: List[Tuple[str, str]] = [
        ("alpha", "가나다순"),
        ("alpha_desc", "가나다역순"),
        ("len_desc", "긴 단어"),
        ("len", "짧은 단어"),
        ("source", "원본 순서"),
    ]
    allowed_sorts = {k for k, _ in sort_options}
    if sort not in allowed_sorts:
        sort = "alpha"

    # load words
    words, meta, exists = _load_public_words(v)
    sig = _BASE_CACHE.get(v, ((0, 0), []))[0]

    # ordering(캐시) -> search(필터)
    # 정렬 결과를 버전+정렬 기준으로만 캐시하고, 검색은 그 위에서 필터링한다.
    base_sorted = _sorted_words(words, v, sort, sig)

    # search (공백 정규화 1회)
    q = q_raw
    if q:
        q_nospace = "".join(q.split())
        if q_nospace and q_nospace != q:
            filtered_sorted = [w for w in base_sorted if (q in w) or (q_nospace in w.replace(" ", ""))]
        else:
            filtered_sorted = [w for w in base_sorted if q in w]
    else:
        filtered_sorted = base_sorted

    total = len(filtered_sorted)
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size
    slice_words = filtered_sorted[offset : offset + page_size]
    rows = [WordRow(word=w) for w in slice_words]

    # pagination query string (page만 바꿔 끼우기)
    qs_base = "v={v}&q={q}&page_size={ps}&sort={s}".format(
        v=quote_plus(v),
        q=quote_plus(q),
        ps=page_size,
        s=quote_plus(sort),
    )
    qs_tab_base = "q={q}&page_size={ps}&sort={s}".format(
        q=quote_plus(q),
        ps=page_size,
        s=quote_plus(sort),
    )
    page_items = _build_pager(page, total_pages)

    # 탭 활성 표시
    packs_tabs = []
    for t in tabs:
        packs_tabs.append({
            "version": t["version"],
            "label": t["label"],
            "active": (t["version"] == v),
        })

    return templates.TemplateResponse(
        "words.html",
        {
            "request": request,
            "packs_tabs": packs_tabs,
            "dict_version": v,
            "list_name": "public_words",
            "list_label": WORDLIST_LABELS.get("public_words", "전체 단어 목록"),
            "q": q,
            "sort": sort,
            "sort_options": sort_options,
            "page": page,
            "page_size": page_size,
            "page_size_options": [50, 100, 200, 500, 1000],
            "total": total,
            "total_pages": total_pages,
            "page_items": page_items,
            "qs_base": qs_base,
            "qs_tab_base": qs_tab_base,
            "offset": offset,
            "rows": rows,
            "meta": meta,
            "pack_exists": exists,
        },
    )
