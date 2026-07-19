"""
Capture-proxy egress guard (plan §15.3, §20.5).

A new proxy is a new egress path, so it must not become an SSRF pivot into
RedAmon's internal network or a hard-guardrail bypass. This module is the pure,
testable core of that guard; the mitmdump addon calls it in the `request` hook
and blocks anything it refuses.

Two layers:
  1. Internal denylist on the RESOLVED IP (not just the hostname) — RFC1918,
     loopback, link-local, CGNAT, reserved, multicast, unspecified, plus any
     explicitly configured RedAmon service IPs. Checking the resolved IP defeats
     DNS-rebinding (an in-scope name pointing at 169.254.169.254 / 10.x).
  2. Static hard-guardrail on the hostname (.gov/.mil/.edu/.int + exact list),
     via an injected checker so the addon can wire in the bundled hard_guardrail
     module without this module depending on it.

Pure stdlib. The addon is responsible for pinning the resolved IP it gets back
here for the actual upstream connection so a TOCTOU re-resolve can't slip past.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable, List, Optional, Tuple

CGNAT = ipaddress.ip_network("100.64.0.0/10")


def is_internal_ip(ip_str: str, extra_blocked: Optional[List[str]] = None) -> bool:
    """True if `ip_str` must never be reached through the capture proxy."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable -> fail closed
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified
            or addr in CGNAT):
        return True
    if extra_blocked:
        for b in extra_blocked:
            try:
                if addr == ipaddress.ip_address(b):
                    return True
            except ValueError:
                continue
    return False


def resolve_host(host: str) -> List[str]:
    """Resolve a hostname to every A/AAAA address. Bare IPs pass through."""
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    out: List[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in out:
            out.append(ip)
    return out


def check_egress(
    host: str,
    hard_blocked: Optional[Callable[[str], bool]] = None,
    extra_blocked_ips: Optional[List[str]] = None,
) -> Tuple[bool, Optional[str], str]:
    """
    Decide whether the proxy may forward to `host`.

    Returns (allowed, pinned_ip, reason). When allowed, `pinned_ip` is the first
    resolved address and the caller MUST connect to exactly that IP (no
    re-resolution) to avoid a rebinding TOCTOU. When blocked, `pinned_ip` is None.
    """
    host = (host or "").strip().strip(".").lower()
    if not host:
        return (False, None, "empty host")

    # Static hard-guardrail on the name (.gov/.mil/... + exact list).
    if hard_blocked is not None:
        try:
            if hard_blocked(host):
                return (False, None, "hard-guardrail")
        except Exception:
            return (False, None, "hard-guardrail-error")  # fail closed

    resolved = resolve_host(host)
    if not resolved:
        return (False, None, "unresolvable")  # fail closed

    # Every resolved address must be routable-public; if ANY is internal we
    # refuse (a rebinding name that returns one public + one internal is hostile).
    for ip in resolved:
        if is_internal_ip(ip, extra_blocked_ips):
            return (False, None, f"internal-ip:{ip}")

    return (True, resolved[0], "ok")
