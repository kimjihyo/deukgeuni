# 서버 배포: 텔레그램 봇 + MCP (EC2 + 웹훅 + MongoDB Atlas)

한 서버에 텔레그램 봇(8080)과 MCP 서버(8081, [`mcp.md`](mcp.md))를 함께 띄운다. Caddy가 경로로 나눈다.

득근이를 텔레그램 봇으로 띄우는 방법. 구조는 [`architecture.md`](architecture.md)의 "메신저 봇" 행.

```
텔레그램 ──HTTPS──▶ Caddy(443, 인증서 자동) ──▶ 봇 서버 127.0.0.1:8080 (python -m bot serve)
                                                     ├─ LLM API (Claude 또는 OpenAI)
                                                     └─ MongoDB Atlas (사용자별 기록·문서·최근 대화)
cron: 매일 백업(Mongo → 데이터 git 레포), 운동 알림(python -m bot remind)
```

## 준비물

| 무엇 | 어디서 | 메모 |
| --- | --- | --- |
| 봇 토큰 | 텔레그램에서 @BotFather → `/newbot` | 봇 표시 이름은 "득근이" |
| LLM API 키 | Anthropic Console 또는 OpenAI 대시보드 | 사용량 과금. 예산 한도를 걸어 둔다 |
| MongoDB 연결 문자열 | MongoDB Atlas 무료 클러스터 → Connect → Drivers | Network Access에 EC2의 탄력적 IP만 허용 |
| 서버 | EC2, Ubuntu 24.04, 작은 ARM 인스턴스, 서울 리전 | 탄력적 IP를 붙인다 (IP가 바뀌면 DNS가 깨짐) |
| 도메인 | 내 도메인의 서브도메인 (예: `bot.내도메인`) | A 레코드 → 탄력적 IP |

## 1. EC2

1. 인스턴스 생성 (Ubuntu 24.04 ARM), 탄력적 IP 할당·연결
2. 보안 그룹 인바운드
   - 22 (SSH): **내 IP만**
   - 80, 443 (HTTP/HTTPS): 전체 — 80은 인증서 발급용, 443은 텔레그램 웹훅
3. DNS: `bot.내도메인` A 레코드 → 탄력적 IP. `dig bot.내도메인`으로 확인
4. 서버에서:
   ```bash
   git clone https://github.com/kimjihyo/deukgeuni /tmp/deukgeuni
   sudo bash /tmp/deukgeuni/deploy/setup-ec2.sh bot.내도메인
   ```
   - Python, git, Caddy 설치, `deukgeuni` 시스템 사용자, `/opt/deukgeuni`에 코드와 가상환경, systemd 서비스, Caddy 설정까지 한다
   - 웹훅 비밀값은 자동 생성된다
   - Ubuntu 저장소에 `caddy`가 없으면 Caddy 공식 설치 문서의 apt 저장소를 추가한 뒤 다시 실행

## 2. 설정 채우기

```bash
sudo nano /etc/deukgeuni/bot.env
```
- `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`(또는 `OPENAI_API_KEY` + `LLM_PROVIDER=openai` + `LLM_MODEL`), `WL_MONGO_URI`
- `ALLOWED_TELEGRAM_IDS`: 처음엔 비워 두지 말고 아무 숫자나 넣고 시작 → 봇에게 `/whoami` → 나온 ID로 바꾸고 재시작
- 파일 권한은 `640 root:deukgeuni` (setup이 맞춰 둠). 이 파일은 절대 git에 올리지 않는다

## 3. 시작과 웹훅 등록

```bash
sudo systemctl start deukgeuni-bot deukgeuni-mcp
sudo journalctl -u deukgeuni-bot -u deukgeuni-mcp -f   # 로그
curl -s https://bot.내도메인/.well-known/oauth-authorization-server   # MCP OAuth 메타데이터가 JSON으로 나오면 정상
curl https://bot.내도메인/healthz              # ok 가 나오면 Caddy·인증서·봇 연결 정상
sudo -u deukgeuni bash -c 'cd /opt/deukgeuni && set -a && . /etc/deukgeuni/bot.env && set +a && .venv/bin/python -m bot set-webhook'
```
텔레그램에서 봇에게 `/start`. 다른 앱 연결은 봇에게 `/link`로 코드를 받아 [`mcp.md`](mcp.md)의 "앱에 연결하기"대로.

## 4. 기존 기록 옮기기 (선택)

파일 데이터 폴더(예: 비공개 git 레포)를 서버에 받고 Mongo로 옮긴다.
```bash
sudo -u deukgeuni git clone <데이터 레포> /opt/workout-log      # 배포 키 등 쓰기 권한 있는 방식으로
sudo -u deukgeuni bash -c 'cd /opt/deukgeuni && set -a && . /etc/deukgeuni/bot.env && set +a && \
  .venv/bin/python -m bot import-user --telegram-id <내ID> --from /opt/workout-log'
```

## 5. 백업과 알림 (cron)

- `deploy/backup.sh <내ID> /opt/workout-log`: Mongo → 데이터 레포 파일(csv + 문서)로 내보내고 커밋·푸시
  - 데이터 레포에 쓰기 권한이 필요하다. 그 레포 하나에만 쓰기 권한이 있는 **배포 키**(SSH)를 `deukgeuni` 사용자에게 두는 방식을 권장
- `python -m bot remind --telegram-id <내ID>`: 득근이가 먼저 메시지 (오늘 처방, 비어 있는 기록 확인)
- 예시: [`deploy/crontab.example`](../deploy/crontab.example) — 서버 시간대가 UTC면 시각을 UTC로 적는다

## 운영

| 할 일 | 명령 |
| --- | --- |
| 코드 업데이트 | `cd /opt/deukgeuni && sudo -u deukgeuni git pull && sudo systemctl restart deukgeuni-bot deukgeuni-mcp` |
| MCP 연결 코드·토큰 | `python -m mcp_server issue-code` / `issue-token` / `revoke` (`docs/mcp.md`) |
| 로그 | `sudo journalctl -u deukgeuni-bot -n 200` (LLM 사용량·캐시 적중 `cache_read_input_tokens`도 여기) |
| 웹훅 해제 | `python -m bot delete-webhook` |
| 공개 주소 없이 테스트 | 서비스를 멈추고 `python -m bot poll` (폴링은 웹훅을 자동 해제함. 끝나면 `set-webhook` 다시) |
| 사용자 대화 맥락 초기화 | 사용자가 봇에게 `/reset` |

## 보안 체크리스트

- [ ] SSH는 내 IP만, 비밀번호 로그인 끔
- [ ] `ALLOWED_TELEGRAM_IDS` 설정 (비우면 서버가 시작을 거부함)
- [ ] 웹훅 비밀값 설정 (요청 헤더가 다르면 401)
- [ ] Atlas Network Access는 EC2 IP만
- [ ] LLM 사용 한도·AWS 결제 알림
- [ ] `/etc/deukgeuni/bot.env` 권한 640, git 밖

## 테스트 (로컬, 네트워크 없이)

```bash
pip install mongomock anthropic openai pymongo mcp
python3 -m unittest discover -s tests -v
```
가짜 텔레그램·가짜 LLM·메모리 MongoDB로 온보딩, 기록, 사용자 분리, 웹훅 비밀값, 백업 왕복, 두 LLM 요청 형식을 확인한다.
