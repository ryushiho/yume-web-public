# config.py
# Yume Admin 공용 설정

import os
from typing import Dict


class Settings:
    """
    간단한 설정 컨테이너.

    - SESSION_SECRET : FastAPI 세션 쿠키 암호화용 시크릿
    - ADMIN_USERS    : 관리자 로그인 ID / PW 목록
    - API_TOKEN      : 디스코드 봇이 전적 업로드할 때 쓰는 토큰 (X-API-Token)
    """

    def __init__(self) -> None:
        # 세션 시크릿 (운영에서는 .env 로 반드시 지정)
        self.SESSION_SECRET: str = os.getenv(
            "YUME_SESSION_SECRET",
            "yume-admin-session-secret-change-me",
        )

        # 블루전 전적 업로드 API 토큰
        # - 디스코드 봇에서 X-API-Token 헤더로 보내면 된다.
        self.API_TOKEN = os.getenv("YUME_API_TOKEN") or os.getenv("YUME_ADMIN_API_TOKEN")

        # 관리자 계정 (ID -> PW)
        # 공개 레포 기본값은 무조건 플레이스홀더로 둔다.
        # 운영 비번은 .env 에서 YUME_ADMIN_*_PW 로 주입할 것.
        self.ADMIN_USERS: Dict[str, str] = {
            "시호": os.getenv("YUME_ADMIN_SIHO_PW", "change-me"),
            "메조": os.getenv("YUME_ADMIN_MEZO_PW", "change-me"),
        }


# 전역 settings 인스턴스
settings = Settings()
