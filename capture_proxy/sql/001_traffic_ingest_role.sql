-- Scoped INSERT-only Postgres role for the traffic-ingest worker (plan §15.2).
--
-- The ingest worker is the ONLY capture component that holds a DB credential,
-- and this role can do exactly one thing: INSERT into captured_http_transactions.
-- No SELECT (cannot read any tenant's data), no other tables, no DDL. So even a
-- fully compromised ingest worker can only append rows (INSERT-only, purgeable,
-- non-readable), never exfiltrate or tamper.
--
-- Apply once after `prisma db push` has created the table:
--   docker compose exec -T postgres psql -U redamon -d redamon \
--     -v role_password="'<strong-secret>'" -f - < capture_proxy/sql/001_traffic_ingest_role.sql
-- then set the ingest DSN:
--   TRAFFIC_INGEST_DATABASE_URL=postgresql://traffic_ingest:<secret>@postgres:5432/redamon

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'traffic_ingest') THEN
    -- Password is injected via psql -v role_password; falls back to a placeholder
    -- that MUST be changed (the deploy secret-strength gate should enforce this).
    EXECUTE format('CREATE ROLE traffic_ingest LOGIN PASSWORD %s', :'role_password');
  END IF;
END
$$;

-- Exactly one privilege on exactly one table.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM traffic_ingest;
GRANT INSERT ON TABLE captured_http_transactions TO traffic_ingest;
-- The role needs USAGE on the schema to reference the table at all.
GRANT USAGE ON SCHEMA public TO traffic_ingest;

-- Defensive: never let it read anything back.
REVOKE SELECT ON captured_http_transactions FROM traffic_ingest;
