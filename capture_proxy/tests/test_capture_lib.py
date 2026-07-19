"""
Unit tests for the capture-proxy pure logic: egress guard, header normalization,
passive signals, body inline/offload, record shaping.

Run: python3 -m unittest capture_proxy.tests.test_capture_lib
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import egress  # noqa: E402
import capture_lib as cl  # noqa: E402


class TestEgress(unittest.TestCase):
    def test_internal_ips_blocked(self):
        for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1",
                   "169.254.169.254", "100.64.0.1", "::1", "0.0.0.0"):
            self.assertTrue(egress.is_internal_ip(ip), ip)

    def test_public_ips_allowed(self):
        for ip in ("8.8.8.8", "1.1.1.1", "93.184.216.34"):
            self.assertFalse(egress.is_internal_ip(ip), ip)

    def test_unparseable_ip_fails_closed(self):
        self.assertTrue(egress.is_internal_ip("not-an-ip"))

    def test_extra_blocked_service_ip(self):
        self.assertTrue(egress.is_internal_ip("8.8.8.8", extra_blocked=["8.8.8.8"]))

    def test_check_egress_public_ip_allowed(self):
        allowed, pinned, reason = egress.check_egress("8.8.8.8")
        self.assertTrue(allowed)
        self.assertEqual(pinned, "8.8.8.8")

    def test_check_egress_internal_ip_blocked(self):
        allowed, pinned, reason = egress.check_egress("127.0.0.1")
        self.assertFalse(allowed)
        self.assertIsNone(pinned)
        self.assertTrue(reason.startswith("internal-ip"))

    def test_check_egress_hard_guardrail(self):
        allowed, _, reason = egress.check_egress(
            "agency.gov", hard_blocked=lambda h: h.endswith(".gov"))
        self.assertFalse(allowed)
        self.assertEqual(reason, "hard-guardrail")

    def test_check_egress_empty_host(self):
        self.assertFalse(egress.check_egress("")[0])

    def test_hard_blocked_exception_fails_closed(self):
        def boom(_):
            raise RuntimeError("x")
        allowed, _, reason = egress.check_egress("1.1.1.1", hard_blocked=boom)
        self.assertFalse(allowed)


class TestHeaders(unittest.TestCase):
    def test_normalize_lowercases_and_collapses_duplicates(self):
        h = cl.normalize_headers([("Set-Cookie", "a=1"), ("set-cookie", "b=2"), ("Server", "nginx")])
        self.assertEqual(h["set-cookie"], ["a=1", "b=2"])
        self.assertEqual(h["server"], "nginx")

    def test_cookie_flag_issues(self):
        issues = cl.cookie_flag_issues(["sid=1", "j=2; HttpOnly; Secure; SameSite=Lax"])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["cookie"], "sid")


class TestPassiveSignals(unittest.TestCase):
    def test_signals(self):
        sig = cl.passive_signals(
            req_headers={"authorization": "Bearer x"},
            resp_headers={"set-cookie": "sid=1"},
            query="?q=INJECTME", req_body=None, resp_body="<p>INJECTME echoed</p>")
        self.assertTrue(sig["hasSetCookie"])
        self.assertTrue(sig["hadAuth"])
        self.assertTrue(sig["reflectedParams"])
        self.assertIn("content-security-policy", sig["securityHeadersMissing"])

    def test_no_reflection_short_values(self):
        sig = cl.passive_signals({}, {}, "?a=1", None, "1 appears everywhere")
        self.assertFalse(sig["reflectedParams"])  # len < 4


class TestDecideBody(unittest.TestCase):
    def test_small_text_inline(self):
        inline, ref, size, sha = cl.decide_body(b"hello", 1024, True, True)
        self.assertEqual(inline, "hello")
        self.assertIsNone(ref)
        self.assertEqual(size, 5)
        self.assertIsNotNone(sha)

    def test_large_text_offloaded(self):
        raw = b"x" * 5000
        inline, ref, size, sha = cl.decide_body(raw, 1024, True, True)
        self.assertIsNone(inline)
        self.assertEqual(ref, sha)
        self.assertEqual(size, 5000)

    def test_binary_always_offloaded(self):
        inline, ref, size, sha = cl.decide_body(b"\x00\x01\x02", 1024, True, False)
        self.assertIsNone(inline)
        self.assertEqual(ref, sha)

    def test_store_bodies_off(self):
        inline, ref, size, sha = cl.decide_body(b"hello", 1024, False, True)
        self.assertIsNone(inline)
        self.assertIsNone(ref)
        self.assertEqual(size, 5)      # size + sha still recorded
        self.assertIsNotNone(sha)

    def test_none_body(self):
        self.assertEqual(cl.decide_body(None, 1024, True, True), (None, None, 0, None))


class TestBuildRecord(unittest.TestCase):
    def test_ctx_token_carried_verbatim(self):
        rec = cl.build_record(
            ctx_token="TOKEN.SIG", method="GET", scheme="https", host="x.example",
            port=443, path="/", query="", req_headers={}, resp_headers={"set-cookie": "s=1"},
            status_code=200, req_body_inline=None, req_body_ref=None, req_body_size=0,
            req_body_sha=None, resp_body_inline="hi", resp_body_ref=None, resp_body_size=2,
            resp_body_sha="abc", http_version="HTTP/2", is_tls=True, tls_version="TLSv1.3",
            target_ip="93.184.216.34", response_time_ms=12, started_at="2026-07-19T00:00:00Z")
        self.assertEqual(rec["ctx_token"], "TOKEN.SIG")
        self.assertTrue(rec["hasSetCookie"])
        self.assertTrue(rec["isTls"])
        self.assertEqual(rec["targetIp"], "93.184.216.34")


if __name__ == "__main__":
    unittest.main()
