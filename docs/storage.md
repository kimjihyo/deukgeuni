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
 Backend:  csv │ jsonl │ sqlite │ (직접 추가: postgres, mongodb, sheets …)
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
| 서버 DB (직접 추가) | 여러 기기·앱에서 동시 접근 | 서버·접속 정보 필요. **클라우드 AI 세션은 외부 DB 접속이 막혀 있을 수 있음** | 앱을 만들 때 |

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

### 예: MongoDB (스케치, 미검증)

```python
# scripts/wlcore/backends/mongodb.py    — pip install pymongo
import os
from pymongo import MongoClient
from .base import Backend

class MongoBackend(Backend):
    name = "mongodb"
    def __init__(self, options):
        super().__init__(options)
        self.client = MongoClient(os.environ["WL_MONGO_URI"])
        self.db = self.client[options.get("database", "workout")]
    def load(self, t):
        return list(self.db[t.name].find({}, {"_id": 0}))
    def save(self, t, rows):
        # 단일 노드에서는 트랜잭션이 없으니 임시 컬렉션에 쓰고 이름을 바꿔 교체
        tmp = self.db[f"{t.name}__tmp"]
        tmp.drop()
        if rows:
            tmp.insert_many([{k: v for k, v in r.items() if v is not None} for r in rows])
            tmp.rename(t.name, dropTarget=True)
        else:
            self.db[t.name].drop()
    def close(self):
        self.client.close()
```

등록:
```python
REGISTRY["postgres"] = "wlcore.backends.postgres:PostgresBackend"
REGISTRY["mongodb"] = "wlcore.backends.mongodb:MongoBackend"
```
