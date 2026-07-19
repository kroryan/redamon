"""
Pure helpers for the capture-proxy addon: header normalization, cheap passive
detections, body inline/offload decisions, and spool-record shaping.

Kept free of any mitmproxy import so it is unit-testable on its own; the addon
adapts a live flow into these plain inputs.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple

SECURITY_HEADERS = (
    "content-security-policy",
    "strict-transport-security",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
)

AUTH_REQUEST_HEADERS = ("authorization", "cookie", "x-api-key", "x-auth-token")


def normalize_headers(items) -> Dict[str, Any]:
    """Lowercase header names; collapse duplicates (e.g. Set-Cookie) to a list.

    `items` is an iterable of (name, value) pairs — mitmproxy's Headers yields
    exactly that, preserving duplicates, which is why we take pairs rather than
    a dict.
    """
    out: Dict[str, Any] = {}
    for name, value in items:
        key = str(name).strip().lower()
        if not key:
            continue
        if key in out:
            existing = out[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                out[key] = [existing, value]
        else:
            out[key] = value
    return out


def cookie_flag_issues(set_cookie) -> List[Dict[str, Any]]:
    issues = []
    values = set_cookie if isinstance(set_cookie, list) else [set_cookie]
    for cookie in values:
        if not cookie:
            continue
        low = str(cookie).lower()
        missing = [flag for flag, tok in
                   (("HttpOnly", "httponly"), ("Secure", "secure"), ("SameSite", "samesite"))
                   if tok not in low]
        if missing:
            name = str(cookie).split("=", 1)[0].strip()[:64]
            issues.append({"cookie": name, "missing": missing})
    return issues


def reflected_params(query: str, req_body: Optional[str], resp_body: Optional[str]) -> bool:
    """Cheap reflected-input signal: any non-trivial query/body param value that
    appears verbatim in the response body (an XSS/SSTI/open-redirect lead)."""
    if not resp_body:
        return False
    haystack = resp_body
    from urllib.parse import parse_qsl
    candidates = []
    if query:
        candidates += [v for _, v in parse_qsl(query.lstrip("?"))]
    if req_body:
        try:
            candidates += [v for _, v in parse_qsl(req_body)]
        except (ValueError, TypeError):
            pass
    for v in candidates:
        if v and len(v) >= 4 and v in haystack:
            return True
    return False


def passive_signals(req_headers: Dict[str, Any], resp_headers: Dict[str, Any],
                    query: str, req_body: Optional[str], resp_body: Optional[str]) -> Dict[str, Any]:
    set_cookie = resp_headers.get("set-cookie")
    return {
        "hasSetCookie": bool(set_cookie),
        "hadAuth": any(h in req_headers for h in AUTH_REQUEST_HEADERS),
        "reflectedParams": reflected_params(query, req_body, resp_body),
        "securityHeadersMissing": [h for h in SECURITY_HEADERS if h not in resp_headers],
        "cookieFlagIssues": cookie_flag_issues(set_cookie) if set_cookie else [],
    }


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decide_body(raw: Optional[bytes], max_inline_bytes: int, store_bodies: bool,
                is_text: bool) -> Tuple[Optional[str], Optional[str], int, Optional[str]]:
    """
    Decide inline vs offload for one body.

    Returns (inline_text, body_ref_sha, size, sha). Rules (plan §6.2):
      - store_bodies off -> nothing stored (only size + sha kept)
      - binary content -> always offload
      - size <= cap and text -> inline
      - otherwise -> offload (ref = sha, caller writes bodies/<sha>)
    """
    if raw is None:
        return (None, None, 0, None)
    size = len(raw)
    sha = sha256_hex(raw) if size else None
    if not store_bodies or size == 0:
        return (None, None, size, sha)
    if is_text and size <= max_inline_bytes:
        try:
            return (raw.decode("utf-8", errors="replace"), None, size, sha)
        except Exception:
            pass
    # offload
    return (None, sha, size, sha)


def build_record(*, ctx_token: Optional[str], method: str, scheme: str, host: str,
                 port: int, path: str, query: str, req_headers: Dict[str, Any],
                 resp_headers: Dict[str, Any], status_code: Optional[int],
                 req_body_inline: Optional[str], req_body_ref: Optional[str], req_body_size: int,
                 req_body_sha: Optional[str], resp_body_inline: Optional[str],
                 resp_body_ref: Optional[str], resp_body_size: int, resp_body_sha: Optional[str],
                 http_version: Optional[str], is_tls: bool, tls_version: Optional[str],
                 target_ip: Optional[str], response_time_ms: Optional[int],
                 started_at: str, blocked: bool = False, in_scope: bool = True,
                 error_text: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the opaque spool record. `ctx_token` is carried VERBATIM — the
    proxy never decodes or verifies it (it holds no key); traffic-ingest does."""
    sig = passive_signals(req_headers, resp_headers, query, req_body_inline, resp_body_inline)
    return {
        "ctx_token": ctx_token,
        "method": method, "scheme": scheme, "host": host, "port": port,
        "path": path, "query": query or None,
        "reqHeaders": req_headers, "respHeaders": resp_headers,
        "statusCode": status_code,
        "reqBody": req_body_inline, "reqBodyRef": req_body_ref,
        "reqBodySize": req_body_size, "reqBodySha": req_body_sha,
        "reqContentType": req_headers.get("content-type"),
        "respBody": resp_body_inline, "respBodyRef": resp_body_ref,
        "respBodySize": resp_body_size, "respBodySha": resp_body_sha,
        "respContentType": resp_headers.get("content-type"),
        "httpVersion": http_version, "isTls": is_tls, "tlsVersion": tls_version,
        "targetIp": target_ip, "responseTimeMs": response_time_ms,
        "blocked": blocked, "inScope": in_scope, "errorText": error_text,
        "startedAt": started_at,
        **sig,
    }
