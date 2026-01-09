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

        # 디스코드 봇 전적 업로드/랭킹 조회 API 토큰 (선택)
        # - 지정 시: X-API-Token 헤더로 검증한다.
        # - 미지정 시: 개발 편의를 위해 API를 오픈 상태로 둔다.
        _api = (
            os.getenv("YUME_API_TOKEN")
            or os.getenv("YUME_ADMIN_API_TOKEN")
            or os.getenv("SHIHO_ADMIN_API_TOKEN")
            or os.getenv("ADMIN_API_TOKEN")
            or os.getenv("API_TOKEN")
        )
        self.API_TOKEN = (_api.strip() if isinstance(_api, str) else "") or None


        # 월별(버전) 단어 DB 팩 저장 경로 (선택)
        # - 미지정 시: <project_root>/data/wordlists/bluewar
        # - 지정 시: 절대/상대 경로 모두 허용(상대는 project_root 기준)
        self.WORDLIST_PACKS_DIR: str = os.getenv("YUME_WORDLIST_PACKS_DIR", "").strip()

        # 월별 단어 DB: 항상 최대 N개만 유지 (기본 3)
        try:
            self.WORDLIST_PACKS_MAX_KEEP: int = int(os.getenv("YUME_WORDLIST_PACKS_MAX_KEEP", "3"))
        except Exception:
            self.WORDLIST_PACKS_MAX_KEEP = 3
        if self.WORDLIST_PACKS_MAX_KEEP < 1:
            self.WORDLIST_PACKS_MAX_KEEP = 3

        
        # 관리자 페이지 '월별 팩 업로드' 탭에 노출할 버전 목록 (쉼표 구분)
        # 예: 2025-10,2025-12,2026-01
        self.WORDLIST_PACKS_TABS: str = os.getenv("YUME_WORDLIST_PACKS_TABS", "").strip()

# 기본(legacy) 경로로 내려줄 디폴트 버전 (선택)
        # - 지정하지 않으면: default_version.txt -> 최신 버전 순
        self.WORDLIST_PACKS_DEFAULT: str = os.getenv("YUME_WORDLIST_PACKS_DEFAULT", "").strip()

        # 블루전(루트전) 분석 캐시 저장 경로 (선택)
        # - 미지정 시: <project_root>/data/bluewar_analysis_cache
        # - data/ 디렉토리는 yumeweb deploy 파이프라인에서 safe-exclude 되므로
        #   서버에서 캐시가 유지된다.
        self.BLUEWAR_ANALYSIS_CACHE_DIR: str = os.getenv("YUME_BLUEWAR_ANALYSIS_CACHE_DIR", "").strip()


# FastAPI 전체에서 쓰는 전역 설정 인스턴스
settings = Settings()
