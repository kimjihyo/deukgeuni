# MCP 서버

득근이를 MCP(Model Context Protocol)로 제공한다. MCP를 지원하는 채팅 앱(Claude, ChatGPT, Claude Code, Cursor …)에서 같은 기록·계산·지식을 쓴다.

```
Claude 앱 / ChatGPT / Claude Code ──MCP(HTTPS)──▶ python -m mcp_server serve ──▶ MongoDB (사용자별)
                                                    └ 지식: 이 레포의 AGENTS.md, skills/, docs/
```

- **생각은 연결한 앱의 모델이 한다.** 서버는 모델을 부르지 않는다 (LLM 비용 없음)
- **대화 기록은 남기지 않는다.** 이어갈 일은 사용자 문서(status.md)에 남긴다
- 텔레그램 봇과 **같은 사용자·같은 데이터**를 쓴다 (`tg:<텔레그램 ID>`)

## 제공하는 것

### 도구

| 도구 | 하는 일 | 표시 |
| --- | --- | --- |
| `get_coaching_state` | 오늘 날짜 + 프로필 + 상태 + 현재 블록 (새 사용자면 온보딩부터 하라는 안내 포함) | 읽기 |
| `get_link_code` | 다른 앱·텔레그램을 같은 계정에 연결할 일회용 코드 | 쓰기 |
| `read_knowledge` | skills/, docs/, AGENTS.md 읽기 (비우면 목록) | 읽기 |
| `get_doc` / `update_doc` | 사용자 문서 읽기 / 덮어쓰기 | 읽기 / 쓰기 |
| `find_exercise` / `add_exercise` | 운동 찾기 / 추가 | 읽기 / 쓰기 |
| `log_sets` | 한 종목의 세트들 기록 (세션 자동 생성, 계획→완료) | 쓰기 |
| `edit_set` | 세트 수정, 변경 전후 반환 | 쓰기 |
| `delete_set` | 세트 삭제, `confirm=true`일 때만 | 삭제 |
| `set_session` | 세션 계획·완료·건너뜀·메모 | 쓰기 |
| `get_session` | 그날 요약 + 세트별 e1RM | 읽기 |
| `exercise_history` | 종목별 최근 기록 | 읽기 |
| `e1rm` | e1RM 추이·현재 추정 | 읽기 |
| `prescribe` | 3대 처방: 리프트·횟수·RPE → 무게 (탑세트 기준 e1RM + 개인 보정, 근거 세트 표시) | 읽기 |
| `load_from_e1rm` | e1RM 숫자로 무게 계산 (보조운동 등) | 읽기 |
| `fatigue_check` | 피로 신호 판정 (같은 조건 세트끼리만) | 읽기 |
| `weekly_volume` | 근육별 주간 세트 | 읽기 |

도구마다 읽기 전용·파괴적 여부 표시(MCP tool annotations)가 있어서, 앱이 쓰기·삭제 전에 승인을 묻는다.

### 서버 안내문, 프롬프트, 리소스

- **서버 안내문**: 연결 시 전달되는 득근이 핵심 규칙 (`mcp_server/server.py`의 `INSTRUCTIONS`)
- **프롬프트**: `start`(득근이 시작: AGENTS.md 전체), `today`, `log`, `weekly_review`, `reschedule`
- **리소스**: `deukgeuni://knowledge/<경로>`(지식 문서 전부), `deukgeuni://me/state`(내 상태)

앱이 페르소나를 얼마나 따를지는 앱이 정한다. 확실하게 하려면 앱의 사용자 지정 지침(프로젝트 지침 등)에 한 줄 넣는다:
> 운동 얘기를 하면 너는 득근이다. 득근이 MCP의 지침과 도구를 따르고, 대화 시작 때 get_coaching_state를 먼저 호출한다.

## 인증

| 방식 | 쓰는 곳 | 얻는 법 |
| --- | --- | --- |
| **OAuth** (자동 등록 + PKCE) | Claude 앱 커스텀 커넥터, ChatGPT | 앱에서 연결 → 득근이 연결 화면에서 **새로 시작하기**(새 계정) 또는 **연결 코드**(기존 계정) |
| **개인 토큰** (고정 헤더) | Claude Code, Cursor, Claude 커넥터의 "Request headers" | `python -m mcp_server issue-token --telegram-id <ID> --name laptop` |

연결 코드 받기 (기존 계정을 다른 앱에 연결할 때):
- 텔레그램 봇에게 `/link`
- 이미 연결된 다른 앱의 득근이에게 "연결 코드 줘" (`get_link_code` 도구)
- 또는 서버에서 `python -m mcp_server issue-code --telegram-id <ID>`

반대로 다른 앱에서 먼저 시작한 사람은 그 앱에서 받은 코드로 텔레그램 봇에 `/connect 코드`.
"새로 시작하기"는 `SIGNUP_MODE=open`일 때만 보이고, 같은 IP에서 한 시간에 5개까지 만들 수 있다.

코드는 15분, 한 번만 쓸 수 있다. 로그인 화면에서 5번 틀리면 그 연결 요청은 닫힌다.
토큰은 해시만 저장한다. 접근 토큰 1시간, 갱신 토큰 60일(쓸 때마다 교체).
해제: `python -m mcp_server revoke --telegram-id <ID> [--name laptop]`

## 앱에 연결하기

서버 주소: `https://<봇 도메인>/mcp`

- **Claude 앱**: 커넥터 설정에서 커스텀 커넥터 추가 → 서버 URL 입력 → 인증은 OAuth(자동 등록) → 연결 시 뜨는 득근이 화면에 연결 코드
- **ChatGPT**: 플러그인(또는 앱) 설정에서 사용자 MCP 서버 추가 → 서버 URL → 인증 OAuth → 연결 코드
- **Claude Code**: `claude mcp add --transport http deukgeuni https://<도메인>/mcp --header "Authorization: Bearer <개인 토큰>"`

각 앱의 메뉴 이름과 요금제 조건은 자주 바뀐다. 2026-11 기준 Claude는 유료 요금제에서 커스텀 커넥터(모바일 포함)를, ChatGPT는 유료 요금제에서 사용자 MCP 서버를 지원한다.

## 실행

```bash
pip install -r mcp_server/requirements.txt
PUBLIC_URL=https://bot.example.com WL_MONGO_URI=... python -m mcp_server serve   # 127.0.0.1:8081, 앞단에 Caddy
```
배포는 텔레그램 봇과 같은 서버: [`deploy-telegram.md`](deploy-telegram.md) (Caddy가 `/mcp`, OAuth 경로, `/login`을 8081로 보낸다).

## 테스트

```bash
pip install mcp mongomock
python3 -m unittest tests.test_mcp -v
```
실제 HTTP 서버와 MCP 클라이언트로: 인증 없는 요청 거부, 메타데이터, 클라이언트 등록, PKCE 로그인(틀린 코드 포함), 토큰 교환, 모든 종류의 도구·프롬프트·리소스, 갱신 토큰 교체, 토큰 해제, 사용자 분리, 개인 토큰, 무차별 대입 차단.
