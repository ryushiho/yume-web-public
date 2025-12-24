# app/main.py

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from app.routers import auth, dashboard, records, users, api_bluewar, ranking
from app.database import Base, engine

# 🔵 여기서 한 번 모든 모델 기반으로 테이블 생성
# - 이미 있는 테이블은 건드리지 않고
# - 없는 테이블만 새로 만든다 (데이터는 그대로 유지)
Base.metadata.create_all(bind=engine)

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
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(records.router)
app.include_router(users.router)
app.include_router(api_bluewar.router)
app.include_router(ranking.router)


# 루트 페이지 -> 대시보드로 리다이렉트
@app.get("/", include_in_schema=False)
async def root():
    # 로그인 안 돼 있으면 /dashboard에서 다시 로그인 페이지로 튕길 거라
    # 여기서는 그냥 대시보드로 보내기만 하면 됨.
    return RedirectResponse(url="/dashboard/")
