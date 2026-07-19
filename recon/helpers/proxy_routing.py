"""
Capture-proxy routing for recon tools (mitmproxy integration, Phase 1, plan §9).

When the per-project routing gate CAPTURE_PROXY_ENABLED is on AND the proxy is
reachable, recon HTTP tools route through the capture proxy and carry a signed
`X-Redamon-Ctx` tag so the ingest can attribute their traffic. Otherwise tools
run direct (fail-open, §20.1).

CRITICAL (§20.2, the tag-leak guard): the caller adds the `-H X-Redamon-Ctx`
header ONLY in the same branch where it adds the `-proxy` flag. On any direct
path the tag is never present, so our internal identifiers can never leak to the
target on a fallback.

Usage:
    from helpers import proxy_routing
    proxy_routing.configure(settings)                 # once, after settings fetch
    ...
    url, token = proxy_routing.get_capture_routing("katana")
    if url and token:
        cmd += ["-proxy", url, "-H", f"X-Redamon-Ctx: {token}"]

recon tools run with --net=host, so the proxy is reached on the host loopback
publish (127.0.0.1:<port>).
"""
import os
import socket

from helpers.redamon_ctx import sign_tag

# Module-level context: recon runs one scan per process, so a single configure()
# call at startup is the right lifetime.
_config = {"enabled": False, "port": 8888, "reachable": False}
_token_cache: dict = {}

_PROXY_HOST = "127.0.0.1"


def _proxy_port() -> int:
    try:
        return int(os.environ.get("CAPTURE_PROXY_PORT", "8888") or "8888")
    except (TypeError, ValueError):
        return 8888


def is_capture_proxy_reachable(host: str = _PROXY_HOST, port: int = None, timeout: float = 1.0) -> bool:
    """TCP-probe the proxy's loopback publish."""
    port = port or _proxy_port()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def configure(settings) -> None:
    """Read the per-project gate + probe reachability once. Idempotent."""
    port = _proxy_port()
    enabled = bool(settings and settings.get("CAPTURE_PROXY_ENABLED"))
    reachable = is_capture_proxy_reachable(port=port) if enabled else False
    _config.update(enabled=enabled, port=port, reachable=reachable)
    _token_cache.clear()
    if enabled and reachable:
        print(f"[*][capture] routing recon HTTP tools through proxy 127.0.0.1:{port}")
    elif enabled and not reachable:
        # §20.1: fail-open — surface the evidence gap but never break the scan.
        print(f"[!][capture] proxy enabled but unreachable on 127.0.0.1:{port} — recon runs DIRECT (capture degraded)")


def _run_id() -> str | None:
    return (
        os.environ.get("RECON_RUN_ID")
        or os.environ.get("PARTIAL_RECON_RUN_ID")
        or os.environ.get("AI_ATTACK_RUN_ID")
        or None
    )


def get_capture_routing(tool: str, phase: str = "informational"):
    """
    Return (proxy_url, ctx_token) when this tool should route through the capture
    proxy, else (None, None). The token is signed with SCANNER_API_KEY (§20.4) and
    cached per (tool, phase) since attribution is per-scan, not per-request.
    """
    if not (_config["enabled"] and _config["reachable"]):
        return (None, None)
    key = os.environ.get("SCANNER_API_KEY") or os.environ.get("INTERNAL_API_KEY", "")
    if not key:
        return (None, None)

    cache_key = (tool, phase)
    token = _token_cache.get(cache_key)
    if token is None:
        payload = {
            "source": "recon",
            "project_id": os.environ.get("PROJECT_ID", ""),
            "user_id": os.environ.get("USER_ID", ""),
            "run_id": _run_id(),
            "tool": tool,
            "phase": phase,
        }
        try:
            token = sign_tag(payload, key)
        except Exception:  # noqa: BLE001 — never break the scan
            return (None, None)
        _token_cache[cache_key] = token

    return (f"http://{_PROXY_HOST}:{_config['port']}", token)
