# 운동 기록

사용자가 운동 결과를 말했을 때. 형식은 자유다: "스쿼트 160 4회 rpe9", "벤치 135 6개 9정도", "ohp 70으로 3개밖에 안됨", "3@70, 2x6@60".

## 절차

1. **해석**
   - 운동 이름 → `wl.py find <이름>`으로 exercise_id. 없거나 여러 개면 묻는다. 새 운동이면 `wl.py exercise add exercise_id=... name_ko=... pattern=... primary_muscles=... increment_kg=...`
   - 날짜: 말이 없으면 오늘. "어제", "토요일" 등은 실제 날짜로 바꿔 확인
   - 세트 유형: 계획의 탑세트면 `top`, 백오프면 `backoff`, 나머지 `work`. 워밍업은 말했을 때만
   - RPE: 없으면 비운다. 핵심 세트(탑세트)인데 없으면 한 번만 묻는다
   - "N개밖에 안 됐다" → RPE 10. 실패한 시도 → 0회, RPE 없음, `--note "실패: 이유"`
   - 무게를 모르면 비우고(`x10@8`) note에 적은 뒤 `status.md` 미해결 질문에
2. **세션**: `wl.py show <날짜>`
   - `planned` 세션이 있으면 `wl.py session set <id> status=done`
   - 없으면 `wl.py session add <날짜> --title "..." [--block .. --day ..]`
   - 같은 세트가 이미 있으면 중복인지 묻는다
3. **세트**: `wl.py set add <session_id> <exercise_id> <무게>x<횟수>@<RPE> [--times N] --type ... [--target "<계획>"]`
   - 계획값(블록 문서의 처방)이 있으면 `--target`으로 같이 남긴다
4. **확인**: `wl.py show <날짜>`, `wl.py check`
5. **저장**: 데이터 폴더가 git이면 커밋·푸시 (`log: <날짜> <요약>`)
6. **보고** (짧게)
   - 메인 리프트: 이번 세트 e1RM과 현재 e1RM 대비 (`wl.py e1rm --lift <id>`)
   - 목표 RPE보다 1 이상 높거나 낮은 세트, 그에 따른 다음 처방 변화
   - 통증 등 메모
7. **상태 갱신**: `status.md`의 다음 세션·미해결 질문. 다음 처방이 바뀌었으면 블록 문서에도

## 하지 말 것

- 계획보다 횟수가 많았다고 지적하지 않는다 (RPE 상한을 지켰으면 처방대로 한 것)
- 데이터 신호 없이 피로 경고하지 않는다 (`docs/principles/fatigue-deload.md`)
- 사용자가 안 한 세트를 추측해서 채우지 않는다
