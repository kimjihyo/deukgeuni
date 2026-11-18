# 데이터 형식

운동 기록의 데이터 모양. **저장 방식과 무관**하다 — CSV, JSON Lines, SQLite 등 어디에 저장해도 이 테이블·필드를 그대로 쓴다 (`docs/storage.md`). 코드상 정의는 `scripts/wlcore/model.py`.

읽고 쓸 때는 파일을 직접 고치지 말고 `scripts/wl.py` 명령을 쓴다. 명령이 타입 변환과 검사를 대신 한다.

## 테이블

| 테이블 | 단위 | 키 | 역할 |
| --- | --- | --- | --- |
| `exercises` | 운동 1종 | `exercise_id` | 운동 사전. 이름·별칭·근육·증량 단위 |
| `sessions` | 운동 1회 | `session_id` | 날짜, 블록, 상태 |
| `sets` | 세트 1개 | `session_id` + `exercise_id` + `set_no` | 핵심 데이터. 무게·횟수·RPE |

타입: 문자열 / 정수(`set_no`, `reps`) / 숫자(무게, RPE, `increment_kg`). 값이 없으면 비움(null).

## exercises

`exercise_id,name_ko,category,pattern,equipment,load_type,primary_muscles,secondary_muscles,increment_kg,aliases`

- `exercise_id`: 영문 snake_case 고정키 (`barbell_back_squat`). 한 번 정하면 바꾸지 않는다
- `category`: `대회종목` / `변형` / `보조`
- `pattern`: `스쿼트` / `힌지` / `수평밀기` / `수직밀기` / `수평당기기` / `수직당기기` / `런지` / `고립`
- `load_type`: `중량` / `맨몸` / `맨몸+추가` / `어시스트`
- `primary_muscles`, `secondary_muscles`: `;` 구분. 주간 세트 집계 때 주동근 1.0, 보조근 0.5
- `increment_kg`: 처방 무게 반올림 단위
- `aliases`: 쉼표 구분. 자유 형식 입력을 매칭할 때 사용
- 변형(정지 벤치, 정지 스쿼트 등)은 별개 운동으로 등록

## sessions

`session_id,date,block,program_day,title,status,notes`

- `session_id`: 날짜(`2026-11-15`). 같은 날 두 번째 세션은 `2026-11-15-2`
- `status`: `done` / `skipped` / `planned`
- `block`, `program_day`: 블록 이름과 블록 안 위치(`W1D2`). 블록 밖이면 비움

## sets

`date,session_id,exercise_id,set_no,set_type,weight_kg,reps,rpe,target_weight_kg,target_reps,target_rpe,note`

- `set_no`: 그 세션·그 운동 안의 순서 (워밍업 포함, 1부터)
- `set_type`: `warmup` / `work` / `top` / `backoff` / `drop` / `amrap`
- `weight_kg`: kg. lb는 ×0.4536, 소수점 한 자리
  - 맨몸 0, 추가중량 양수, 어시스트 음수
  - 덤벨 등 편측 기구는 한쪽 무게
- `rpe`: RIR 기반 (RPE = 10 − RIR), 0.5 단위. 워밍업은 비움
- **실패 세트**: `reps=0`, `rpe` 비움, note에 이유. e1RM 계산에서 자동 제외
- `target_*`: 계획값. `target_reps`는 텍스트 (`5`, `8-12`, 최소 횟수는 `3+`)
- 값을 모르면 비우고 note에 적는다 (추측해서 채우지 않음)
- 정렬: `session_id`, `exercise_id`, `set_no`

## 검사

모든 쓰기 명령은 저장 전에 자동 검사하고, 실패하면 저장하지 않는다. 전체 검사는 `python3 scripts/wl.py check`.
검사 항목: 필수값, 허용값(status, set_type 등), 중복 키, 없는 운동·세션 참조, 세트 날짜 = 세션 날짜, RPE 5–10·0.5 단위, RPE가 있는 실패 세트.
