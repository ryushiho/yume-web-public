# app/models.py

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Float,
    Text,
    UniqueConstraint,
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

    # 어떤 봇/앱에서 올라온 전적인지 (예: "shiho")
    source_app = Column(String(32), nullable=False, default="shiho")

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

    # 어떤 봇/앱에서 올라온 전적인지 (예: "shiho", "yume")
    # - UI 필터/표시용
    # - 실제 DB 컬럼은 app/schema.py의 ensure_sqlite_schema(engine)로 자동 보정된다.
    source_app = Column(String(32), nullable=False, default="shiho")

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


class BlueWarWord(Base):
    """블루전 단어 리스트(웹에서 관리).

    - list_name: "suggestion" | "blue_archive_words" (파일명 기준)
    - word: 단어(1줄 1단어)

    Phase 1에서는 읽기 API(/api/bluewar/wordlists/*.txt) 제공을 위해 도입.
    이후 Phase 2~에서 업로드/CRUD UI로 확장.
    """

    __tablename__ = "bluewar_words"
    __table_args__ = (
        UniqueConstraint("list_name", "word", name="uq_bluewar_words_list_word"),
    )

    id = Column(Integer, primary_key=True, index=True)
    list_name = Column(String(50), nullable=False, index=True)
    word = Column(String(200), nullable=False, index=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BlueWarWordListSnapshot(Base):
    """블루전 단어 리스트 버전 스냅샷(Phase 6).

    - 업로드/추가/수정/삭제/롤백 등 "변경"이 발생할 때마다 현재 리스트 전체를 스냅샷으로 저장한다.
    - 운영 실수(잘못 업로드 등) 시 특정 버전으로 롤백할 수 있다.
    """

    __tablename__ = "bluewar_wordlist_snapshots"
    __table_args__ = (
        UniqueConstraint("list_name", "version", name="uq_bluewar_wordlist_snapshots_list_version"),
    )

    id = Column(Integer, primary_key=True, index=True)

    # "suggestion" | "blue_archive_words"
    list_name = Column(String(50), nullable=False, index=True)

    # 리스트별 버전(1부터 증가)
    version = Column(Integer, nullable=False, index=True)

    # 스냅샷 시점의 리스트 정보
    sha256 = Column(String(64), nullable=False)
    count = Column(Integer, nullable=False, default=0)

    # txt 원문(마지막에 개행 포함). 롤백 시 이 값을 그대로 적용한다.
    content_text = Column(Text, nullable=False, default="")

    # upload/add/edit/delete/bulk_delete/rollback/bootstrap 등
    action = Column(String(30), nullable=False, default="unknown")

    # 누가(어떤 관리자) 변경했는지
    created_by = Column(String(100), nullable=True)
    created_by_name = Column(String(100), nullable=True)

    # 보조 메모(예: rollback to v3)
    note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)



# ============================
# BlueWar Analysis (syllable/word win-lose classification)
# ============================


class BlueWarAnalysisMeta(Base):
    """분석 메타(캐시 키/버전/해시).

    - analysis_key: words_sha + dooum_sha + algo_version 기반
    - list_name: 어떤 리스트를 분석했는지 (blue_archive_words / suggestion / public_words)
    - pack_version: 월별 dict pack 버전(YYYY-MM). DB 기반 분석이면 NULL
    """

    __tablename__ = "bluewar_analysis_meta"

    id = Column(Integer, primary_key=True, index=True)
    analysis_key = Column(String(64), nullable=False, index=True)
    list_name = Column(String(50), nullable=False, index=True)
    pack_version = Column(String(16), nullable=True, index=True)

    words_sha256 = Column(String(64), nullable=False)
    word_count = Column(Integer, nullable=False, default=0)
    dooum_sha256 = Column(String(64), nullable=False)
    algo_version = Column(String(64), nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class BlueWarSyllableStat(Base):
    """음절(노드) 단위 승패 분류 결과."""

    __tablename__ = "bluewar_syllable_stats"
    __table_args__ = (
        UniqueConstraint("analysis_key", "syllable", name="uq_bluewar_syllable_stats_key_syl"),
    )

    id = Column(Integer, primary_key=True, index=True)
    analysis_key = Column(String(64), nullable=False, index=True)

    syllable = Column(String(10), nullable=False, index=True)
    node_type = Column(String(10), nullable=False, index=True)  # WIN/LOSE/DRAW

    out_moves = Column(Integer, nullable=False, default=0)
    in_moves = Column(Integer, nullable=False, default=0)

    win_moves = Column(Integer, nullable=False, default=0)
    lose_moves = Column(Integer, nullable=False, default=0)
    draw_moves = Column(Integer, nullable=False, default=0)

    # JSON list of sample words that send the opponent to a LOSE syllable
    sample_win_words = Column(Text, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)


class BlueWarWordStat(Base):
    """단어 단위 승패 분류 결과."""

    __tablename__ = "bluewar_word_stats"
    __table_args__ = (
        UniqueConstraint("analysis_key", "word", name="uq_bluewar_word_stats_key_word"),
    )

    id = Column(Integer, primary_key=True, index=True)
    analysis_key = Column(String(64), nullable=False, index=True)

    word = Column(String(200), nullable=False, index=True)
    start_syllable = Column(String(10), nullable=True, index=True)
    end_syllable = Column(String(10), nullable=True, index=True)
    node_type = Column(String(10), nullable=False, index=True)  # WIN/LOSE/DRAW

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)



