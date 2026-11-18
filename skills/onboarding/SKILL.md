---
name: onboarding
description: "처음 쓰는 사용자이거나 데이터 폴더가 없을 때. 데이터 위치·저장 방식을 정하고, 프로필을 인터뷰로 채우고, 첫 블록을 짠다."
---

# 온보딩

처음 쓰는 사용자이거나 데이터 폴더를 못 찾았을 때.

## 1. 데이터 위치 정하기

먼저 묻는다: "기존 기록 레포(또는 폴더)가 있나요?"

- **있다** (예: 개인 비공개 레포): 이 레포 옆에 clone하고 위치를 알려준다
  ```bash
  git clone <데이터 레포> ../my-training-data
  echo ../my-training-data > .wl-data      # 또는 export WL_DATA=../my-training-data
  python3 scripts/wl.py check
  ```
- **없다**: 새로 만든다. 데이터는 이 레포에 커밋하지 않는다
  - 기록을 git으로 남기고 싶으면 사용자에게 **비공개** 레포를 하나 만들어 달라고 하고, 그 레포를 clone한 폴더에 `cp -r templates/. <폴더>/`
  - 로컬에만 둘 거면 `cp -r templates data` (`data/`는 gitignore됨)

## 2. 저장 방식 (선택)

기본은 CSV. 다른 걸 원하면 데이터 폴더의 `config.toml`에서 `type`을 바꾸고 기본 운동 사전을 옮긴다:
```bash
python3 scripts/wl.py --backend csv export /tmp/seed.jsonl
python3 scripts/wl.py import /tmp/seed.jsonl --yes
```
자세히: `docs/storage.md`

## 3. 프로필 인터뷰

`profile.md`를 채운다. **한 번에 다 묻지 않는다.** 루틴을 짜는 데 꼭 필요한 것부터, 2–3개씩:

1. 목표 유형(파워빌딩/파워리프팅/보디빌딩)과 구체 목표 (예: "12월까지 3대 600")
2. 운동 가능한 요일, 세션당 시간 (평일·주말 차이)
3. 현재 기록: 3대 1RM 또는 최근 무거운 세트(무게 × 횟수 @RPE)
4. 장비: 바벨 최소 증량, 없는 기구, 스트랩·벨트
5. 부상·통증 이력
6. (나중에 물어도 됨) 약점 부위, 좋아하는·싫어하는 종목, 탑세트 스타일

답을 받는 대로 날짜와 함께 `profile.md`에 적는다.

## 4. 최근 기록 받기 (있으면)

최근 2–4주 기록을 받으면 `skills/log-workout/SKILL.md`대로 기록 → `wl.py e1rm`, `wl.py volume --weeks 4`로 현재 수준 파악.
없으면 첫 주를 RPE로 처방해서 그 결과로 e1RM을 잡는다.

## 5. 첫 블록

`skills/plan-block/SKILL.md`대로 첫 블록을 짜고 `status.md`를 채운다. 첫 블록은 보수적으로(탑세트 RPE 상한 −0.5).

## 6. 마무리

사용자에게 짧게: 블록 요약, 이번 주 첫 세션 루틴, "운동하면 자유롭게 말해 주면 기록한다".
