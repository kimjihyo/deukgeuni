# 인수인계: 득근이 봇·MCP 서버 개발과 배포

이 문서는 득근이의 **서버 쪽(텔레그램 봇, MCP 서버, 계정·배포)**을 맡을 에이전트를 위한 맥락이다. 운동 코칭과 기록은 다른 대화가 맡고, 이 작업의 범위가 아니다.

## 1. 한 줄 요약

득근이는 파워빌딩 코치 에이전트다. 지식(스킬·원칙 문서)과 계산 도구(`scripts/wl.py`)는 이 저장소에 있고, 사용자별 기록은 저장소 밖에 둔다. 서버 코드는 다 만들어졌고 오프라인 테스트(32개)는 통과한다. 하지만 **실제 텔레그램·MongoDB Atlas·LLM API에 붙여 본 적은 한 번도 없다.** 남은 일은 실제 환경 배포와 그 과정에서 나오는 버그 수정이다.

## 2. 저장소

| 저장소 | 공개 | 내용 | 이 작업에서 |
| --- | --- | --- | --- |
| `kimjihyo/deukgeuni` | 공개(MIT) | 에이전트 지식, `wl.py`, 봇, MCP, 배포 파일, 테스트 | 주 작업 대상 |
| `kimjihyo/workout-log` | 비공개 | 운영자 본인의 운동 기록(csv)과 프로필·계획 문서 | 읽기만. 배포 때 `import-user`로 Mongo에 한 번 넣는 원본. 내용을 공개 저장소·로그·테스트 픽스처에 옮기지 않는다 |

## 3. 운영자가 정한 원칙 (지킬 것)

- **특정 LLM에 묶이지 않는다.** Claude와 OpenAI를 설정값(`LLM_PROVIDER`)으로 바꿔 쓴다. 지식은 평범한 마크다운이라 어떤 에이전트·채팅 앱에서도 읽힌다
- **일반 지식과 개인 데이터를 분리한다.** 공개 저장소에는 누구에게나 맞는 내용만. 개인 데이터는 절대 커밋하지 않는다
- **문서에 외부 인터넷 링크를 넣지 않는다.** 필요한 자료는 내용을 요약해 로컬 문서로 두고, 논문은 DOI를 텍스트로만 적는다
- **스킬은 `skills/<이름>/SKILL.md` 표준 형식**(YAML 앞머리 포함)
- **제3자가 바로 쓸 수 있어야 한다.** 봇에게 말을 걸면 가입 절차 없이 시작한다. LLM 비용은 운영자가 낸다. 남용은 하루 한도로 막는다
- **비밀값**(봇 토큰, API 키, Mongo URI, 웹훅 비밀)은 서버의 `/etc/deukgeuni/bot.env`에만. git, 문서, 채팅 출력에 남기지 않는다
- 운영자의 이메일 등 개인정보를 외부에 쓰지 않는다
- 삭제(데이터, 리소스, 브랜치)는 운영자 확인 후에만
- 문서와 사용자 대상 문구는 한국어

## 4. 구조

```
텔레그램 ─HTTPS─▶ Caddy(443) ─┬─▶ 봇 127.0.0.1:8080  (python -m bot serve)
Claude/ChatGPT 등 ─MCP──────▶ └─▶ MCP 127.0.0.1:8081 (python -m mcp_server serve)  경로: /mcp, OAuth 경로들
                                   둘 다 ─▶ MongoDB Atlas (사용자별 기록·문서·대화·계정·토큰)
                                   봇 ─▶ Anthropic 또는 OpenAI API
cron: 매일 Mongo 백업, 운동 알림(python -m bot remind)
```
자세한 설명은 `docs/architecture.md`, `docs/deploy-telegram.md`, `docs/mcp.md`, `docs/storage.md`.

### 코드 지도

