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


# FastAPI 전체에서 쓰는 전역 설정 인스턴스
settings = Settings()
