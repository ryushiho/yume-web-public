# 블루전(루트전) 그래프 기반 판정 엔진 계약서 (Phase 0)

이 문서는 **웹(서버) 분석 엔진**, **봇(Shiho/Yume) 게임 로직**, **관리자 UI**가 동일한 의미로 "필킬/필패/중립"을 해석하도록 고정하는 **데이터 계약(Data Contract)** 입니다.

> 핵심: 이 분석은 "단어"가 아니라 **음절(현재 턴의 시작 글자)** 을 상태 노드로 두고, 단어를 "이동(간선)"으로 보는 **그래프 게임**입니다.

---

## 1) 용어 정의

- **음절(노드, syllable)**: 현재 턴에 플레이어가 시작해야 하는 글자 1개
- **단어(간선, word move)**: `(시작음절 -> 끝음절)` 로 이동시키는 행동
- **두음(두음법칙, dooum)**: "현재 음절"에서 다음 단어의 "시작음절" 후보를 확장하는 규칙

### 노드 상태(NodeType)
- `WIN` : **현재 턴 플레이어가 최선 플레이를 하면** 승리를 강제할 수 있는 음절
- `LOSE`: **현재 턴 플레이어가 무엇을 해도** 패배가 확정되는 음절
- `DRAW`: 승/패가 역전파로 결정되지 않는 영역(순환 가능)

> 주의: 여기서 WIN/LOSE는 "그 음절로 시작해야 하는 사람" 기준입니다.

---

## 2) 두음법칙(필수 규칙, Shiho 호환)

웹 분석은 **Shiho 봇과 100% 동일한 두음 처리 방식**을 사용합니다.

- 적용 함수: `app/bluewar/dooum.py::allowed_first_chars(last_char)`
- 규칙 파일: `app/bluewar/dooum_rules.txt`
- 기본 맵: `DEFAULT_DOOUM_MAP` + 파일 규칙을 merge

### 적용 방식(중요)
- **단방향(one-way)만 허용**
  - 예) `려 -> 여` 는 허용
  - 예) `여 -> 려` 는 **불허**
- "완전형 자동 확장" 같은 추가 추론은 **하지 않음**
  - (추론을 과하게 하면 그래프가 과연결되어 WIN/LOSE가 DRAW로 무너질 수 있음)

### 그래프 생성 시 두음 적용 위치
- 현재 노드가 `u` 일 때, 다음 단어의 시작음절 후보는 `allowed_first_chars(u)` 입니다.
- 따라서 간선 생성은 다음 의미를 갖습니다.
  - `u`에서 시작 가능한 단어 = `first(word) in allowed_first_chars(u)`

---

## 3) 입력 데이터 계약

분석 입력은 다음 중 하나에서 공급될 수 있습니다.

1) **월별 dict pack(운영 기본)**
- `WORDLIST_PACKS_DIR/<version>/blue_archive_words.txt`
- `WORDLIST_PACKS_DIR/<version>/suggestion.txt`
- 등

2) **DB(웹에서 관리하는 리스트)**
- `bluewar_words (list_name, word)`

분석 함수는 `prepare_input()`에서 입력을 결정합니다.

- `list_name`: 분석 대상 리스트 이름
  - 기본: `blue_archive_words`
- `pack_version`: 특정 월 버전 지정(없으면 기본 버전)

### 정규화 규칙
- 공백 포함 단어는 제외
- 중복 단어는 제거
- 1줄 1단어

---

## 4) 결과/캐시 키 계약(재현성)

동일한 입력이면 결과가 반드시 동일해야 하므로, 분석은 **analysis_key**로 동일성 판단을 합니다.

- `words_sha256`: 분석에 사용된 단어 목록(txt)의 SHA256
- `dooum_sha256`: 두음 규칙 전체(기본+파일+모드)의 SHA256 (`dooum_signature()`)
- `algo_version`: 알고리즘 버전 문자열 (`ALGO_VERSION`)

`analysis_key`는 위 3개(+list_name/pack_version)를 묶어 SHA256으로 생성합니다.

> 두음 규칙이 조금이라도 바뀌면 `dooum_sha256`이 바뀌어야 하며, 따라서 `analysis_key`도 바뀝니다.

---

## 5) 저장 스키마(결과 테이블)

- `bluewar_analysis_meta`
  - analysis_key, list_name, pack_version, words_sha256, dooum_sha256, algo_version, created_at

- `bluewar_syllable_stats`
  - (analysis_key, syllable) unique
  - node_type(WIN/LOSE/DRAW)
  - out_moves / in_moves
  - win_moves / lose_moves / draw_moves
  - sample_win_words(JSON)

- `bluewar_word_stats`
  - (analysis_key, word) unique
  - start_syllable / end_syllable
  - node_type(WIN/LOSE/DRAW)  ← 아래 "단어 상태 해석" 참고

---

## 6) 단어 상태 해석(필킬/필패/중립)

음절 상태가 결정되면 단어 상태는 **끝음절 상태로부터 파생**됩니다.

- 단어 `w`의 끝음절이 `LOSE`이면
  - 그 단어를 쓰면 상대가 `LOSE`에서 시작해야 하므로
  - `w`는 `WIN` (실질적으로 **필킬 단어**)

- 단어 `w`의 끝음절이 `WIN`이면
  - `w`는 `LOSE` (실질적으로 **필패 단어**)

- 끝음절이 `DRAW`이면
  - `w`는 `DRAW` (실질적으로 **중립 단어**)

---

## 7) 증명(트리거 경로) 계약

관리자 UI에서 "왜 WIN/LOSE/DRAW인가"를 보여주기 위해, 서버는 **대표 경로(line)** 를 생성할 수 있어야 합니다.

- WIN 음절: `win_witness[u] = (word, next)`
  - next는 반드시 LOSE
- LOSE 음절: `lose_best[u] = (word, next)`
  - 패배를 최대한 늦추는(= mate_dist 최대) next 선택
- DRAW 음절: 짧은 순환(cycle) 샘플 탐색(깊이 제한)

경로는 운영/표시를 위해 길이를 제한합니다(기본 10).

---

## 8) 변경 시 규칙(절대 깨지면 안 되는 것)

다음이 바뀌면 결과가 달라질 수 있으므로, 반드시 **버전/해시가 바뀌어야** 합니다.

- 단어 리스트 내용(추가/삭제/수정)
- 두음 규칙(기본 맵/파일/적용 모드)
- 알고리즘(역전파 규칙/타이브레이커/샘플 경로 생성 방식)

