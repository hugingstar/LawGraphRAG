# LawGraphRAG Ops — 인프라 통합 모니터링 + 3D 그래프

기존 웹 앱(`app/`)과 **완전히 분리된** 스택이다. 앱을 내려도 이건 계속 뜬다.

```bash
cp .env.ops.example .env.ops   # 값을 채운다 (특히 비밀번호)
docker compose -f docker-compose.ops.yml up -d --build
```

| 화면 | 주소 | 내용 |
|---|---|---|
| ops 대시보드 | http://localhost:8900 | 웹·Postgres·Neo4j·Docker 통합 상태 |
| 3D 그래프 | http://localhost:8900/graph | 법령 그래프 WebGL 탐색 |
| Prometheus | http://localhost:9091 | 원시 지표·알림 규칙 (호스트 9090 은 이미 점유 중이라 9091) |
| Alertmanager | http://localhost:9093 | 발화된 알림, 라우팅·억제 상태, 침묵(silence) 설정 |
| Grafana | http://localhost:3001 | 시계열 대시보드 (익명 조회 허용) |
| 메트릭 원본 | http://localhost:8900/metrics | Prometheus 노출 포맷 |

모든 포트는 `127.0.0.1` 에만 바인딩된다. 외부에서 직접 열 수 없고, 원격에서 보려면
SSH 터널을 쓴다:

```bash
ssh -L 8900:localhost:8900 -L 3001:localhost:3001 사용자@서버
```

## 왜 이렇게 분리했나

- **`app/` 를 import 하지 않는다.** 설정·DB 접속을 `ops/` 안에서 따로 들고 있다.
  앱 모듈을 끌어오는 순간, 앱이 깨졌을 때 모니터링도 같이 죽는다.
- **`depends_on` 이 없다.** 감시 대상이 죽어 있을 때야말로 이게 떠 있어야 한다.
- **앱 compose 네트워크에 붙지 않는다.** `host.docker.internal` 로 호스트가 공개한
  포트(8000/5433/7687)를 본다. 앱 compose 프로젝트 이름이 바뀌어도 안 깨진다.
- **의존성 파일이 다르다.** `requirements-ops.txt` 는 torch·sentence-transformers를
  포함하지 않아 이미지가 가볍고 빌드가 빠르다.

## 계정

ops 는 **전용 계정**으로 붙는다. 앱 계정을 재사용하지 않는다.

```bash
docker exec -i lawgraphrag-db-1 psql -U <관리자> -d <DB> -v ro_password=<비밀번호> -v db_name=<DB> -f - < ops/create_readonly_role.sql
```

`lawowly_ro` 는 `SELECT` + `pg_monitor` 만 가진다. INSERT/UPDATE/DELETE/CREATE 는
전부 거부된다(스크립트 마지막 SELECT 가 0행이면 정상). `pg_monitor` 는 통계 뷰
열람 권한일 뿐이며, 이게 없으면 `pg_stat_activity` 에서 자기 커넥션만 보여
"연결 수" 지표가 항상 1 로 나온다.

**Neo4j 는 진짜 읽기 전용을 만들 수 없다.** Community 에디션은 사용자 생성은
되지만 RBAC(권한 제한)는 Enterprise 전용이라, `SHOW ROLES` 부터 거부된다.
그래서 `ops_ro` 계정은 자격증명 분리(앱과 다른 비밀번호, 독립적으로 폐기 가능)
까지만 제공하고 권한은 앱 계정과 동일하다.

```cypher
CREATE USER ops_ro IF NOT EXISTS SET PASSWORD '...' CHANGE NOT REQUIRED
```

## 수집하는 것

### 인프라 (서버가 살아 있는가)

| 대상 | 방식 | 주요 지표 |
|---|---|---|
| 웹 | HTTP GET (블랙박스) | 상태 코드, 응답 시간. 302/401 은 정상 기동으로 본다 |
| Postgres | `psycopg` 직접 조회 | DB 크기, 연결 수/상한, 캐시 적중률, 장기 실행 쿼리, 데드락, 테이블별 행 수, pgvector 설치 여부 |
| Neo4j | Bolt 드라이버 | 버전/에디션, 노드·관계 총계, 라벨별·타입별 분포 |
| Docker | `/var/run/docker.sock` (읽기 전용) | 컨테이너 상태, healthcheck, 재시작 횟수 |

### 서비스 활동 (사람들이 실제로 쓰고 있는가)

