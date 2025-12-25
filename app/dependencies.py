# app/dependencies.py

from __future__ import annotations

from typing import Any, Dict, Generator, Optional

from fastapi import HTTPException, Request, status
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


async def get_current_admin_user(request: Request) -> Dict[str, Any]:
    """관리자 권한 확인.

    - (구) /auth/login 세션: request.session["user"]
    - (신) 회원 로그인 세션: request.session["member"]["is_admin"] == True
    """
    user = request.session.get("user")
    if user:
        return user

    member = request.session.get("member") or {}
    if member and member.get("is_admin"):
        # 관리자도 기존 템플릿/라우터 호환을 위해 {"id": "..."} 형태 비슷하게 맞춘다.
        return {"id": member.get("id"), "member_id": member.get("member_id"), "nickname": member.get("nickname")}

    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": "/member/login"},
    )



async def get_optional_admin_user(request: Request) -> Optional[Dict[str, Any]]:
    """
    로그인 여부가 선택적인 경우 사용할 수 있는 버전.
    - 로그인 안 되어 있으면 None
    - 되어 있으면 {"id": username} 형태의 딕셔너리 반환
    """
    return request.session.get("user")


async def get_current_member_user(request: Request) -> Dict[str, Any]:
    """
    로그인된 일반 회원(세션 기반) 가져오기.

    - member.py 로그인 성공 시:
        request.session["member"] = {"id": discord_id, "nickname": "...", "member_id": 1}
      형태로 저장한다.
    - 로그인 안 되어 있으면 /member/login 으로 리다이렉트.
    """
    member = request.session.get("member")
    if not member:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/member/login"},
        )
    return member


async def get_optional_member_user(request: Request) -> Optional[Dict[str, Any]]:
    return request.session.get("member")


async def get_current_member_or_admin(request: Request) -> Dict[str, Any]:
    """
    '조회 페이지'에서 사용.
    - admin이면 {"role":"admin", ...}
    - member면 {"role":"member", ...}
    - 둘 다 아니면 member 로그인으로 보냄
    """
    admin = request.session.get("user")
    if admin:
        out = dict(admin)
        out["role"] = "admin"
        return out

    member = request.session.get("member")
    if member:
        out = dict(member)
        out["role"] = "member"
        return out

    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": "/member/login"},
    )
