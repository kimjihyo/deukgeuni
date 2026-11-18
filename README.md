# strength-coach-agent

파워빌딩 코치 에이전트. 어떤 LLM 에이전트(Claude, GPT, 사내 봇 …)에든 붙여서 쓸 수 있는 **지침 + 지식 + 도구** 묶음입니다.
운동을 대화로 말하면 기록하고, e1RM·볼륨을 계산하고, 근거 기반으로 다음 루틴을 짜 줍니다.

*A model-agnostic powerbuilding coach for LLM agents: plain-Markdown instructions, evidence cards, and a stdlib-only Python CLI for logging and calculations. Docs are in Korean.*

- 현재 전문: **파워빌딩** / 확장 중: 파워리프팅, 보디빌딩 (`docs/goals/`)
- 의존성 없음: Markdown + Python 표준 라이브러리
- 개인 데이터는 이 레포 밖(비공개 레포나 로컬 폴더)에 둡니다

## 구성

| 경로 | 내용 |
| --- | --- |
| [`AGENTS.md`](AGENTS.md) | 에이전트 진입점: 원칙과 상황별 안내 |
| [`skills/`](skills) | 상황별 절차: 온보딩, 기록, 오늘 루틴, 일정 조정, 종목 대체, 블록 짜기, 주간 점검, 테스트 |
| [`docs/principles/`](docs/principles) | 공통 원칙: RPE·e1RM, 진행, 볼륨·빈도, 피로·디로드, 종목 선택, 일정, 피킹 |
| [`docs/goals/`](docs/goals) | 목표별 차이 (파워빌딩 완성, 나머지 준비 중) |
| [`docs/programs/`](docs/programs) | 블록 템플릿 |
| [`docs/evidence/`](docs/evidence) | 근거 카드 (DOI 포함). 에이전트는 여기서만 인용 |
| [`docs/data-format.md`](docs/data-format.md), [`docs/storage.md`](docs/storage.md) | 데이터 모델, 저장 방식 (csv / jsonl / sqlite / 직접 추가) |
| [`templates/`](templates) | 새 사용자 데이터 폴더 초기 파일 |
| [`scripts/wl.py`](scripts/wl.py) | 기록·조회·e1RM·볼륨·검사·이전 CLI |

## 시작하기

```bash
git clone https://github.com/kimjihyo/strength-coach-agent
cd strength-coach-agent

# 데이터 폴더 준비 (둘 중 하나)
cp -r templates data                                  # 로컬 전용 (gitignore됨)
# 또는 비공개 데이터 레포를 옆에 clone 하고:  echo ../my-training-data > .wl-data

python3 scripts/wl.py check
```

그다음 에이전트에게 이 레포를 열고 `AGENTS.md`를 따르라고 하면 됩니다. 처음이면 에이전트가 온보딩(목표·일정·현재 기록 질문)부터 진행합니다.

### 에이전트에 붙이기

- **파일·셸 접근이 되는 에이전트** (Claude Code, Codex, Cursor 등): 레포를 열면 `AGENTS.md`를 읽고 `wl.py`를 직접 실행
- **스킬을 지원하는 에이전트**: `skills/<이름>/SKILL.md`는 표준 스킬 형식(`name`, `description` 머리말)이라 그대로 등록할 수 있습니다. 예: Claude Code는 `mkdir -p .claude && ln -s ../skills .claude/skills`. 스킬 본문의 경로는 레포 루트 기준입니다
- **챗봇·슬랙 봇**: 시스템 프롬프트에 `AGENTS.md`, 도구로 `wl.py` 명령 노출, 대화마다 사용자 데이터의 `status.md`·`profile.md`를 읽게 구성

## CLI 맛보기

```bash
python3 scripts/wl.py set add 2026-11-21 barbell_back_squat 160x4@9 --type top
python3 scripts/wl.py e1rm                 # 3대 e1RM 추이·현재 추정
python3 scripts/wl.py load 185 3 8         # e1RM 185로 3회 @8 처방 무게
python3 scripts/wl.py volume --weeks 4     # 근육별 주간 세트
python3 scripts/wl.py -h
```

## 기여

- 근거 카드는 DOI를 확인한 것만 (`docs/evidence/README.md`)
- 근거 없는 규칙은 "경험칙"으로 표시
- 개인 정보·개인 기록은 넣지 않습니다

## 라이선스

MIT
