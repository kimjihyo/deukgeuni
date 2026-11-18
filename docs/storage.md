# 저장 방식

데이터 모양(`docs/data-format.md`)은 하나이고, 어디에 저장할지는 사용자가 고른다.
에이전트와 사람 모두 **`scripts/wl.py` 명령으로만** 읽고 쓰므로, 저장 방식을 바꿔도 사용법은 같다.

```
 대화 / 에이전트
       │  wl.py set add ... / query / e1rm / check
       ▼
   Store  ── 검사(model.validate) 통과해야 저장
       │  load(table) / save(table, rows)
       ▼
 Backend:  csv │ jsonl │ sqlite │ mongodb(다중 사용자) │ (직접 추가: postgres, sheets …)
```

## 고르기

데이터 폴더의 `config.toml` (데이터 폴더 찾는 순서: `WL_DATA` → `.wl-data` 파일 → `./data`):

```toml
[backend]
type = "csv"          # csv | jsonl | sqlite
# file = "workout.db" # sqlite 파일 이름 (data 폴더 기준, 기본 workout.db)
```

일회성으로 바꾸려면 `WL_BACKEND=sqlite python3 scripts/wl.py ...` 또는 `--backend sqlite`.
데이터 폴더 자체를 바꾸려면 `WL_DATA=/경로`.

| 방식 | 장점 | 단점 | 추천 |
| --- | --- | --- | --- |
| `csv` (기본) | 사람이 읽기 쉬움, 엑셀로 열림, git diff 깔끔 | 타입 정보 없음 | git으로 관리할 때 |
| `jsonl` | 타입 보존, git diff 깔끔, `mongoimport`로 바로 MongoDB 이전 | 표로 보기 불편 | 다른 도구로 넘길 때 |
| `sqlite` | SQL로 바로 분석 (`sqlite3 <데이터 폴더>/workout.db`), 서버 없음 | 바이너리라 git diff 안 보임 | 로컬 분석용 |
| `mongodb` | 여러 사용자·봇·앱에서 동시 접근, 사용자별 분리(`user_id`) | 서버·접속 정보 필요(`pip install pymongo`). **클라우드 AI 세션은 외부 DB 접속이 막혀 있을 수 있음** | 메신저 봇 (`docs/deploy-telegram.md`) |

추가 설치 없이 되는 것: `csv`, `jsonl`, `sqlite` (파이썬 표준 라이브러리).

## 바꾸기 (이전)

공통 형식(JSON Lines, 한 줄 = `{"table": ..., 필드...}`)을 거친다.

```bash
python3 scripts/wl.py export all.jsonl                       # 현재 저장소 → 파일
WL_BACKEND=sqlite python3 scripts/wl.py import all.jsonl --yes  # 파일 → 새 저장소 (덮어씀)
WL_BACKEND=sqlite python3 scripts/wl.py check
# 확인되면 데이터 폴더 config.toml의 type을 바꾼다
```

`import`는 전체를 검사한 뒤에만 쓰고, 검사에 실패하면 아무것도 바꾸지 않는다.

## 새 저장 방식 추가하기

백엔드는 테이블 전체를 읽고(`load`) 쓰는(`save`) 두 메서드만 구현하면 된다. 추가·수정·삭제·검사는 `Store`가 메모리에서 처리한다 (데이터가 수천 행 수준이라 충분).

1. `scripts/wlcore/backends/<이름>.py`에 `Backend`를 상속한 클래스 작성
2. `scripts/wlcore/backends/__init__.py`의 `REGISTRY`에 `"이름": "wlcore.backends.<이름>:클래스"` 추가
3. 위 "바꾸기" 절차로 데이터를 넣고 `check`

규칙:
- `load`는 테이블이 없으면 `[]`. 값은 문자열이어도 된다 (`model.coerce`가 변환)
- `save`는 받은 행으로 테이블 전체를 **원자적으로** 교체 (트랜잭션 또는 임시 파일 후 교체)
- 접속 정보(비밀번호 등)는 `config.toml`에 쓰지 말고 환경변수로. `config.toml`의 `[backend]` 다른 키는 `options`로 전달된다
- 외부 드라이버는 그 모듈 안에서만 import (다른 사용자는 설치 안 해도 되게)

### 예: PostgreSQL (스케치, 미검증)

```python
# scripts/wlcore/backends/postgres.py   — pip install psycopg
import os
import psycopg
from ..model import INT, NUM
from .base import Backend

class PostgresBackend(Backend):
    name = "postgres"
    def __init__(self, options):
        super().__init__(options)
        self.conn = psycopg.connect(os.environ["WL_PG_DSN"])
    def _ensure(self, t):
        typ = {INT: "integer", NUM: "double precision"}
        cols = ", ".join(f'"{f.name}" {typ.get(f.type, "text")}' for f in t.fields)
        self.conn.execute(f'create table if not exists "{t.name}" ({cols}, primary key ({", ".join(t.key)}))')
    def load(self, t):
        self._ensure(t)
        cur = self.conn.execute(f'select * from "{t.name}"')
        names = [d.name for d in cur.description]
        return [dict(zip(names, r)) for r in cur]
    def save(self, t, rows):
        self._ensure(t)
        with self.conn.transaction():
            self.conn.execute(f'delete from "{t.name}"')
            cols = t.columns
            with self.conn.cursor() as c:
                c.executemany(f'insert into "{t.name}" ({", ".join(cols)}) values ({", ".join(["%s"] * len(cols))})',
                              [[r[k] for k in cols] for r in rows])
    def close(self):
        self.conn.close()
```

### MongoDB (구현됨: `scripts/wlcore/backends/mongodb.py`)

여러 사용자를 한 DB에 둔다. 모든 문서에 `user_id`가 있고, Store는 자기 사용자 것만 본다.
기록(`exercises`, `sessions`, `sets`)과 사용자 문서(`docs` 컬렉션: profile.md 등)를 함께 저장한다.

```toml
# 데이터 폴더 config.toml (한 사용자로 CLI를 쓸 때)
[backend]
type = "mongodb"
user = "tg:123456789"
database = "deukgeuni"
# 접속 문자열은 파일에 쓰지 않고 환경변수 WL_MONGO_URI
```

봇은 사용자마다 `Store(config={"type": "mongodb", "user": ..., "client": ...})`를 만든다 (`bot/app.py`).
테이블 쓰기는 서버가 지원하면 트랜잭션으로 묶는다 (Atlas 지원).

등록:
```python
REGISTRY["postgres"] = "wlcore.backends.postgres:PostgresBackend"
```

DB 백엔드는 데이터 폴더가 없으므로 사용자 문서용 `load_doc` / `save_doc` / `list_docs`도 구현한다 (MongoDB 백엔드 참고).
