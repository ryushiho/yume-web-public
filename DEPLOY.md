# DEPLOY.md — Yume Admin (yume-web-public)

이 문서는 **서버(/opt/yume-web)에서 운영 중인 Yume Admin(FastAPI)** 배포/운영 규칙을 한 장으로 고정해두기 위한 메모다.
새 채팅/새 환경에서 “뭐가 어디에 있지?”를 방지하는 게 목적.

---

## 1) 서비스 개요

- 프로젝트: Yume Admin (FastAPI)
- 서버 경로: `/opt/yume-web`
- systemd 서비스: `yume-admin.service`
- uvicorn 바인딩: `127.0.0.1:8001`
- 외부 도메인: `shihonoyume.xyz` / `www.shihonoyume.xyz`
- 리버스 프록시: Nginx (HTTPS, Let’s Encrypt)

---

## 2) 서버 구성(운영 기준)

### (A) systemd
- 서비스명: `yume-admin.service`
- 실행 형태(예시):
  - `/opt/yume-web/venv/bin/python3`
  - `/opt/yume-web/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001`

상태/로그:
- `systemctl status yume-admin.service --no-pager`
- `journalctl -u yume-admin.service -n 120 --no-pager`

### (B) Nginx (TLS + Reverse Proxy)
- 사이트 파일(예시): `/etc/nginx/sites-available/shihonoyume` (enabled에 링크)
- 핵심:
  - `server_name shihonoyume.xyz www.shihonoyume.xyz;`
  - `proxy_pass http://127.0.0.1:8001;`
  - SSL은 certbot 관리

점검:
- `nginx -t`
- `sudo nginx -T | grep -n "server_name shihonoyume.xyz\\|proxy_pass\\|listen" | head -n 120`
- `systemctl reload nginx`

---

## 3) 환경변수(.env) 규칙

**운영 .env는 서버에만 존재해야 하며 레포에 커밋 금지.**
- 운영 파일 위치: `/opt/yume-web/.env`
- `.gitignore`로 반드시 제외

### 사용되는 주요 키
- `YUME_SESSION_SECRET`
  - FastAPI 세션 쿠키 암호화용
- `YUME_API_TOKEN` (또는 `YUME_ADMIN_API_TOKEN`)
  - 디스코드 봇이 전적 업로드 시 `X-API-Token` 헤더로 보내는 토큰
- `YUME_APP_SECRET_KEY`
  - app/config.py의 SECRET_KEY (공개 레포 기본값은 `change-me`)

---

## 4) API / 라우팅 핵심

- 전적 업로드 엔드포인트:
  - `POST /bluewar/matches`
  - 헤더: `X-API-Token: <YUME_API_TOKEN>`
- OpenAPI:
  - `/openapi.json`

주의:
- `POST /api/bluewar/matches` 는 **404**가 정상(해당 경로 없음).
- 루트 `/`는 FastAPI 기본 라우팅에 따라 `307/405` 같은 응답이 나올 수 있음(루트에 GET 핸들러가 없으면 405 가능).

---

## 5) 서버 배포 커맨드: yumeweb

서버에 `/usr/local/bin/yumeweb` 스크립트를 설치해 사용한다.

사용법:
- `yumeweb pull`     : git pull만
- `yumeweb restart`  : 서비스 재시작만
- `yumeweb deploy`   : git pull + restart + status + logs
- `yumeweb status`   : status 출력
- `yumeweb logs`     : 최근 120줄 로그

권장 배포 루틴:
1) 서버에서 코드 갱신이 필요하면:
   - `yumeweb deploy`
2) 단순 재시작만이면:
   - `yumeweb restart`

---

## 6) 레포에 절대 올리면 안 되는 것(민감/대용량)

아래는 레포 추적 금지:
- `.env` / `.env.*`
- `*.db` / `*.sqlite*`
- `venv/`
- `*.zip`

서버에서 점검:
- `git ls-files | egrep "\\.env$|\\.env\\.|\\.db$|\\.sqlite|^venv/|\\.zip$" && echo "!!! BAD" || echo "OK"`

---

## 7) 트러블슈팅 메모

### (A) 토큰 401 (Invalid API token)
- 서버 `.env`에 `YUME_API_TOKEN` 값이 들어있는지 확인
- 요청 헤더가 정확히 `X-API-Token`인지 확인
- uvicorn 재시작 후 반영 확인: `yumeweb restart`

### (B) 외부 접속이 이상함
- Nginx upstream이 `127.0.0.1:8001`로 되어있는지 확인
- `systemctl status yume-admin.service` 로 포트가 8001인지 확인
- `curl -I https://shihonoyume.xyz/` 로 응답 확인

---

## 8) 서버 위치 정보(참고)
- (운영 서버) `/opt/yume-web`
- (도메인) `shihonoyume.xyz`
