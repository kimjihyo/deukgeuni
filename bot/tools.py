"""Tools the model can call. Every tool works on ONE user's Store, chosen by the bot —
the model never sees file paths, credentials, or other users."""
from __future__ import annotations

import contextlib
import io
import shlex
import sys
from pathlib import Path

from .config import REPO
from .llm import Tool

sys.path.insert(0, str(REPO / "scripts"))
from wlcore import service  # noqa: E402  (same operations as the CLI and the MCP server)

TOOLS = [
    Tool("wl",
         "득근이 기록 도구 wl.py를 실행한다 (이 사용자 데이터에만 적용). args는 명령줄 인자 문자열. "
         "예: 'show 2026-11-21', 'find 벤치', 'e1rm', 'load 185 3 8 --step 5', 'volume --weeks 4', "
         "'session add 2026-11-21 --title \"스쿼트 헤비\"', 'set add 2026-11-21 barbell_back_squat 160x4@9 --type top', "
         "'set edit 2026-11-21 barbell_back_squat 2 rpe=8.5', 'query sets exercise_id=barbell_deadlift', 'check'. "
         "삭제(set rm --yes)는 사용자 확인을 받은 뒤에만.",
         {"type": "object", "properties": {"args": {"type": "string"}}, "required": ["args"]}),
    Tool("read_knowledge",
         "득근이 지식 문서를 읽는다: skills/<이름>/SKILL.md, docs/..., AGENTS.md. path를 비우면 목록을 돌려준다.",
         {"type": "object", "properties": {"path": {"type": "string"}}}),
    Tool("user_doc_get",
         "이 사용자의 코칭 문서를 읽는다: profile.md, status.md, history.md, blocks/<이름>.md. path를 비우면 목록.",
         {"type": "object", "properties": {"path": {"type": "string"}}}),
    Tool("user_doc_put",
         "이 사용자의 코칭 문서를 전체 내용으로 저장한다 (덮어쓰기). 고칠 때는 먼저 읽고 전체를 다시 쓴다.",
         {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
          "required": ["path", "content"]}),
]


def run_tool(store, name: str, inp: dict) -> str:
    try:
        if name == "wl":
            return service.run_wl(store, inp.get("args", ""))
        if name == "read_knowledge":
            return service.read_knowledge(inp.get("path") or "")
        if name == "user_doc_get":
            return service.get_doc(store, inp.get("path") or "")
        if name == "user_doc_put":
            return service.put_doc(store, inp["path"], inp["content"])
        return f"알 수 없는 도구: {name}"
    except service.ServiceError as e:
        msg = str(e)
        return msg if msg.startswith("[실패") else f"[실패] {msg}"
    except (ValueError, KeyError) as e:
        return f"[실패] {e}"
