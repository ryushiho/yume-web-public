# app/main.py

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from config import settings
from app.routers import auth, dashboard, users, api_bluewar, ranking, bluewar, home, member, admin_members
from app.routers import admin_wordlists
from app.routers import api_wordlists
from app.routers import words
from app.database import Base, engine
from app.database import SessionLocal
from app.schema import ensure_sqlite_schema
from app.seed_import import ensure_blue_records_seed
from app.seed_wordlists import seed_wordlist_snapshots
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


# ============================
#   로그인 전 UI 잠금
# ============================
#
# 운영 요구사항:
# - 로그인하지 않은 사용자는 "로그인 화면" 이외의 UI를 보지 못하게 한다.
# - 단, 디스코드 봇 연동(/bluewar/*) 및 단어장 API(/api/*)는 세션이 없어도
#   토큰/권한으로 동작해야 하므로 여기서 막지 않는다.


@app.middleware("http")
async def require_login_for_ui(request: Request, call_next):
    path = request.url.path or "/"

    # 1) 정적/공개 API/봇 연동은 예외
    if (
        path.startswith("/static")
        or path.startswith("/api")
        or path.startswith("/bluewar")
        or path.startswith("/favicon")
        or path.startswith("/robots.txt")
    ):
        return await call_next(request)

    # 2) 로그인/회원가입/관리자 로그인 화면은 예외
    if (
        path.startswith("/member/login")
        or path.startswith("/member/register")
        or path.startswith("/auth/login")
        or path.startswith("/member/logout")
        or path.startswith("/auth/logout")
    ):
        return await call_next(request)

    # 3) 그 외는 로그인 강제
    #    ⚠️ SessionMiddleware가 아직 실행되기 전에는 request.session 속성이 없을 수 있다.
    #    그래서 scope의 session을 안전하게 읽는다.
    session = getattr(request, "session", {}) or {}
    if not session.get("user") and not session.get("member"):
        return RedirectResponse(url="/member/login", status_code=303)

    return await call_next(request)


# 세션 미들웨어 (로그인 상태용)
# - public repo에서는 하드코딩을 피하기 위해 env 기반으로 설정한다.
# ⚠️ require_login_for_ui 미들웨어가 세션을 읽을 수 있도록 SessionMiddleware를 *나중에* 등록한다.
app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET)

# 정적 파일 (CSS, JS)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# 라우터 등록
app.include_router(home.router)
app.include_router(auth.router)
app.include_router(member.router)
app.include_router(dashboard.router)
app.include_router(bluewar.router)
app.include_router(users.router)
app.include_router(api_bluewar.router)
app.include_router(api_wordlists.router)
app.include_router(ranking.router)
app.include_router(words.router)
app.include_router(admin_members.router)
app.include_router(admin_wordlists.router)



@app.on_event("startup")
def _startup_seed_import() -> None:
    # 시드 전적(blue_records.json)을 1회 반영 + 부트스트랩 관리자 지정
    db = SessionLocal()
    try:
        ensure_blue_records_seed(db)

        # Phase 6: 기존 wordlist 데이터를 최초 1회 스냅샷으로 고정(버전/롤백용)
        seed_wordlist_snapshots()

        # ✅ 회원 관리자 권한 부트스트랩(안전한 방식)
        # - 더 이상 회원 테이블을 통째로 삭제하지 않는다.
        # - (선택) .env의 YUME_BOOTSTRAP_ADMIN_MEMBER_ID 로 지정된 '회원 아이디'가 있으면
        #   그 계정을 관리자(is_admin=True)로 올려 준다.
        # - 아무 계정도 없을 때만, 최소 1개의 초기 관리자(시호/miyo)를 생성한다.

        # 1) 부트스트랩 아이디가 지정된 경우: 해당 회원을 admin으로 승격(존재하면)
        bootstrap_id = getattr(settings, "BOOTSTRAP_ADMIN_MEMBER_ID", "").strip()
        if bootstrap_id:
            target = (
                db.query(models.MemberUser)
                .filter(models.MemberUser.discord_id == bootstrap_id)
                .first()
            )
            if target and not target.is_admin:
                target.is_admin = True
                db.add(target)
                db.commit()

        # 2) 회원이 아예 없는 경우: 초기 관리자 1개만 생성(운영에서 바로 교체 권장)
        any_member = db.query(models.MemberUser).first()
        if not any_member:
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
            db.commit()
    finally:
        db.close()