# ============================
# Abydos Mini-game (Aby) tables
# - Bot -> Web sync payload
# ============================


class AbyGuildState(Base):
    __tablename__ = "aby_guild_states"

    id = Column(Integer, primary_key=True, index=True)

    # Discord guild id (string for safety / future migration)
    guild_id = Column(String(30), unique=True, index=True, nullable=False)
    guild_name = Column(String(120), nullable=True)

    debt = Column(Integer, nullable=False, default=0)
    interest_rate = Column(Float, nullable=False, default=0.0)
    last_interest_ymd = Column(String(20), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)


class AbyUserEconomy(Base):
    __tablename__ = "aby_user_economy"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", name="uq_aby_user_economy_guild_user"),
    )

    id = Column(Integer, primary_key=True, index=True)

    guild_id = Column(String(30), index=True, nullable=False)
    user_id = Column(String(30), index=True, nullable=False)
    nickname = Column(String(120), nullable=True)

    credits = Column(Integer, nullable=False, default=0)
    water = Column(Integer, nullable=False, default=0)
    last_explore_ymd = Column(String(20), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)


class AbyExploreLog(Base):
    __tablename__ = "aby_explore_logs"
    __table_args__ = (
        UniqueConstraint("guild_id", "source_id", name="uq_aby_explore_logs_guild_source"),
    )

    id = Column(Integer, primary_key=True, index=True)

    guild_id = Column(String(30), index=True, nullable=False)
    source_id = Column(String(80), index=True, nullable=False)  # stable id from bot (or hash)
    user_id = Column(String(30), index=True, nullable=True)
    nickname = Column(String(120), nullable=True)

    date_ymd = Column(String(20), nullable=True)
    weather = Column(String(20), nullable=True)
    success = Column(Integer, nullable=False, default=0)

    delta_credits = Column(Integer, nullable=False, default=0)
    delta_water = Column(Integer, nullable=False, default=0)

    summary = Column(Text, nullable=True)
    raw_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class AbyIncidentLog(Base):
    __tablename__ = "aby_incident_logs"
    __table_args__ = (
        UniqueConstraint("guild_id", "source_id", name="uq_aby_incident_logs_guild_source"),
    )

    id = Column(Integer, primary_key=True, index=True)

    guild_id = Column(String(30), index=True, nullable=False)
    source_id = Column(String(80), index=True, nullable=False)

    kind = Column(String(40), nullable=False, default="incident")
    title = Column(String(120), nullable=False, default="")
    description = Column(Text, nullable=False, default="")
    delta_debt = Column(Integer, nullable=False, default=0)

    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class AbyWeeklySummary(Base):
    __tablename__ = "aby_weekly_summaries"
    __table_args__ = (
        UniqueConstraint("guild_id", "week_key", name="uq_aby_weekly_summaries_guild_week"),
    )

    id = Column(Integer, primary_key=True, index=True)

    guild_id = Column(String(30), index=True, nullable=False)
    week_key = Column(String(20), index=True, nullable=False)

    debt_summary_json = Column(Text, nullable=True)
    points_ranking_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