블랙박스 점검만으로는 "서버는 200을 주는데 아무도 안 쓴다"를 구분할 수 없다.
그래서 두 경로를 추가로 본다. **둘 다 `app/` 을 건드리지 않는다.**

| 출처 | 파일 | 얻는 것 |
|---|---|---|
| Postgres 업무 테이블 | [activity.py](activity.py) | 신청(`incidents`) 1시간/24시간/7일 건수, 상태별 분포, 처리 이벤트·코멘트, 사용자 수, 유효 세션, 24시간 내 로그인, 최근 접수 10건과 처리 타임라인 10건 (**누가** 신청했고 **누가** 상태를 바꿨는지) |
| 앱 컨테이너 액세스 로그 | [access_log.py](access_log.py) | 경로·메서드·상태코드별 누적 요청 수, 최근 5분/1시간 요청량, 4xx/5xx 건수 (**어떤 페이지**가 호출됐는지) |

경로의 숫자 세그먼트는 `{id}` 로 접는다 — `/api/incidents/12/comments` 가 12, 13,
14 … 로 갈라지면 카디널리티가 폭발하고 어느 화면이 바쁜지 안 보인다.

**개인정보는 담지 않는다.** `incidents` 의 `reporter_name`·`reporter_contact`·
`reporter_info` 는 신고자 개인정보라 모니터링 화면에 올릴 이유가 없어 쿼리에서
제외했다. 행위자 식별은 운영자 계정(`username`)까지만 한다.

**한계 두 가지.** uvicorn 기본 액세스 로그에는 응답 시간과 사용자 식별자가 없다.
그래서 경로별 지연 시간(p95 등)은 안 나오고, "이 사용자가 이 페이지를 봤다"는
연결도 안 된다. 그게 필요하면 앱에 미들웨어를 넣어야 하고, 그 순간 모니터링이
앱 배포 주기에 묶인다 — 지금은 의도적으로 그 선을 넘지 않았다.

또 대시보드의 "테이블 행(추정)"은 `pg_stat_user_tables.n_live_tup` 이라 통계
수집 시점에 따라 실제와 다르다(예: users 추정 2 vs 실제 9). 정확한 값이 필요한
지표는 활동 카드 쪽의 실제 `count(*)` 를 봐야 한다.

### Neo4j Community 관련 주의

Neo4j 는 **Enterprise 에서만** Prometheus 메트릭 엔드포인트를 제공한다.
현재 `docker-compose.yml` 은 `neo4j:5-community` 를 쓰므로, 그래프 지표는
ops 서비스가 Cypher 로 직접 조회해 `/metrics` 로 내보내는 경로가 유일하다.
라벨별 카운트는 전부 카운트 스토어를 읽는 O(1) 쿼리라 그래프가 커져도
스캔 비용이 늘지 않는다.

폴링은 백그라운드 태스크 한 곳에서만 돈다(`POLL_INTERVAL_SECONDS`, 기본 30초).
대시보드를 여러 개 열어도 감시 대상에 가는 부하는 그대로다.

시계열은 두 벌 남는다 — Prometheus(30일, 상세) 와 ops 자체 SQLite
(`HISTORY_RETENTION_DAYS`, 기본 7일, 스파크라인용). Prometheus 가 죽어도
ops 대시보드는 최근 추이를 계속 그린다.

## 3D 그래프

개요 화면은 **법령(Law) 노드 + 법령 간 인용**이다. 노드를 클릭하면 그 아래를
붙여 넣는다.

- **Law 클릭** → 조문(Article) + 조문 간 `REFERENCES` + 조문이 가리키는 `Entity`
- **Article 클릭** → 소속 법령 + 나가는/들어오는 인용 + `Entity`
- **Domain 클릭** → 소속 법령
- **Entity 클릭** → 그 개체를 참조하는 조문들
- **검색창** → 법령명으로 찾아 해당 서브그래프로 이동 (개요 상한에 안 걸린 법령도 가능)

한 번에 전부 그리지 않는 이유는 조문 수 때문이다. 법령 하나당 수백 개라
전체를 던지면 WebGL 이 상호작용 불가 상태가 된다. 개요 상한은
`GRAPH_MAX_NODES` (기본 1500) 로 조정한다.

### 왜 Domain 계층이 개요의 중심이 아닌가

`Domain-[:CONTAINS]->Law` 로 층을 나누는 게 원래 설계였는데, 현재 그래프에는
**법령 5380개 중 Domain 에 연결된 것이 1개뿐**이다(`CONTAINS` 관계 1개).
그대로 두면 개요가 연결선 없는 점 1500개가 된다.

