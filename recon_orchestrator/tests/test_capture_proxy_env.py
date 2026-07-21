"""
Verifies the orchestrator maps CaptureProxyConfig body-storage knobs to the exact
CAPTURE_* environment injected into the spawned capture-proxy container, without
actually spawning containers (the docker client's containers.run is intercepted).

This closes the one seam the addon E2E cannot cover: config -> env mapping.

Run: python3 -m unittest recon_orchestrator.tests.test_capture_proxy_env
(from repo root, inside the recon-orchestrator container which has `docker`.)
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import container_manager as cm  # noqa: E402


def _spawn_env(config: dict) -> dict:
    """Run start_capture_proxy with docker intercepted; return the proxy env dict."""
    mgr = cm.ContainerManager.__new__(cm.ContainerManager)  # skip docker.from_env in __init__
    calls = []

    class _Containers:
        def run(self, image, **kw):
            calls.append(kw)
            return mock.Mock()

    mgr.client = mock.Mock()
    mgr.client.containers = _Containers()
    mgr._remove_container_if_exists = lambda name: None
    mgr._capture_image = lambda: "redamon-capture-proxy:latest"
    mgr._capture_port = lambda: 8888

    async def _fake_status():
        return {"running": True}
    mgr.capture_proxy_status = _fake_status

    asyncio.run(mgr.start_capture_proxy(config))
    # First containers.run call is the proxy (second is ingest).
    proxy_kw = calls[0]
    return proxy_kw["environment"]


class TestCaptureProxyEnvMapping(unittest.TestCase):
    def test_defaults_present(self):
        env = _spawn_env({})
        # New body-storage knobs must always be injected, even with an empty config.
        self.assertEqual(env["CAPTURE_STORE_REQ_BODIES"], "true")
        self.assertEqual(env["CAPTURE_STORE_RESP_BODIES"], "true")
        self.assertEqual(env["CAPTURE_MAX_STORE_MB"], "5")
        self.assertIn("CAPTURE_BODY_RULES", env)

    def test_direction_toggles_map(self):
        env = _spawn_env({"storeReqBodies": False, "storeRespBodies": True})
        self.assertEqual(env["CAPTURE_STORE_REQ_BODIES"], "false")
        self.assertEqual(env["CAPTURE_STORE_RESP_BODIES"], "true")

    def test_max_store_mb_maps_including_zero(self):
        self.assertEqual(_spawn_env({"maxStoreMb": 12})["CAPTURE_MAX_STORE_MB"], "12")
        # 0 = unlimited must survive (not be treated as falsy-default).
        self.assertEqual(_spawn_env({"maxStoreMb": 0})["CAPTURE_MAX_STORE_MB"], "0")

    def test_body_rules_json_normalized(self):
        env = _spawn_env({"bodyRules": '{"image": "disk", "font": "meta"}'})
        # Re-serialized compactly, valid JSON the addon's parse_body_rules accepts.
        import json
        parsed = json.loads(env["CAPTURE_BODY_RULES"])
        self.assertEqual(parsed["image"], "disk")
        self.assertEqual(parsed["font"], "meta")

    def test_body_rules_accepts_dict(self):
        env = _spawn_env({"bodyRules": {"binary": "meta"}})
        import json
        self.assertEqual(json.loads(env["CAPTURE_BODY_RULES"])["binary"], "meta")

    def test_body_rules_garbage_becomes_empty(self):
        env = _spawn_env({"bodyRules": "{not valid json"})
        self.assertEqual(env["CAPTURE_BODY_RULES"], "")

    def test_existing_knobs_still_map(self):
        env = _spawn_env({"maxBodyKb": 128, "storeBodies": False})
        self.assertEqual(env["CAPTURE_PROXY_MAX_BODY_KB"], "128")
        self.assertEqual(env["CAPTURE_PROXY_STORE_BODIES"], "false")


if __name__ == "__main__":
    unittest.main()
