# app/config.py

"""
Yume Admin 공용 설정 모듈.

지금은 샘플/플레이스홀더만 둔다.
운영 환경에서는 반드시 환경변수로 주입할 것.
"""

import os


class Settings:
    def __init__(self) -> None:
        # 공개 레포 기본값은 change-me
        self.SECRET_KEY: str = os.getenv("YUME_APP_SECRET_KEY", "change-me")

        # 단어 리스트 원본(.txt) 보호용 토큰
        # - 공개 단어 보기(/words)는 누구나 가능
        # - 하지만 원본 TXT 다운로드는 막기 위해 토큰을 요구한다.
        # - 운영 환경에서는 /opt/yume-web/.env 에 반드시 넣어줄 것.
        #   예) YUME_WORDLIST_TOKEN="<랜덤 긴 문자열>"
        self.WORDLIST_TOKEN: str = os.getenv("YUME_WORDLIST_TOKEN", "")


# FastAPI 전체에서 쓰는 전역 설정 인스턴스
settings = Settings()
