# 강도: RPE와 e1RM

모든 목표(파워빌딩·파워리프팅·보디빌딩)에 공통.

## RPE (RIR 기반)

| RPE | 의미 |
| --- | --- |
| 10 | 더 못 함 (또는 실패) |
| 9.5 | 1개는 못 하지만 무게를 조금 더 올릴 수는 있었음 |
| 9 | 1개 남음 |
| 8 | 2개 남음 |
| 7 | 3개 남음 |
| 6 이하 | 워밍업 수준, 기록하지 않아도 됨 |

- RPE = 10 − RIR, 0.5 단위로 기록·처방 — [Zourdos 2016](../evidence/autoregulation.md#zourdos-2016-rir-기반-rpe-척도), [Helms 2016](../evidence/autoregulation.md#helms-2016-rir-기반-rpe-적용법)
- 메인 리프트 탑세트는 %1RM보다 RPE로 처방한다. %는 시작 무게 참고용 — [Helms 2018](../evidence/autoregulation.md#helms-2018-rpe-vs-1rm-부하), [2025 자동조절 메타분석](../evidence/autoregulation.md#2025-자동조절-네트워크-메타분석)
- 사용자가 "3개밖에 안 됐다"처럼 말하면 그 세트는 RPE 10으로 본다. 애매하면 묻는다

## e1RM (추정 1RM)

- RTS(Tuchscherer) RPE 차트: e1RM = 무게 ÷ 차트 % — [RTS](../evidence/programs.md#rts-reactive-training-systems)
  - 예: 1@9 = 95.5%, 3@8 = 86.3%, 4@8 = 83.7%, 5@8 = 81.1%, 6@9 = 81.1%
  - 계산: `wl.py pct <횟수> <RPE>`, `wl.py e1rm`
- 계산 대상: 워밍업 제외, 1–10회, RPE ≥ 6.5, 무게 > 0. 실패 세트(0회) 제외
- **현재 e1RM** = 최근 21일 세션별 최고값의 중앙값. 하루 좋은 날·나쁜 날에 휘둘리지 않게
- 반복이 많을수록 차트 오차가 크다 → 근력 판단은 탑세트(1–6회) 위주. 고반복에 특히 강한 사람은 볼륨 세트로 계산한 e1RM이 실제보다 높게 나온다
- 맨몸 운동(풀업 등)은 e1RM 대신 횟수·RPE 추세로 본다

## 처방 무게 계산

`wl.py load <e1RM> <횟수> <RPE> --step <증량단위>` → e1RM × 차트 %를 증량 단위로 반올림.
증량 단위는 운동 사전의 `increment_kg`와 사용자 장비(최소 원판)를 따른다.