| 경로 | 역할 |
| --- | --- |
| `scripts/wl.py` | 기록·조회·계산 CLI. `main(argv, store)`로 프로세스 안에서 호출 가능 |
| `scripts/wlcore/` | 데이터 모델(`model.py`), 저장소 추상화(`store.py`, `backends/`: csv·jsonl·sqlite·mongodb), RTS 표(`rts.py`), **처방·피로 판정(`estimate.py`)**, 봇과 MCP가 함께 쓰는 동작(`service.py`) |
| `bot/` | `config`(환경변수), `llm`(Claude/OpenAI 어댑터), `tools`(LLM 도구), `memory`(최근 대화), `agent`(도구 루프·시스템 프롬프트), `app`(메시지 처리·명령어), `telegram`(API 클라이언트), `server`(웹훅), `__main__`(serve/poll/set-webhook/delete-webhook/import-user/backup/usage/remind) |
| `mcp_server/` | `server.py`(도구 14개+`get_link_code`, 프롬프트, 리소스, 안내문), `oauth.py`(Mongo 기반 OAuth 제공자), `__main__`(serve/issue-code/issue-token/list-tokens/revoke) |
| `deukgeuni_server/` | 앱 간 공용: `accounts`(계정·연결), `linkcodes`(일회용 연결 코드), `usage`(사용량·한도), `userdata`(내보내기·삭제) |
| `deploy/` | `.env.example`, systemd 유닛 2개, `Caddyfile`, `setup-ec2.sh`, `backup.sh`, `crontab.example` |
| `tests/` | `test_bot.py`, `test_mcp.py`, `test_estimate.py`. 실행: `python3 -m unittest discover -s tests` |

## 5. 이미 내린 설계 결정

**봇**
- 웹훅만 쓴다(폴링은 개발용 `poll`). 비밀 토큰 헤더를 검사하고, 텔레그램에는 즉시 200을 돌려준 뒤 처리는 스레드에서 한다
- 같은 사용자의 메시지는 잠금으로 순서대로 처리하고, `update_id`로 중복을 거른다
- LLM 도구는 4개다: `wl`(그 사용자 데이터에만 적용되는 `wl.py`), `read_knowledge`(저장소 지식 읽기), `user_doc_get`, `user_doc_put`(프로필·계획 같은 사용자 문서)
- 최근 대화는 Mongo에 둔다. `_id` 순으로 정렬하고(같은 시각 문제), 같은 역할이 연달아 오면 합친다(알림 뒤 대화)
- **프롬프트 캐싱**
  - 순서: AGENTS.md + 런타임 안내 + 스킬 목록(1시간 캐시) → 사용자 프로필(캐시) → 날짜·현재 상태(캐시 안 함)
  - Anthropic: system에 `cache_control` ttl 1h, 그리고 최상위 자동 cache_control
  - OpenAI: `prompt_cache_key`
- 사용량(`last_usage`)은 사용자별 dict로 따로 둔다(한때 사용자끼리 섞이는 버그가 있었음)

**계정**
- 텔레그램에서 시작한 계정은 `tg:<id>`, 웹 가입은 `u:<무작위>`
- `/link`, `/connect`로 여러 앱을 한 계정에 묶는다
- `SIGNUP_MODE=open|allowlist`, 하루 사용자별·전체 메시지 한도, 사용량 기록
- `/export`(zip), `/delete`(확인 후)

**MCP**
- mcp SDK 2.3.0의 `MCPServer`, Streamable HTTP
- OAuth는 SDK 라우트 + `MongoOAuthProvider`
  - 동적 클라이언트 등록, PKCE
  - 로그인 페이지에서 일회용 연결 코드 입력, 5회 시도 제한
  - 토큰은 해시로 저장, 리프레시 토큰 회전, 폐기 지원, 개인 토큰 발급
- 사용자 식별: `get_access_token().subject`
- 도구마다 읽기·쓰기 annotation을 붙였다

