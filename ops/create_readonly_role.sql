-- 모니터링 전용 읽기 계정.
--
--   docker exec -i lawgraphrag-db-1 psql -U <관리자> -d <DB> \
--     -v ro_password=<비밀번호> -v db_name=<DB> -f - < ops/create_readonly_role.sql
--
-- 왜 별도 계정인가: ops 는 DB 를 읽기만 한다. 앱 계정을 그대로 쓰면 모니터링
-- 프로세스가 실수로든 침해로든 쓰기를 할 수 있고, 앱 비밀번호를 바꿀 때마다
-- 모니터링이 같이 끊긴다.
--
-- psql 변수(:ro_password)는 $$ 달러 인용 블록 안에서 치환되지 않는다. 그래서
-- DO 블록 대신 \gexec 로 "SQL 을 만들어 실행"하는 방식을 쓴다.

-- 없으면 만든다 (있으면 아래 SELECT 가 0행이라 아무 일도 일어나지 않는다)
SELECT 'CREATE ROLE lawowly_ro LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lawowly_ro')
\gexec

-- 비밀번호는 항상 갱신한다 (%L 이 따옴표·이스케이프를 처리한다)
SELECT format('ALTER ROLE lawowly_ro LOGIN PASSWORD %L', :'ro_password')
\gexec

-- 접속과 스키마 탐색
GRANT CONNECT ON DATABASE :"db_name" TO lawowly_ro;
GRANT USAGE ON SCHEMA public TO lawowly_ro;

-- 현재 있는 테이블 + 앞으로 만들어질 테이블 모두 SELECT 만
GRANT SELECT ON ALL TABLES IN SCHEMA public TO lawowly_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO lawowly_ro;

-- pg_stat_activity 에서 다른 사용자의 세션까지 보려면 필요하다. 이게 없으면
-- 자기 자신의 커넥션만 보여서 "연결 수" 지표가 항상 1 로 나온다.
-- pg_monitor 는 통계 뷰 열람 권한일 뿐, 데이터 쓰기 권한이 아니다.
GRANT pg_monitor TO lawowly_ro;

-- 확인: SELECT 이외의 권한이 있으면 안 된다 (0행이 정상)
SELECT table_name, privilege_type
FROM information_schema.role_table_grants
WHERE grantee = 'lawowly_ro' AND privilege_type <> 'SELECT';
