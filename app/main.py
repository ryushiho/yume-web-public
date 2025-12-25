# app/main.py

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from app.routers import auth, dashboard, records, users, api_bluewar, ranking, bluewar, home, member, admin_members
from app.database import Base, engine
from app.database import SessionLocal
from app.schema import ensure_sqlite_schema
from app.seed_import import ensure_blue_records_seed
from app import models
from app.security import hash_password

# 🔵 여기서 한 번 모든 모델 기반으로 테이블 생성
# - 이미 있는 테이블은 건드리지 않고
# - 없는 테이블만 새로 만든다 (데이터는 그대로 유지)
Base.metadata.create_all(bind=engine)
ensure_sqlite_schema(engine)

app = FastAPI(
    title="Yume Admin",
    docs_url=None,
    redoc_url=None,
)

# 세션 미들웨어 (로그인 상태용)
# - public repo에서는 하드코딩을 피하기 위해 env 기반으로 설정한다.
app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET)

# 정적 파일 (CSS, JS)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# 라우터 등록
app.include_router(home.router)
app.include_router(auth.router)
app.include_router(member.router)
app.include_router(dashboard.router)
app.include_router(records.router)
app.include_router(bluewar.router)
app.include_router(users.router)
app.include_router(api_bluewar.router)
app.include_router(ranking.router)
app.include_router(admin_members.router)



@app.on_event("startup")
def _startup_seed_import() -> None:
    # 시드 전적(blue_records.json)을 1회 반영 + 부트스트랩 관리자 지정
    db = SessionLocal()
    try:
        ensure_blue_records_seed(db)

        # ✅ 요청사항: 멤버 로그인 아이디를 "디스코드 ID" 강제에서 해제하고,
        #    관리자 계정 1개만 유지 (ID: 시호, PW: miyo) - 1회성 부트스트랩
        bootstrap_key = "member_bootstrap_admin_v1"
        meta = db.query(models.AppMeta).filter(models.AppMeta.key == bootstrap_key).first()
        if not meta:
            # 기존 계정 전부 정리
            db.query(models.MemberUser).delete()
            db.commit()

            # 관리자 1개 생성
            admin_id = "시호"
            admin_pw = "miyo"
            m = models.MemberUser(
                discord_id=admin_id,
                nickname=admin_id,
                password_hash=hash_password(admin_pw),
                is_active=True,
                is_admin=True,
            )
            db.add(m)
            db.add(models.AppMeta(key=bootstrap_key, value="done"))
            db.commit()
    finally:
        db.close()
