"""득근이 agent loop: build the prompt (cache-friendly order), call the LLM, run tools, repeat."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import REPO, Config
from .llm import LLM, Segment
from .memory import Memory
from .tools import TOOLS, run_tool

log = logging.getLogger("deukgeuni.agent")
WEEKDAYS = "월화수목금토일"

RUNTIME_NOTES = """\
## 실행 환경: 메신저 봇 (위 AGENTS.md보다 이 절이 우선)

- 셸·파일 접근이 없다. 아래 도구만 쓴다:
  - `wl`: `python3 scripts/wl.py <인자>` 대신 args에 `<인자>`만 넣는다 (예: "show 2026-11-21"). 이 사용자 데이터에만 적용된다
  - `read_knowledge`: skills/·docs/·AGENTS.md 읽기. 상황에 맞는 skill(`skills/<이름>/SKILL.md`)을 먼저 읽고 따른다
  - `user_doc_get` / `user_doc_put`: 이 사용자의 profile.md, status.md, history.md, blocks/*.md
- 데이터 폴더 찾기, git 커밋·푸시, `.wl-data`는 신경 쓰지 않는다. 저장은 즉시 반영된다
- 코칭에 영향을 줄 말(통증, 일정, 선호, 컨디션)은 듣는 즉시 문서에 적는다: 계속 지킬 것은 profile.md, 당분간 신경 쓸 것은 status.md 메모
- 대화 맥락은 최근 몇 개 메시지만 남는다. 다음에 이어갈 일은 반드시 status.md에 적는다
- 답장은 메신저용 일반 텍스트: 마크다운 표·제목(#)·굵게(**) 쓰지 말고, 줄바꿈과 "-" 목록만. 짧게
- 오늘 날짜는 아래 '현재' 절을 따른다. "어제", "토요일"은 그 기준으로 실제 날짜로 바꾼다
"""


def skills_index() -> str:
    lines = ["## 스킬 목록 (필요할 때 read_knowledge로 본문을 읽는다)"]
    for p in sorted((REPO / "skills").glob("*/SKILL.md")):
        fm = p.read_text(encoding="utf-8").split("---")[1]
        name = re.search(r"^name:\s*(.+)$", fm, re.M).group(1).strip().strip('"')
        desc = re.search(r"^description:\s*(.+)$", fm, re.M).group(1).strip().strip('"')
        lines.append(f"- {name}: {desc} → skills/{name}/SKILL.md")
    return "\n".join(lines)


class Agent:
    def __init__(self, cfg: Config, llm: LLM, memory: Memory, store_factory):
        """store_factory(user_id) -> wlcore Store for that user."""
        self.cfg, self.llm, self.memory, self.store_factory = cfg, llm, memory, store_factory
        self.base_prompt = (REPO / "AGENTS.md").read_text(encoding="utf-8") + "\n\n" + RUNTIME_NOTES + "\n" + skills_index()

    def _system(self, store, new_user: bool) -> list[Segment]:
        now = datetime.now(ZoneInfo(self.cfg.timezone))
        profile = store.doc("profile.md") or "(프로필 없음)"
        status = store.doc("status.md") or "(상태 없음)"
        current = f"## 현재\n\n오늘: {now:%Y-%m-%d} ({WEEKDAYS[now.weekday()]}) {now:%H:%M} {self.cfg.timezone}\n"
        if new_user:
            current += "\n이 사용자는 처음이다. skills/onboarding/SKILL.md를 읽고 온보딩을 시작한다 (데이터 폴더는 이미 준비됨: 1·2단계 생략).\n"
        return [
            Segment(self.base_prompt, cache=True),                       # same for every user
            Segment("## 사용자 프로필 (profile.md)\n\n" + profile, cache=True),  # changes rarely
            Segment(current + "\n## 사용자 상태 (status.md)\n\n" + status),     # changes often → after the cache point
        ]

    def handle(self, user_id: str, text: str, *, save_user_text: bool = True) -> str:
        store = self.store_factory(user_id)
        try:
            profile = store.doc("profile.md")
            # onboarding until there are records or the template profile has been filled in
            new_user = not store.rows("sets") and (profile is None or "- 운동 경력:\n" in profile)
            system = self._system(store, new_user=new_user)
            messages = self.memory.recent(user_id, self.cfg.history_turns) + [{"role": "user", "content": text}]
            reply_text = ""
            for _ in range(self.cfg.max_tool_rounds):
                reply = self.llm.chat(system, messages, TOOLS, user_key=user_id)
                if not reply.tool_calls:
                    reply_text = reply.text.strip()
                    break
                messages.append({"role": "assistant", "content": reply.text, "tool_calls": reply.tool_calls})
                for c in reply.tool_calls:
                    result = run_tool(store, c["name"], c["input"])
                    log.info("tool %s %s -> %s", c["name"], c["input"], result[:200].replace("\n", " | "))
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": result[:20000]})
            else:
                reply_text = "처리할 게 많아서 중간에 멈췄어요. 한 번 더 말해 주시면 이어서 할게요."
            if not reply_text:
                reply_text = "기록했어요."
            if save_user_text:
                self.memory.append(user_id, "user", text)
            self.memory.append(user_id, "assistant", reply_text)
            return reply_text
        finally:
            store.close()
