-- sayday DB 부트스트랩 (클러스터 레벨, 1회 실행 — superuser)
-- 로컬: /opt/homebrew/opt/postgresql@15/bin/psql -d postgres -f scripts/db_bootstrap.sql
-- 프로덕션(RDS): 비밀번호는 Secrets Manager 값으로 교체 후 실행

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sayday_app') THEN
    CREATE ROLE sayday_app LOGIN PASSWORD 'sayday_app';       -- RLS 적용 (앱 경로)
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sayday_admin') THEN
    CREATE ROLE sayday_admin LOGIN PASSWORD 'sayday_admin' BYPASSRLS;  -- carrot/worker/마이그레이션
  END IF;
END $$;

-- 데이터베이스 (없으면): CREATE DATABASE sayday OWNER sayday_admin;
