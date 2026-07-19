"""
redamon-capture-proxy — mitmdump addon (plan §11.1, §11.2).

The target-facing, CREDENTIAL-FREE capture component. It runs `mitmdump -s
capture_addon.py` on pentest-net. Responsibilities:

  request hook:
    - lift the opaque `X-Redamon-Ctx` tag off the request and DELETE the header
      so it never leaks to the target (§7.2). The proxy carries it verbatim; it
      holds NO signing key and never decodes/verifies it (that's traffic-ingest).
    - enforce the egress guard (§15.3, §20.5): resolve the host, refuse internal
      IPs / hard-guardrail domains, pin the resolved IP. Blocked requests get a
      403 and a `blocked=true` spool record; they are NOT forwarded.

  response hook:
    - assemble the transaction, apply body inline/offload + dedup, stamp cheap
      passive signals, and append it to the append-only spool as one
      atomically-renamed file (concurrency-safe under many coroutines).

  backpressure:
    - a bounded queue drained by a writer thread; if it backs up, drop-and-count
      (never block the proxy data path).

Everything the LLM/analyst eventually sees is stamped from the VERIFIED tag by
traffic-ingest, never from anything this proxy or a target controls.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone

from mitmproxy import http

from capture_lib import build_record, decide_body, normalize_headers, sha256_hex
from egress import check_egress

try:
    from hard_guardrail import is_hard_blocked  # bundled into the image
except Exception:  # pragma: no cover - guardrail must exist in the image
    def is_hard_blocked(domain):
        return (False, "")


def _hard_blocked(host: str) -> bool:
    blocked, _ = is_hard_blocked(host)
    return blocked


TEXT_CT_MARKERS = ("text", "json", "xml", "javascript", "html", "csv", "x-www-form-urlencoded")


def _is_text(content_type) -> bool:
    if not content_type:
        return True  # unknown -> treat as text (bounded by the size cap anyway)
    ct = str(content_type).lower()
    return any(m in ct for m in TEXT_CT_MARKERS)


class RedamonCapture:
    CTX_HEADER = "X-Redamon-Ctx"

    def __init__(self) -> None:
        self.spool_dir = os.environ.get("CAPTURE_SPOOL_DIR", "/spool")
        self.bodies_dir = os.environ.get("CAPTURE_BODIES_DIR", "/bodies")
        self.max_body_bytes = int(os.environ.get("CAPTURE_PROXY_MAX_BODY_KB", "64") or "64") * 1024
        self.store_bodies = os.environ.get("CAPTURE_PROXY_STORE_BODIES", "true").lower() != "false"
        self.extra_blocked_ips = [ip for ip in os.environ.get("CAPTURE_BLOCKED_IPS", "").split(",") if ip.strip()]
        self.tmp_dir = os.path.join(self.spool_dir, ".tmp")
        os.makedirs(self.tmp_dir, exist_ok=True)
        os.makedirs(self.bodies_dir, exist_ok=True)
        # The bodies store is shared with the webapp (different uid) which reads
        # + ref-counted-GCs blobs, so make it group/other writable. Internal
        # volume only; blobs are never served by raw path (§15.7).
        try:
            os.chmod(self.bodies_dir, 0o777)
        except OSError:
            pass

        self._q: "queue.Queue[dict]" = queue.Queue(maxsize=int(os.environ.get("CAPTURE_QUEUE_MAX", "2000")))
        self.dropped = 0
        self._writer = threading.Thread(target=self._drain, name="spool-writer", daemon=True)
        self._writer.start()

    # ---- mitmproxy hooks ---------------------------------------------------
    def request(self, flow: http.HTTPFlow) -> None:
        # Lift + strip the internal tag BEFORE anything can forward it upstream.
        token = flow.request.headers.pop(self.CTX_HEADER, None)
        flow.metadata["redamon_ctx"] = token
        flow.metadata["redamon_started"] = time.time()

        try:
            allowed, pinned_ip, reason = check_egress(
                flow.request.pretty_host, hard_blocked=_hard_blocked,
                extra_blocked_ips=self.extra_blocked_ips,
            )
        except Exception as e:  # fail CLOSED — never forward on a guard error
            allowed, pinned_ip, reason = (False, None, f"guard-error:{e}")

        if not allowed:
            # Refuse: do not forward. Record the attempt for the scope audit.
            print(f"[capture] BLOCKED {flow.request.pretty_host} ({reason})", flush=True)
            flow.metadata["redamon_blocked"] = reason
            flow.response = http.Response.make(
                403, b"blocked by redamon capture proxy egress guard\n",
                {"Content-Type": "text/plain"},
            )
            self._emit_blocked(flow, reason)
            return

        flow.metadata["redamon_pinned_ip"] = pinned_ip
        # Pin the upstream connection to the vetted IP so mitmproxy does NOT
        # re-resolve the hostname and get a rebound internal IP between the guard
        # check and the connection (DNS-rebinding TOCTOU, §20.5). We set the server
        # connection address ONLY (not request.host), so the Host header + TLS SNI
        # keep the original hostname and vhosts/HTTPS still work.
        if pinned_ip and pinned_ip != flow.request.host:
            try:
                flow.server_conn.address = (pinned_ip, flow.request.port)
            except Exception as e:
                print(f"[capture] pin failed for {flow.request.pretty_host}: {e}", flush=True)

    def response(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("redamon_blocked"):
            return  # already emitted in request hook
        try:
            self._emit(flow)
        except Exception as e:  # never break the proxy path
            print(f"[capture] emit failed: {e}", flush=True)

    # ---- internals ---------------------------------------------------------
    def _emit_blocked(self, flow: http.HTTPFlow, reason: str) -> None:
        req_headers = normalize_headers(flow.request.headers.items(multi=True))
        rec = build_record(
            ctx_token=flow.metadata.get("redamon_ctx"),
            method=flow.request.method, scheme=flow.request.scheme,
            host=flow.request.pretty_host, port=flow.request.port,
            path=flow.request.path.split("?", 1)[0],
            query=flow.request.path.split("?", 1)[1] if "?" in flow.request.path else "",
            req_headers=req_headers, resp_headers={}, status_code=None,
            req_body_inline=None, req_body_ref=None, req_body_size=0, req_body_sha=None,
            resp_body_inline=None, resp_body_ref=None, resp_body_size=0, resp_body_sha=None,
            http_version=None, is_tls=flow.request.scheme == "https", tls_version=None,
            target_ip=None, response_time_ms=None,
            started_at=datetime.now(timezone.utc).isoformat(),
            blocked=True, in_scope=False, error_text=f"egress:{reason}",
        )
        self._enqueue(rec)

    def _emit(self, flow: http.HTTPFlow) -> None:
        req = flow.request
        resp = flow.response
        req_headers = normalize_headers(req.headers.items(multi=True))
        resp_headers = normalize_headers(resp.headers.items(multi=True)) if resp else {}

        req_raw = req.raw_content if req and req.raw_content else None
        resp_raw = resp.raw_content if resp and resp.raw_content else None

        rb_inline, rb_ref, rb_size, rb_sha = decide_body(
            req_raw, self.max_body_bytes, self.store_bodies, _is_text(req_headers.get("content-type")))
        sb_inline, sb_ref, sb_size, sb_sha = decide_body(
            resp_raw, self.max_body_bytes, self.store_bodies, _is_text(resp_headers.get("content-type")))

        # Offload bodies to the content-addressed store (dedup by sha).
        if rb_ref and req_raw is not None:
            self._offload(rb_ref, req_raw)
        if sb_ref and resp_raw is not None:
            self._offload(sb_ref, resp_raw)

        started = flow.metadata.get("redamon_started")
        rt_ms = int((time.time() - started) * 1000) if started else None
        path = req.path.split("?", 1)[0]
        query = req.path.split("?", 1)[1] if "?" in req.path else ""

        rec = build_record(
            ctx_token=flow.metadata.get("redamon_ctx"),
            method=req.method, scheme=req.scheme, host=req.pretty_host, port=req.port,
            path=path, query=query, req_headers=req_headers, resp_headers=resp_headers,
            status_code=resp.status_code if resp else None,
            req_body_inline=rb_inline, req_body_ref=rb_ref, req_body_size=rb_size, req_body_sha=rb_sha,
            resp_body_inline=sb_inline, resp_body_ref=sb_ref, resp_body_size=sb_size, resp_body_sha=sb_sha,
            http_version=getattr(resp, "http_version", None) if resp else None,
            is_tls=req.scheme == "https", tls_version=None,
            target_ip=flow.metadata.get("redamon_pinned_ip"),
            response_time_ms=rt_ms, started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._enqueue(rec)

    def _offload(self, sha: str, raw: bytes) -> None:
        dest = os.path.join(self.bodies_dir, sha)
        if os.path.exists(dest):
            return  # dedup
        # The temp file MUST live in bodies_dir so os.replace is a SAME-FILESYSTEM
        # rename. /spool and /bodies are separate mounts, so staging the temp in
        # /spool/.tmp made os.replace raise EXDEV ("cross-device link") — which the
        # old bare `except OSError` swallowed silently, dropping every offloaded
        # blob while the DB ref was still written (dangling resp_body_ref rows).
        tmp = os.path.join(self.bodies_dir, f".tmp-{uuid.uuid4().hex}")
        try:
            with open(tmp, "wb") as f:
                f.write(raw)
            os.replace(tmp, dest)  # atomic within bodies_dir (same filesystem)
        except OSError as e:
            print(f"[capture] body offload failed (sha={sha[:12]}): {e}", flush=True)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _enqueue(self, rec: dict) -> None:
        try:
            self._q.put_nowait(rec)
        except queue.Full:
            self.dropped += 1
            if self.dropped % 100 == 1:
                print(f"[capture] spool queue full, dropped={self.dropped}", flush=True)

    def _drain(self) -> None:
        while True:
            rec = self._q.get()
            try:
                self._write_spool(rec)
            except Exception as e:
                print(f"[capture] spool write failed: {e}", flush=True)
            finally:
                self._q.task_done()

    def _write_spool(self, rec: dict) -> None:
        name = f"{time.time_ns()}-{uuid.uuid4().hex}.json"
        tmp = os.path.join(self.tmp_dir, name)
        final = os.path.join(self.spool_dir, name)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, final)  # atomic publish; ingest only ever sees complete files


addons = [RedamonCapture()]
