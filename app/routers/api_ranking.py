# app/routers/api_ranking.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.routers.api_bluewar import verify_api_token
from app.routers.ranking import compute_ranking_rows, RankingRow


router = APIRouter(
    prefix="/api/bluewar",
    tags=["api-bluewar-ranking"],
)


@router.get("/ranking")
def api_bluewar_ranking(
    db: Session = Depends(get_db),
    _token_ok=Depends(verify_api_token),
    limit: int = Query(default=50, ge=1, le=200),
    source_app: str = Query(default="shiho"),
    mode: str = Query(default="pvp"),
) -> Dict[str, Any]:
    """블루전 랭킹(JSON).

    - 디스코드 봇(#승률 임베드)과 웹 페이지의 계산을 100% 동일하게 만들기 위해 제공.
    - 토큰이 설정되어 있으면 X-API-Token 검증을 통과해야 한다.
    """
    rows: List[RankingRow] = compute_ranking_rows(db, limit=limit, source_app=source_app, mode=mode)
    return {
        "mode": (mode or "pvp").strip().lower(),
        "source_app": (source_app or "shiho").strip().lower(),
        "rows": rows,
    }