**처방·피로 (계약)**
- 3대 처방 무게는 `wl.py prescribe --lift --reps --rpe`, 피로 판정은 `wl.py fatigue`, 현재 수준은 `wl.py estimate`가 정한다. 로직은 `wlcore/estimate.py`
- 봇 런타임 안내와 MCP 안내문은 "이 도구로만 판단하고 근거 세트를 말하라"고 지시한다
- MCP 도구: `prescribe(lift, reps, rpe)`, `load_from_e1rm`, `fatigue_check`
- 이 판정 로직과 `skills/`, `docs/principles/`는 코칭 쪽 소관이다. 서버 작업 때문에 바꿔야 하면 운영자에게 먼저 말한다

## 6. 알려진 함정 (이미 고친 것 포함)

- MCP SDK 클라이언트(테스트)는 `httpx2` 패키지가 필요하다
- OAuth revoke 엔드포인트는 public client여도 `client_secret` 필드를 요구한다
- 텔레그램 `call()`의 timeout 인자 충돌을 고친 적 있다
- 문서 경로 검사는 `../`를 막아야 한다(`check_doc_path`). `lstrip("./")` 버그를 고쳤다
- YAML 앞머리 안의 따옴표가 파싱을 깬 적 있다
- 테스트는 open 가입이 기본이라, allowlist를 시험할 때는 명시해야 한다
- 실제 데이터 회귀 테스트는 `WL_TEST_DATA=<workout-log 경로>`일 때만 돈다(평소엔 skip)

## 7. 남은 일 (순서대로)

운영자는 지금 AWS 첫 단계에 있다. 루트 계정으로 로그인해서 관리자 IAM 사용자(`jihyo-admin`)를 만드는 중이다. 운영자는 콘솔 화면을 보며 작은 질문을 하나씩 하는 편이니, 단계별로 짧게 안내한다.

1. IAM 관리자 사용자와 MFA, 루트 MFA, 예산 알림(AWS Budgets)
2. LLM 비용 한도: Anthropic과 OpenAI 콘솔에 월 예산을 건다
3. MongoDB Atlas 무료 클러스터, DB 사용자, 연결 문자열. Network Access는 EC2 탄력적 IP만 허용
4. EC2를 만든다(서울 리전, Ubuntu 24.04 ARM, t4g.micro 정도)
   - 탄력적 IP를 붙인다
   - 보안 그룹: 22번은 운영자 IP만, 80·443은 전체
5. 운영자 도메인의 서브도메인 A 레코드 → 탄력적 IP. 도메인은 운영자에게 묻는다
6. `deploy/setup-ec2.sh <서브도메인>` 실행, `/etc/deukgeuni/bot.env` 채우기(봇 토큰은 @BotFather)
7. 서비스 시작, `/healthz`와 OAuth 메타데이터 확인, `python -m bot set-webhook`
8. 운영자 기록 가져오기: `python -m bot import-user`로 workout-log를 운영자 텔레그램 계정에 넣는다. `ADMIN_TELEGRAM_IDS` 설정(봇에게 `/whoami`)
9. **실환경 스모크 테스트**
   - 텔레그램 왕복: 기록, 조회, 처방
   - Anthropic 캐시가 실제로 맞는지: 응답 usage의 `cache_read_input_tokens`
   - `LLM_PROVIDER=openai`로 바꿔서도 동작하는지
   - 하루 한도, `/link`, `/export`
10. MCP를 Claude와 ChatGPT에 커스텀 커넥터로 연결하고 OAuth 흐름을 끝까지 확인한다
11. cron: 백업(`deploy/backup.sh`, Mongo → 비공개 데이터 저장소), 운동 알림
12. 실환경에서 나온 버그를 고치고, 테스트를 추가하고, 문서(`docs/deploy-telegram.md`, `docs/mcp.md`)를 실제 절차에 맞게 고친다

## 8. 작업 규칙

- 고친 뒤 `python3 -m unittest discover -s tests`가 모두 통과해야 커밋한다
- 커밋 메시지 끝에 운영자가 정한 공동 작성자 줄을 붙인다(이 대화 환경의 안내를 따른다)
- 운영자 확인이 필요한 결정은 묻는다: 비용이 드는 리소스, 삭제, 공개 범위, 도메인
- 서버 로그나 오류 메시지에 비밀값이나 사용자 기록 본문이 찍히지 않게 한다
