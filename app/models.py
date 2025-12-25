# app/models.py

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class AdminUser(Base):
    """
    (선택) 관리자 계정 테이블.
    지금은 admins.json 을 쓰고 있어도, 추후 마이그레이션용으로 남겨 둔 기본 구조.
    """
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    """
    블루전 유저(디스코드 유저) 테이블.
    - 유저 관리 / 유저 정보 수정 / 전적 수정 페이지에서 사용하는 모델.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    # 디스코드 ID (snowflake)
    discord_id = Column(String(32), unique=True, nullable=False)

    # 닉네임 / 이름
    nickname = Column(String(100), nullable=True)

    # 관리자 메모
    note = Column(Text, nullable=True)

    # 기본 전적(핸디캡)용 값
    base_wins = Column(Integer, default=0, nullable=False)
    base_losses = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    # 블루전 매치 참가 이력
    participants = relationship(
        "BlueWarParticipant",
        back_populates="user",
        cascade="all, delete-orphan",
    )



class MemberUser(Base):
    """
    일반 회원(조회용) 계정 테이블.

    - 디스코드 OAuth 없이도 "디스코드 ID + 개인 비밀번호"로 가입/로그인 가능하게 한다.
    - 블루전 복기/매치 목록 같은 '조회' 기능 접근용.
    - 관리자(AdminUser/settings.ADMIN_USERS)와는 권한이 분리된다.
    """
    __tablename__ = "member_users"

    id = Column(Integer, primary_key=True, index=True)

    # 디스코드 ID (snowflake)
    discord_id = Column(String(32), unique=True, nullable=False)

    # 사이트에서 표시할 닉네임
    nickname = Column(String(100), nullable=False)

    # 비밀번호 해시 (절대 평문 저장 금지)
    password_hash = Column(String(255), nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)

    # 관리자 권한 (관리 페이지 접근)
    is_admin = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)


class BlueWarMatch(Base):
    """
    블루전 한 판의 정보.
    - /api 쪽에서 디스코드 봇이 전적을 보내면 여기 한 줄 생성.
    """
    __tablename__ = "bluewar_matches"

    id = Column(Integer, primary_key=True, index=True)

    # pvp / practice 등
    mode = Column(String(20), nullable=False)

    # finished / aborted 같은 상태
    status = Column(String(20), nullable=False, default="finished")

    starter_discord_id = Column(String(32), nullable=False)
    winner_discord_id = Column(String(32), nullable=True)
    loser_discord_id = Column(String(32), nullable=True)

    # 승차(필요할 때만 사용)
    win_gap = Column(Integer, nullable=True)

    # 총 사용된 단어 수
    total_rounds = Column(Integer, nullable=True)

    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=False)

    # 자유 메모 (game_no, reason 등)
    note = Column(Text, nullable=True)

    # 🔵 여기 새로 추가된 부분: 단어 복기 로그 전체
    #    예: "블루아카이브 → 브로콜리 → ..."
    review_log = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    participants = relationship(
        "BlueWarParticipant",
        back_populates="match",
        cascade="all, delete-orphan",
    )


class BlueWarParticipant(Base):
    """
    블루전 매치 참가자 정보 (사람/봇 통합).
    """
    __tablename__ = "bluewar_participants"

    id = Column(Integer, primary_key=True, index=True)

    match_id = Column(Integer, ForeignKey("bluewar_matches.id"), nullable=False)
    match = relationship("BlueWarMatch", back_populates="participants")

    # 실제 유저 테이블과도 연결 (있으면)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    user = relationship("User", back_populates="participants")

    # 디스코드 ID (사람이든 봇이든)
    discord_id = Column(String(32), nullable=True)

    # 표시 이름 (디스코드 닉네임 등)
    name = Column(String(100), nullable=True)

    # AI 이름 (연습 모드에서 '유메')
    ai_name = Column(String(50), nullable=True)

    # 1 / 2 같은 사이드 번호
    side = Column(Integer, nullable=False)

    # 승리 여부
    is_winner = Column(Boolean, default=False, nullable=False)

    # 점수 / 턴 수 등 추가 지표 (필요 시)
    score = Column(Integer, nullable=True)
    turns = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class AppMeta(Base):
    """앱 내부 메타데이터(단발성 마이그레이션/시드 적용 여부 등)."""
    __tablename__ = "app_meta"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

