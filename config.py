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

        # 유메 초대 링크 (홈 화면에서 사용)
        self.INVITE_URL = os.getenv("YUME_INVITE_URL", "#")

        # 관리자 계정 (ID -> PW)
        # (기존 방식: 관리자 로그인 페이지 /auth/login)

        # 공개 레포 기본값은 무조건 플레이스홀더로 둔다.
        # 운영 비번은 .env 에서 YUME_ADMIN_*_PW 로 주입할 것.
        # 요청사항: 관리자 1명만 유지
        #   ID: 시호
        #   PW: miyo
        self.ADMIN_USERS: Dict[str, str] = {"시호": "miyo"}

        # 회원(MemberUser) 계정 중 "관리 페이지 접근" 권한을 부여할 로그인 아이디 목록(쉼표 구분)
        #
        # ✅ 변경 이유:
        # - 회원 가입이 "아이디/비밀번호" 방식인데,
        #   관리자 권한 부여 기준이 '디스코드 ID'로 되어 있으면 운영이 헷갈린다.
        # - 그래서 이제는 "회원 로그인 아이디"(member register/login에서 입력하는 아이디) 기준으로 부여한다.
        #
        # 호환성:
        # - 기존 .env의 YUME_ADMIN_DISCORD_IDS / YUME_BOOTSTRAP_ADMIN_DISCORD_ID 도 그대로 읽는다.
        raw_ids = (os.getenv("YUME_ADMIN_MEMBER_IDS") or os.getenv("YUME_ADMIN_DISCORD_IDS") or "").strip()
        self.ADMIN_MEMBER_IDS = {x.strip() for x in raw_ids.split(",") if x.strip()}

        # 부트스트랩(초기 1인) 관리자 로그인 아이디(선택)
        self.BOOTSTRAP_ADMIN_MEMBER_ID = (
            os.getenv("YUME_BOOTSTRAP_ADMIN_MEMBER_ID")
            or os.getenv("YUME_BOOTSTRAP_ADMIN_DISCORD_ID")
            or ""
        ).strip()


# 전역 settings 인스턴스
settings = Settings()
