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
import wl  # noqa: E402  (the same CLI people use locally)

# repo knowledge the model may read (never user data, never secrets)
KNOWLEDGE_ROOTS = ("AGENTS.md", "docs/", "skills/")
BLOCKED_WL = {"import", "export", "backends"}  # file-system / storage admin: not for the model

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


def _run_wl(store, args: str) -> str:
    try:
        argv = shlex.split(args)
    except ValueError as e:
        return f"인자 해석 실패: {e}"
    if not argv:
        return "args가 비었음"
    if argv[0] in BLOCKED_WL or "--backend" in argv:
        return f"'{argv[0]}'는 봇에서 쓸 수 없는 명령"
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wl.main(argv, store=store)
    except SystemExit as e:  # argparse errors and wl.py validation failures
        code = e.code if isinstance(e.code, int) else 1
        if e.code and not isinstance(e.code, int):
            err.write(str(e.code))
    text = (out.getvalue() + err.getvalue()).strip() or "(출력 없음)"
    return text if code == 0 else f"[실패 {code}] {text}"


def _read_knowledge(path: str) -> str:
    if not path:
        files = [str(p.relative_to(REPO)) for root in KNOWLEDGE_ROOTS
                 for p in ([REPO / root] if root.endswith(".md") else sorted((REPO / root).rglob("*.md")))]
        return "\n".join(files)
    p = (REPO / path).resolve()
    rel = str(p.relative_to(REPO)) if p.is_relative_to(REPO) else ""
    if not rel or not any(rel == r or rel.startswith(r) for r in KNOWLEDGE_ROOTS) or p.suffix != ".md" or not p.exists():
        return f"읽을 수 없는 경로: {path}"
    return p.read_text(encoding="utf-8")


def run_tool(store, name: str, inp: dict) -> str:
    try:
        if name == "wl":
            return _run_wl(store, inp.get("args", ""))
        if name == "read_knowledge":
            return _read_knowledge((inp.get("path") or "").strip())
        if name == "user_doc_get":
            path = (inp.get("path") or "").strip()
            if not path:
                return "\n".join(store.docs()) or "(문서 없음)"
            text = store.doc(path)
            return text if text is not None else f"문서 없음: {path}"
        if name == "user_doc_put":
            store.put_doc(inp["path"], inp["content"])
            return f"{inp['path']} 저장됨"
        return f"알 수 없는 도구: {name}"
    except (ValueError, KeyError) as e:
        return f"[실패] {e}"
