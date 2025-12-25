# app/routers/api_bluewar.py

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from app.database import get_db
from app import models

router = APIRouter(
    prefix="/bluewar",
    tags=["bluewar_api"],
)


# ============================
#   인증 (봇 → 관리자 웹)
# ============================
def get_expected_api_token() -> Optional[str]:
    """
    config.settings 에서 API 토큰 값을 가져온다.
    - YUME_API_TOKEN 또는 API_TOKEN 중 하나를 사용.
    - 둘 다 없으면 토큰 검증을 하지 않는다(=개발용 오픈 상태).
    """
    return getattr(settings, "API_TOKEN", None)


async def verify_api_token(
    x_api_token: Optional[str] = Header(None, alias="X-API-Token")
) -> None:
    expected = get_expected_api_token()
    if expected is None:
        # 설정 안 돼 있으면 검증 생략 (개발용)
        return

    if not x_api_token or x_api_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
        )


# ============================
#   Pydantic 입력 모델
# ============================


class BlueWarParticipantIn(BaseModel):
    discord_id: Optional[str] = None
    name: Optional[str] = None
    ai_name: Optional[str] = None
    side: int
    is_winner: bool
    score: Optional[int] = None
    turns: Optional[int] = None


class BlueWarMatchIn(BaseModel):
    mode: str
    status: str
    starter_discord_id: str
    winner_discord_id: Optional[str] = None
    loser_discord_id: Optional[str] = None
    win_gap: Optional[int] = None
    total_rounds: Optional[int] = None
    started_at: datetime
    finished_at: datetime
    note: Optional[str] = None

    # 🔵 디스코드에서 넘어오는 단어 복기 로그
    review_log: Optional[str] = None

    participants: List[BlueWarParticipantIn]


# ============================
#   엔드포인트
# ============================


@router.post(
    "/matches",
    dependencies=[Depends(verify_api_token)],
)
async def create_match(
    data: BlueWarMatchIn,
    db: Session = Depends(get_db),
):
    """
    디스코드 봇(blue_war.py)이 한 판 끝났을 때 호출하는 엔드포인트.

    - BlueWarMatch 한 줄 생성
    - BlueWarParticipant 여러 줄 생성
    - 필요하면 users 테이블과도 연결 (discord_id 기준)
    """
    # 방어적 정규화: 봇 쪽 mode/status 값 표기가 조금 다르게 오더라도 수용
    mode = (data.mode or "").strip().lower()
    if mode in {"pv", "pve", "ai", "practice"}:
        mode = "practice"
    elif mode in {"pvp", "versus", "vs"}:
        mode = "pvp"
    else:
        # 알 수 없는 값도 일단 저장은 하되, 공백만 방지
        mode = mode or "unknown"

    status = (data.status or "").strip().lower() or "unknown"

    # 1) 매치 기본 정보 저장
    match = models.BlueWarMatch(
        mode=mode,
        status=status,
        starter_discord_id=data.starter_discord_id,
        winner_discord_id=data.winner_discord_id,
        loser_discord_id=data.loser_discord_id,
        win_gap=data.win_gap,
        total_rounds=data.total_rounds,
        started_at=data.started_at,
        finished_at=data.finished_at,
        note=data.note,
        review_log=data.review_log,
    )
    db.add(match)
    db.flush()  # match.id 확보용

    # 2) 참가자 정보 저장
    for p in data.participants:
        # discord_id 가 있으면 users 테이블 upsert + 연결
        user_obj = None
        if p.discord_id:
            user_obj = (
                db.query(models.User)
                .filter(models.User.discord_id == p.discord_id)
                .first()
            )
            if user_obj is None:
                user_obj = models.User(
                    discord_id=p.discord_id,
                    nickname=p.name,
                    note=None,
                    base_wins=0,
                    base_losses=0,
                )
                db.add(user_obj)
                db.flush()
            else:
                # 닉네임이 비어 있을 때만 채우기
                if (not user_obj.nickname) and p.name:
                    user_obj.nickname = p.name

        participant = models.BlueWarParticipant(
            match=match,
            user=user_obj,
            discord_id=p.discord_id,
            name=p.name,
            ai_name=p.ai_name,
            side=p.side,
            is_winner=p.is_winner,
            score=p.score,
            turns=p.turns,
        )
        db.add(participant)

    db.commit()
    db.refresh(match)

    return {"ok": True, "match_id": match.id}