그래서 조문 단위 `REFERENCES` 를 법령 단위로 집계해(`CITES`, 간선 굵기 = 인용
횟수 로그) 매크로 뷰의 골격으로 삼았다. "어느 법이 어느 법을 얼마나 인용하는가"는
그 자체로 의미 있는 구조이고, 카운트 집계는 0.4초면 끝난다.
`graph_ingest` 가 Domain 연결을 제대로 채우면 Domain 노드도 자동으로 같이 뜬다.

렌더러는 `3d-force-graph`(three.js 번들)다. 이미지 빌드 시 `ops/static/vendor/`
로 내려받아 폐쇄망에서도 뜨고, 다운로드가 막혔으면 브라우저가 CDN 으로 폴백한다.

## Grafana 대시보드

프로비저닝된 대시보드 2개가 기동 시 자동으로 올라온다 (수동 설정 불필요).

| 대시보드 | 패널 |
|---|---|
| **인프라 개요** (`lawgraphrag-overview`) | 구성요소 상태, 그래프 규모, DB 크기/연결, 점검 응답시간, 라벨별 노드 추이, 연결 수 vs 상한, 테이블별 행 수, 컨테이너 상태, 캐시 적중률 |
| **서비스 활동** (`lawgraphrag-activity`) | 신청 건수(1h/24h/누적), 상태별 분포 도넛, 신청 누적 추이, 사용자/세션, 활성 세션 추이, 처리 활동, 경로별 요청률(req/s), 상태코드별 응답률, 최다 호출 경로, 4xx/5xx 상세 테이블 |

데이터소스 uid 는 `lawgraphrag-prom` 으로 고정돼 있다. 고정하지 않으면 Grafana
볼륨을 지울 때마다 uid 가 바뀌어 패널이 전부 "datasource not found" 가 된다.

## 알림

`ops/prometheus/rules.yml` 에 10개 규칙이 있고, 각 규칙에 `severity` 라벨
(critical / warning / info)이 붙어 있다. 구성요소 다운, 폴러 정지, 연결 수
80% 초과, 캐시 적중률 저하, 장기 실행 쿼리, 그래프 적재 정체, 컨테이너 비정상,
웹 지연, 5xx 급증, 신청 적체.

발화된 알림은 **Alertmanager**([alertmanager.yml](alertmanager/alertmanager.yml))로
넘어가 `severity` 로 갈라진다.

| severity | 채널 | 재촉 주기 |
|---|---|---|
| critical | `slack-critical` | 그룹 대기 10초, 1시간마다 재발송 |
| warning / info | `slack-warning` | 6시간 / 24시간마다 재발송 |

**억제(inhibition) 규칙도 있다.** 예를 들어 Postgres 가 통째로 죽으면
(`ComponentDown{component="postgres"}`) 그 위에서 파생되는
`PostgresConnectionsHigh`·`LongRunningQueries` 같은 경보는 같이 울리지 않는다.
원인 하나에 알림 여러 개가 동시에 오는 걸 막는다.

### Slack 연결

`.env.ops` 의 `SLACK_WEBHOOK_URL` 에 Incoming Webhook URL을 넣고
`docker compose -f docker-compose.ops.yml up -d alertmanager` 로 재기동하면 된다.
비어 있으면 라우팅·억제 규칙은 정상 평가되지만 Slack 전송만 실패한다 —
Alertmanager 는 실패해도 재시작 루프에 빠지지 않고 계속 뜬 채로 있는다
(`entrypoint.sh` 가 빈 값일 때 문법적으로 유효한 자리표시자로 채운다).

검증 방법:

```bash
docker exec lawgraphrag-ops-alertmanager-1 amtool check-config //tmp/alertmanager.yml
docker exec lawgraphrag-ops-alertmanager-1 amtool config routes test \
  --config.file=//tmp/alertmanager.yml severity=critical alertname=ComponentDown
```

(Git Bash 에서 `/tmp/...` 가 호스트 경로로 잘못 치환되는 걸 피하려 `//tmp` 로 쓴다.)

알림을 실제로 **받으려면** Alertmanager 를 붙여야 한다. 지금은 규칙 평가까지만
한다 — 화면을 안 보고 있으면 알림도 없다.

## 로컬에서(도커 없이) 실행

```bash
pip install -r requirements-ops.txt
```

`.env.ops` 에서 호스트를 `localhost` 로 바꾸고 `HISTORY_PATH` 를 쓰기 가능한
경로로 지정한 뒤:

```bash
uvicorn ops.main:app --port 8900 --reload
```
