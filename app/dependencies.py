# app/dependencies.py

from typing import Generator, Optional, Dict, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """
    요청마다 DB 세션을 하나 열고, 응답 후에 닫아주는 의존성.
    모든 라우터에서 Depends(get_db)로 사용.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_admin_user(
    request: Request,
) -> Dict[str, Any]:
    """
    로그인된 관리자(세션 기반) 가져오기.

    - auth.py 에서 로그인 성공 시:
        request.session["user"] = {"id": username}
      이런 형태로 저장하고 있으므로, 여기서 그대로 꺼내 쓴다.
    - 로그인 안 되어 있으면 /auth/login 으로 리다이렉트.
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/auth/login"},
        )

    return user


async def get_optional_admin_user(
    request: Request,
) -> Optional[Dict[str, Any]]:
    """
    로그인 여부가 선택적인 경우 사용할 수 있는 버전.
    - 로그인 안 되어 있으면 None
    - 되어 있으면 {"id": username} 형태의 딕셔너리 반환
    """
    return request.session.get("user")
