"""Regression coverage for Claude Code credential-pool UI state changes."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "claude_credential_proxy.py"
SPEC = importlib.util.spec_from_file_location("claude_credential_proxy_test", SCRIPT)
assert SPEC and SPEC.loader
proxy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = proxy
SPEC.loader.exec_module(proxy)


def credential(record_id: str, label: str) -> proxy.Credential:
    return proxy.Credential(record_id, f"key-{record_id}", "https://example.test/v1", "claude-test", 0, label)


class CredentialPoolUiStateTests(unittest.TestCase):
    def _capture_feishu_put(self, pool: proxy.CredentialPool, current_status: str) -> list[object]:
        """Return captured PUT requests while serving one current status."""
        pool._read_record_status = lambda candidate: current_status
        pool._token = lambda: "test-token"
        requests: list[object] = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return None

            def read(self):
                return b'{"code": 0}'

        original_urlopen = proxy.urlopen
        proxy.urlopen = lambda request, timeout: requests.append(request) or Response()
        self.addCleanup(setattr, proxy, "urlopen", original_urlopen)
        return requests

    def test_f_p01_in_use_credential_is_retained_from_recent_health_cache(self) -> None:
        active = credential("r1", "A")
        pool = proxy.CredentialPool()
        identity = proxy.credential_identity(active)
        pool._known_status[identity] = proxy.CLAUDE_IN_USE_STATUS
        pool._last_probe[identity] = time.time()

        self.assertEqual(pool._health_filter([active]), [active])

    def test_f_p02_probe_success_does_not_overwrite_claude_in_use_status(self) -> None:
        active = credential("r1", "A")
        pool = proxy.CredentialPool()
        pool._known_status[proxy.credential_identity(active)] = proxy.CLAUDE_IN_USE_STATUS
        writes: list[tuple[object, ...]] = []
        pool._write_ui_state = lambda *args: writes.append(args)
        original_forward = proxy.forward
        proxy.forward = lambda *args, **kwargs: (200, {}, b"{}")
        try:
            self.assertEqual(pool._health_filter([active]), [active])
        finally:
            proxy.forward = original_forward

        self.assertEqual(writes, [])

    def test_f_p03_delayed_dashboard_update_preserves_active_marker(self) -> None:
        active = credential("r1", "A")
        pool = proxy.CredentialPool()
        pool._active_record_id = active.record_id
        pool._read_record_status = lambda candidate: proxy.CLAUDE_IN_USE_STATUS
        pool._token = lambda: "test-token"
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return None

            def read(self):
                return b'{"code": 0}'

        original_urlopen = proxy.urlopen
        proxy.urlopen = lambda request, timeout: requests.append(request) or Response()
        try:
            pool._write_ui_state(active, "✅ 正常", "delayed health result")
        finally:
            proxy.urlopen = original_urlopen

        payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(payload["fields"][proxy.FIELD_ALIASES["status"][0]], proxy.CLAUDE_IN_USE_STATUS)

    def test_f_p04_verified_fallback_moves_claude_in_use_marker(self) -> None:
        active = credential("r1", "A")
        standby = credential("r2", "B")
        pool = proxy.CredentialPool()
        pool._credentials = [active, standby]
        pool._by_tier = {0: [active, standby]}
        pool._active_record_id = active.record_id
        pool._probe_credential = lambda candidate: candidate is standby
        writes: list[tuple[object, ...]] = []
        pool._write_ui_state = lambda *args: writes.append(args)

        self.assertIs(pool.current(verify=True), standby)
        self.assertEqual(pool._active_record_id, standby.record_id)
        self.assertEqual(
            [(call[0].label, call[1]) for call in writes],
            [("A", "✅ 正常"), ("B", proxy.CLAUDE_IN_USE_STATUS)],
        )

    def test_f_p05_tier_advance_moves_marker_from_previous_tier(self) -> None:
        active = credential("r1", "A")
        fallback = proxy.Credential("r2", "key-r2", "https://example.test/v1", "claude-test", 1, "B")
        pool = proxy.CredentialPool()
        pool._credentials = [active]
        pool._by_tier = {0: [active], 1: [fallback]}
        pool._active_record_id = active.record_id
        writes: list[tuple[object, ...]] = []
        pool._write_ui_state = lambda *args: writes.append(args)

        self.assertIs(pool.next_after(active), fallback)
        self.assertEqual(pool._active_tier, 1)
        self.assertEqual(pool._active_record_id, fallback.record_id)
        self.assertEqual(
            [(call[0].label, call[1]) for call in writes],
            [("A", "✅ 正常"), ("B", proxy.CLAUDE_IN_USE_STATUS)],
        )

    def test_f_p06_stale_marker_move_is_discarded_after_waiting_for_ui_lock(self) -> None:
        previous = credential("r1", "A")
        stale_candidate = credential("r2", "B")
        newer_candidate = credential("r3", "C")
        pool = proxy.CredentialPool()
        pool._active_record_id = stale_candidate.record_id
        writes: list[tuple[object, ...]] = []
        pool._write_ui_state = lambda *args: writes.append(args)

        class AdvancingUiLock:
            def __enter__(self) -> None:
                pool._active_record_id = newer_candidate.record_id

            def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
                return None

        pool._ui_lock = AdvancingUiLock()
        pool._move_active_in_use(previous, stale_candidate, "stale")

        self.assertEqual(writes, [])

    def test_f_p07_refresh_clears_stale_claude_marker_from_standby(self) -> None:
        active = credential("r1", "A")
        stale = credential("r2", "B")
        pool = proxy.CredentialPool()
        pool._known_status[proxy.credential_identity(stale)] = proxy.CLAUDE_IN_USE_STATUS
        pool._consecutive_successes[proxy.credential_identity(stale)] = proxy.PROBE_SUCCESS_TOLERANCE
        writes: list[tuple[object, ...]] = []
        pool._write_ui_state = lambda *args: writes.append(args)

        pool._sync_dashboard([active, stale], active)

        self.assertEqual(
            [(call[0].label, call[1]) for call in writes],
            [("B", "✅ 正常")],
        )

    def test_f_p08_standby_cleanup_preserves_other_agent_marker(self) -> None:
        standby = credential("r2", "B")
        pool = proxy.CredentialPool()
        requests = self._capture_feishu_put(pool, f"🔄 Hermes+{proxy.CLAUDE_LEGACY_AGENT_NAME}使用中")

        pool._write_ui_state(standby, "✅ 正常", "standby")

        payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(payload["fields"][proxy.FIELD_ALIASES["status"][0]], "🔄 Hermes使用中")

    def test_f_p09_active_marker_merge_preserves_other_agent_marker(self) -> None:
        active = credential("r1", "A")
        pool = proxy.CredentialPool()
        pool._active_record_id = active.record_id
        requests = self._capture_feishu_put(pool, "🔄 Hermes使用中")

        pool._move_active_in_use(None, active, "request")

        payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(
            payload["fields"][proxy.FIELD_ALIASES["status"][0]],
            f"🔄 Hermes+{proxy.CLAUDE_AGENT_NAME}使用中",
        )

    def test_f_p10_legacy_token_stripped_from_non_active_standby(self) -> None:
        # P-07: 旧格式 "Claude Code"（无主机名）在非 active 行被剥离并落到 ✅ 正常。
        standby = credential("r2", "B")
        pool = proxy.CredentialPool()
        requests = self._capture_feishu_put(pool, f"🔄 {proxy.CLAUDE_LEGACY_AGENT_NAME}使用中")

        pool._write_ui_state(standby, "✅ 正常", "standby")

        payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(payload["fields"][proxy.FIELD_ALIASES["status"][0]], "✅ 正常")

    def test_f_p11_legacy_strip_preserves_other_agents_and_other_hosts(self) -> None:
        # P-07: 剥离本代理旧格式 token 时保留其他 Agent 标记（Hermes）与
        # 其他机器按新格式写的标记（Claude Code(他机)），不误清。
        standby = credential("r2", "B")
        pool = proxy.CredentialPool()
        other_host = "Claude Code(OTHER-HOST)"
        current = f"🔄 Hermes+{proxy.CLAUDE_LEGACY_AGENT_NAME}+{other_host}使用中"
        requests = self._capture_feishu_put(pool, current)

        pool._write_ui_state(standby, "✅ 正常", "standby")

        payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(payload["fields"][proxy.FIELD_ALIASES["status"][0]], f"🔄 Hermes+{other_host}使用中")

    def test_f_p12_active_marker_replaces_legacy_without_double_token(self) -> None:
        # P-07: active 行仍带旧格式 "Claude Code" 标记时，落新标记应替换而非并列双写。
        active = credential("r1", "A")
        pool = proxy.CredentialPool()
        pool._active_record_id = active.record_id
        requests = self._capture_feishu_put(pool, f"🔄 {proxy.CLAUDE_LEGACY_AGENT_NAME}使用中")

        pool._move_active_in_use(None, active, "request")

        payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(payload["fields"][proxy.FIELD_ALIASES["status"][0]], proxy.CLAUDE_IN_USE_STATUS)


if __name__ == "__main__":
    unittest.main()
