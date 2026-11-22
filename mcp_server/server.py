"""득근이 MCP server: tools, resources and prompts over Streamable HTTP, one user per OAuth token.

The connecting app's own model does the thinking; this server provides 득근이's memory
(records + coaching docs in MongoDB), calculations, and knowledge (this repo's docs and skills).
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from mcp.server.auth.middleware.auth_context import get_access_token  # noqa: E402
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from wlcore import service  # noqa: E402
from wlcore.store import Store  # noqa: E402

from .oauth import SCOPE, MongoOAuthProvider, login_routes  # noqa: E402

INSTRUCTIONS = """\
너는 '득근이', 사용자의 파워빌딩 코치 겸 기록 담당이다. 사용자는 운동을 자유롭게 말한다("데드 200 4개 rpe9").
- 대화를 시작하면 get_coaching_state로 프로필·상태·현재 블록을 먼저 읽는다
- 상황별 절차는 read_knowledge로 skills/<이름>/SKILL.md를 읽고 따른다 (log-workout, daily-session, reschedule, exercise-swap, plan-block, weekly-review, peaking-test, onboarding)
- 수치(e1RM, 처방 무게, 볼륨)는 반드시 도구로 계산한다. 어림잡지 않는다
- 근거는 read_knowledge로 docs/evidence의 카드만 인용한다. 없으면 '근거 카드 없음(경험칙)'
- 모호한 값만 짧게 묻고 나머지는 바로 기록한다. 삭제는 사용자 확인 후 confirm=true
- 코칭에 영향을 줄 말(통증, 일정, 선호)과 다음에 이어갈 일은 update_doc으로 status.md·profile.md에 즉시 적는다
- 탑세트를 계획보다 더 했다고 지적하지 않는다. 피로 경고는 데이터 신호가 있을 때만
- 사용자 언어로 짧게. 루틴은 '종목 무게 × 횟수 × 세트 @RPE'
"""

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)


class SetInput(BaseModel):
    weight_kg: float | None = Field(None, description="kg. 맨몸 0, 어시스트 음수, 덤벨은 한쪽. 모르면 비움")
    reps: int = Field(..., ge=0, description="반복 수. 실패한 시도는 0 (rpe 비움)")
    rpe: float | None = Field(None, ge=5, le=10, description="RIR 기반 RPE, 0.5 단위. 워밍업·모르면 비움")
    set_type: Literal["warmup", "work", "top", "backoff", "drop", "amrap"] = "work"
    times: int = Field(1, ge=1, le=20, description="같은 세트를 몇 번 반복했는지 (3x5면 times=3, reps=5)")
    target: str | None = Field(None, description="계획값 '무게x횟수@RPE', 예 '160x3+@8'")
    note: str | None = None


def build(cfg: dict, db, client=None) -> MCPServer:
    """cfg: public_url (https://bot.example.com), mcp_path (/mcp), timezone, allowed_hosts."""
    public = cfg["public_url"].rstrip("/")
    resource_url = public + cfg.get("mcp_path", "/mcp")
    tz = ZoneInfo(cfg.get("timezone", "Asia/Seoul"))
    provider = MongoOAuthProvider(db, issuer_url=public, resource_url=resource_url)
    mongo_client = client or db.client

    mcp = MCPServer(
        name="deukgeuni", title="득근이", version="0.1.0", instructions=INSTRUCTIONS,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=public, resource_server_url=resource_url, required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
            revocation_options=RevocationOptions(enabled=True), validate_token_resource=False,
        ),
    )
    mcp.provider = provider  # for the CLI / tests

    def store() -> Store:
        tok = get_access_token()
        if tok is None or not tok.subject:
            raise service.ServiceError("인증된 사용자가 없음")
        st = Store(config={"type": "mongodb", "user": tok.subject, "client": mongo_client, "database": db.name})
        service.seed_from_templates(st)  # first contact from any app
        return st

    def run(fn, *a, **kw) -> str:
        st = store()
        try:
            return fn(st, *a, **kw)
        except service.ServiceError as e:
            return str(e) if str(e).startswith("[실패") else f"[실패] {e}"
        except ValueError as e:  # Store validation
            return f"[실패] {e}"
        finally:
            st.close()

    # ---------------------------------------------------------------- tools: state & knowledge
    @mcp.tool(annotations=READ, description="프로필·상태(다음 세션, 미해결 질문)·현재 블록 문서를 한 번에 읽는다. 대화 시작 때 먼저 호출.")
    def get_coaching_state() -> str:
        now = datetime.now(tz)
        return f"오늘: {now:%Y-%m-%d} ({'월화수목금토일'[now.weekday()]}) {now:%H:%M}\n\n" + run(service.coaching_state)

    @mcp.tool(annotations=READ, description="득근이 지식 문서 읽기: skills/<이름>/SKILL.md, docs/principles/…, docs/evidence/…, AGENTS.md. path를 비우면 목록.")
    def read_knowledge(path: str = "") -> str:
        try:
            return service.read_knowledge(path)
        except service.ServiceError as e:
            return f"[실패] {e}"

    @mcp.tool(annotations=READ, description="사용자 문서 읽기: profile.md, status.md, history.md, blocks/<이름>.md. path를 비우면 목록.")
    def get_doc(path: str = "") -> str:
        return run(service.get_doc, path)

    @mcp.tool(annotations=WRITE, description="사용자 문서를 전체 내용으로 저장(덮어쓰기). 고칠 때는 먼저 get_doc으로 읽고 전체를 다시 쓴다.")
    def update_doc(path: str, content: str) -> str:
        return run(service.put_doc, path, content)

    # ---------------------------------------------------------------- tools: records
    @mcp.tool(annotations=READ, description="운동 이름·별칭으로 exercise_id 찾기 (예: '티바', '벤치'). 비우면 전체 목록.")
    def find_exercise(text: str = "") -> str:
        return run(service.find_exercise, text)

    @mcp.tool(annotations=WRITE, description="운동 사전에 새 운동 추가. exercise_id는 영문 snake_case.")
    def add_exercise(exercise_id: str, name_ko: str,
                     pattern: Literal["스쿼트", "힌지", "수평밀기", "수직밀기", "수평당기기", "수직당기기", "런지", "고립"],
                     primary_muscles: str, increment_kg: float, category: Literal["대회종목", "변형", "보조"] = "보조",
                     equipment: str | None = None, load_type: Literal["중량", "맨몸", "맨몸+추가", "어시스트"] = "중량",
                     secondary_muscles: str | None = None, aliases: str | None = None) -> str:
        fields = {k: v for k, v in locals().items() if v is not None}
        return run(service.add_exercise, fields)

    @mcp.tool(annotations=WRITE, description="한 종목의 세트들을 기록. 그 날짜 세션이 없으면 만들고, 계획(planned) 세션이면 완료로 바꾼다. 결과로 그날 요약(e1RM 포함)을 돌려준다.")
    def log_sets(date: Annotated[str, Field(description="YYYY-MM-DD")], exercise_id: str, sets: list[SetInput],
                 session_title: str | None = None, block: str | None = None, program_day: str | None = None) -> str:
        return run(service.log_sets, date, exercise_id, [s.model_dump() for s in sets], session_title, block, program_day)

    @mcp.tool(annotations=WRITE, description="기록된 세트 수정 (예: reps 4→6 정정). changes 예: {\"reps\": 6, \"note\": \"정정\"}. 변경 전후를 돌려준다.")
    def edit_set(session_id: str, exercise_id: str, set_no: int, changes: dict[str, str | int | float | None]) -> str:
        return run(service.edit_set, session_id, exercise_id, set_no, changes)

    @mcp.tool(annotations=DELETE, description="세트 삭제. 사용자에게 확인을 받은 뒤 confirm=true로만 실행된다.")
    def delete_set(session_id: str, exercise_id: str, set_no: int, confirm: bool = False) -> str:
        return run(service.delete_set, session_id, exercise_id, set_no, confirm)

    @mcp.tool(annotations=WRITE, description="세션 만들기·수정: 계획(planned)/완료(done)/건너뜀(skipped), 메모, 제목, 블록·주차. 없는 날짜면 새로 만든다.")
    def set_session(date_or_session_id: str, status: Literal["planned", "done", "skipped"] | None = None,
                    notes: str | None = None, title: str | None = None, block: str | None = None,
                    program_day: str | None = None) -> str:
        return run(service.set_session, date_or_session_id, status, notes, title, block, program_day)

    # ---------------------------------------------------------------- tools: read & compute
    @mcp.tool(annotations=READ, description="그 날짜(또는 session_id) 세션의 세트 요약과 세트별 e1RM. 비우면 가장 최근 완료 세션.")
    def get_session(date: str = "") -> str:
        return run(service.get_session, date or None)

    @mcp.tool(annotations=READ, description="종목별 최근 세트 기록 (보조운동 무게 정할 때).")
    def exercise_history(exercise_id: str, limit: int = 30) -> str:
        return run(service.exercise_history, exercise_id, limit)

    @mcp.tool(annotations=READ, description="e1RM 추이와 현재 추정치(최근 21일 중앙값, 최고). lift를 비우면 3대 전체와 합계.")
    def e1rm(lift: str = "", all_history: bool = False) -> str:
        return run(service.e1rm, lift or None, all_history)

    @mcp.tool(annotations=READ, description="RTS 차트로 처방 무게 계산: e1RM, 횟수, RPE → 증량 단위(step)로 반올림한 무게.")
    def prescribe(e1rm_kg: float, reps: float, rpe: float, step: float = 5) -> str:
        return run(service.prescribe, e1rm_kg, reps, rpe, step)

    @mcp.tool(annotations=READ, description="근육별 주간 세트(주동근 1.0 + 보조근 0.5). weeks로 추이, end로 기준일(YYYY-MM-DD).")
    def weekly_volume(weeks: int = 1, end: str = "", min_rpe: float = 0) -> str:
        return run(service.weekly_volume, weeks, end or None, min_rpe)

    # ---------------------------------------------------------------- resources
    for rel in service.knowledge_files():
        def _make(rel=rel):
            @mcp.resource(f"deukgeuni://knowledge/{rel}", name=rel, mime_type="text/markdown",
                          description="득근이 지식 문서")
            def _res() -> str:
                return service.read_knowledge(rel)
        _make()

    @mcp.resource("deukgeuni://me/state", name="내 코칭 상태", mime_type="text/markdown",
                  description="프로필 + 상태 + 현재 블록")
    def my_state() -> str:
        return run(service.coaching_state)

    # ---------------------------------------------------------------- prompts
    def _skill_prompt(skill: str, ask: str) -> str:
        return (f"득근이로 행동해. 먼저 get_coaching_state로 내 상태를 읽고, read_knowledge('skills/{skill}/SKILL.md') 절차를 따라. "
                f"수치는 도구로 계산해.\n\n{ask}")

    @mcp.prompt(name="start", title="득근이 시작", description="득근이 지침 전체와 내 상태로 대화를 시작")
    def start() -> str:
        return ("아래 지침대로 득근이가 되어 줘. 그다음 get_coaching_state로 내 상태를 읽고, 다음 세션을 짧게 알려 줘.\n\n"
                + service.read_knowledge("AGENTS.md"))

    @mcp.prompt(name="today", title="오늘 루틴", description="오늘 처방과 워밍업")
    def today() -> str:
        return _skill_prompt("daily-session", "오늘 루틴 알려 줘.")

    @mcp.prompt(name="log", title="기록하기", description="방금 한 운동 기록")
    def log(workout: str) -> str:
        return _skill_prompt("log-workout", f"이거 기록해 줘: {workout}")

    @mcp.prompt(name="weekly_review", title="주간 점검", description="이번 주 진행·피로 신호·다음 주 조정")
    def weekly_review() -> str:
        return _skill_prompt("weekly-review", "이번 주 점검해 줘.")

    @mcp.prompt(name="reschedule", title="일정 조정", description="빠진 날 재배치")
    def reschedule(situation: str) -> str:
        return _skill_prompt("reschedule", f"일정 조정해 줘: {situation}")

    # ---------------------------------------------------------------- login page + health
    mcp.custom_route("/login", methods=["GET", "POST"], name="login")(login_routes(provider))

    @mcp.custom_route("/healthz", methods=["GET"], name="healthz")
    async def healthz(request):
        from starlette.responses import PlainTextResponse
        return PlainTextResponse("ok")

    return mcp


def asgi_app(mcp: MCPServer, cfg: dict):
    host = cfg["public_url"].split("://", 1)[1].split("/", 1)[0]
    sec = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                    allowed_hosts=[host, "127.0.0.1:*", "localhost:*"] + cfg.get("extra_hosts", []),
                                    allowed_origins=[cfg["public_url"].rstrip("/"), "http://127.0.0.1:*", "http://localhost:*"])
    return mcp.streamable_http_app(streamable_http_path=cfg.get("mcp_path", "/mcp"), transport_security=sec,
                                   host=cfg.get("listen_host", "127.0.0.1"))
