# redamon-capture-proxy — one image, two roles (mitmdump proxy OR ingest worker).
# The role is selected by the compose `command:` + env + network, not the image,
# so isolation comes from placement (proxy: pentest-net, no DB creds; ingest:
# redamon-network, scoped INSERT-only role), per plan §15.2 / §15.10.
FROM python:3.12-slim

# Pinned deps (§15.10: the proxy parses untrusted target responses — keep patched).
# mitmproxy for the capture proxy; psycopg3 (already the stack's PG driver) for ingest.
RUN pip install --no-cache-dir "mitmproxy~=11.1" "psycopg[binary]~=3.2"

# Non-root, least-privilege user (§15.10). Spool + bodies volumes are the only
# writable paths at runtime (compose mounts them; root fs is read-only).
RUN useradd -m -u 10001 -s /usr/sbin/nologin capture

WORKDIR /app
COPY capture_proxy/redamon_ctx.py \
     capture_proxy/egress.py \
     capture_proxy/capture_lib.py \
     capture_proxy/capture_addon.py \
     capture_proxy/ingest_worker.py \
     /app/
# Bundle the hard-guardrail so the proxy can enforce it WITHOUT any secret or
# network access (static domain blocklist). Kept in sync with the other copies.
COPY recon_orchestrator/hard_guardrail.py /app/hard_guardrail.py

USER capture

# Default command is the capture proxy; the ingest service overrides it in compose.
# --listen-port is provided at runtime; --set stream_large_bodies bounds memory.
CMD ["mitmdump", "--quiet", "--set", "stream_large_bodies=5m", \
     "--listen-port", "8888", "-s", "/app/capture_addon.py"]
