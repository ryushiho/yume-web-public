# app/database.py

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# ============================================
# 1) DB 경로
#    - /opt/yume-web/yume_admin.db 를 사용하도록 절대 경로로 고정
#    - 실제 파일명이 다르면 여기만 바꿔주면 됨.
# ============================================
SQLALCHEMY_DATABASE_URL = "sqlite:////opt/yume-web/yume_admin.db"

# SQLite인 경우 check_same_thread 옵션 필요
connect_args = {}
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """
    FastAPI 의 Depends(get_db) 에서 쓰는 세션 의존성.
    요청마다 DB 세션 하나 열고, 끝나면 닫아준다.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
