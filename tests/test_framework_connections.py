from __future__ import annotations

import asyncio
import copy
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_llm_retrieval_bridge.bridge import BridgeError
from astrbot_plugin_llm_retrieval_bridge.framework_connections import FrameworkConnections, ORIGIN_KEY, PLUGIN_ID
from astrbot_plugin_llm_retrieval_bridge.settings import default_config, REMOTE_EMBEDDING


class Manager:
    """Lifecycle API substitute: all changes must enter create/update/delete."""
    def __init__(self):
        self.providers_config = []
        self.inst_map = {}
        self.calls = []
        self.active = self.max_active = 0
        self.fail_after_save = False

    def get_provider_config_by_id(self, provider_id):
        return next((copy.deepcopy(row) for row in self.providers_config if row["id"] == provider_id), None)

    async def create_provider(self, config):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(.001)
            if self.get_provider_config_by_id(config["id"]):
                raise ValueError("duplicate ID")
            self.calls.append(("create", copy.deepcopy(config)))
            self.providers_config.append(copy.deepcopy(config))
            if self.fail_after_save:
                raise RuntimeError("api-key-must-not-leak")
            self.inst_map[config["id"]] = object()
        finally:
            self.active -= 1

    async def update_provider(self, provider_id, config):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(.001)
            self.calls.append(("update", provider_id, copy.deepcopy(config)))
            index = next(i for i, row in enumerate(self.providers_config) if row["id"] == provider_id)
            self.providers_config[index] = copy.deepcopy(config)
            self.inst_map[provider_id] = object()
        finally:
            self.active -= 1

    async def delete_provider(self, *, provider_id):
        self.calls.append(("delete", provider_id))
        self.providers_config[:] = [row for row in self.providers_config if row["id"] != provider_id]
        self.inst_map.pop(provider_id, None)


class Service:
    """Verified ProviderConfigService signatures; framework owns provider lifecycle."""
    def __init__(self, lifecycle):
        self.manager = lifecycle.provider_manager
    async def create_provider(self, config, source_id=None):
        await self.manager.create_provider(copy.deepcopy(config))
    async def update_provider(self, provider_id, config):
        await self.manager.update_provider(provider_id, copy.deepcopy(config))
    async def delete_provider(self, provider_id):
        await self.manager.delete_provider(provider_id=provider_id)


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.config = default_config()
        self.config["api_key"] = "test-bridge-key"
        self.manager = Manager()
        self.context = types.SimpleNamespace(provider_manager=self.manager, get_config=lambda: {"provider": self.manager.providers_config})
        self.bridge = FrameworkConnections(self.context, self.data, 32150, service_factory=Service)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_snapshot_is_read_only_and_describes_rest_service(self):
        result = await self.bridge.snapshot(self.config)
        self.assertEqual(result["origin"]["plugin_id"], PLUGIN_ID)
        self.assertTrue(result["api"]["available"])
        self.assertEqual(result["providers"]["embedding"]["status"], "missing")
        self.assertTrue(result["providers"]["rerank"]["can_sync"])
        self.assertFalse(result["providers"]["rerank"]["can_remove"])
        self.assertFalse((self.data / "framework-connections.json").exists())
        self.assertFalse(self.manager.calls)

    async def test_embedding_create_uses_native_adapter_fields_and_origin(self):
        result = await self.bridge.sync("embedding", self.config)
        payload = self.manager.calls[0][1]
        self.assertEqual(payload["type"], "openai_embedding")
        self.assertEqual(payload["provider_type"], "embedding")
        self.assertEqual(payload["embedding_api_base"], "http://127.0.0.1:32150/v1")
        self.assertEqual(payload["embedding_api_key"], "test-bridge-key")
        self.assertEqual(payload["embedding_dimensions_mode"], "never")
        self.assertNotIn("endpoint", payload)
        self.assertNotIn("provider_id", payload)
        self.assertNotIn("origin_plugin", payload)
        self.assertEqual(payload[ORIGIN_KEY]["plugin_id"], PLUGIN_ID)
        self.assertTrue(result["providers"]["embedding"]["managed"])
        self.assertTrue(result["providers"]["embedding"]["registered"]["loaded"])

    async def test_rerank_create_uses_vllm_protocol_without_diagnostic_fields(self):
        await self.bridge.sync("rerank", self.config)
        payload = self.manager.calls[0][1]
        self.assertEqual(payload["type"], "vllm_rerank")
        self.assertEqual(payload["provider_type"], "rerank")
        self.assertEqual(payload["rerank_api_suffix"], "/v1/rerank")
        self.assertEqual(payload["rerank_api_base"], "http://127.0.0.1:32150")
        self.assertNotIn("top_n", payload)
        self.assertEqual(payload[ORIGIN_KEY]["kind"], "rerank")

    async def test_repeated_sync_updates_owned_provider_and_detects_parameter_changes(self):
        await self.bridge.sync("embedding", self.config)
        self.config["embedding_backend"]["api_model"] = "new-model"
        self.config["api_key"] = "rotated-key"
        before = await self.bridge.snapshot(self.config)
        self.assertTrue(before["providers"]["embedding"]["needs_sync"])
        result = await self.bridge.sync("embedding", self.config)
        self.assertEqual(len(self.manager.providers_config), 1)
        self.assertEqual(result["action"], "updated")
        self.assertFalse(result["providers"]["embedding"]["needs_sync"])
        self.assertEqual(self.manager.providers_config[0]["embedding_model"], "new-model")
        self.assertEqual(self.manager.providers_config[0]["embedding_api_key"], "rotated-key")

    async def test_reload_uses_durable_ownership_and_delete_calls_framework_api(self):
        await self.bridge.sync("embedding", self.config)
        reloaded = FrameworkConnections(self.context, self.data, 32150, service_factory=Service)
        snapshot = await reloaded.snapshot(self.config)
        self.assertTrue(snapshot["providers"]["embedding"]["can_remove"])
        result = await reloaded.remove("embedding", self.config)
        self.assertEqual(result["action"], "removed")
        self.assertEqual(self.manager.calls[-1], ("delete", "bridge-embedding"))
        self.assertFalse(self.manager.providers_config)
        self.assertTrue((self.data / "framework-connections.json").is_file())

    async def test_unchanged_sync_does_not_reload_an_in_use_provider(self):
        await self.bridge.sync("embedding", self.config)
        result = await self.bridge.sync("embedding", self.config)
        self.assertEqual(result["action"], "unchanged")
        self.assertEqual(len(self.manager.calls), 1)

    async def test_same_id_does_not_grant_ownership(self):
        original = {"id": "bridge-embedding", "type": "openai_embedding", "provider_type": "embedding",
                    "embedding_api_base": "http://127.0.0.1:32150/v1", "embedding_api_key": "someone-elses-key"}
        self.manager.providers_config.append(copy.deepcopy(original))
        for action in (self.bridge.sync, self.bridge.remove):
            with self.assertRaises(BridgeError) as error:
                await action("embedding", self.config)
            self.assertEqual(error.exception.code, "provider_ownership_conflict")
        self.assertEqual(self.manager.providers_config, [original])
        self.assertFalse(self.manager.calls)

    async def test_owner_from_different_installation_is_not_adopted(self):
        await self.bridge.sync("embedding", self.config)
        self.manager.providers_config[0][ORIGIN_KEY]["instance_id"] = "0" * 32
        with self.assertRaises(BridgeError):
            await self.bridge.remove("embedding", self.config)
        self.assertEqual(len(self.manager.providers_config), 1)

    async def test_moved_endpoint_and_provider_source_are_never_overwritten_or_deleted(self):
        await self.bridge.sync("embedding", self.config)
        row = self.manager.providers_config[0]
        for changes in ({"embedding_api_base": "https://cloud.example/v1"}, {"provider_source_id": "shared-parent"}):
            original = copy.deepcopy(row)
            row.update(changes)
            for action in (self.bridge.sync, self.bridge.remove):
                with self.assertRaises(BridgeError):
                    await action("embedding", self.config)
            row.clear()
            row.update(original)

    async def test_even_unregistered_self_upstream_is_rejected(self):
        self.config["embedding_backend"].update(mode=REMOTE_EMBEDDING, provider_id="bridge-embedding")
        snapshot = await self.bridge.snapshot(self.config)
        self.assertFalse(snapshot["providers"]["embedding"]["can_sync"])
        with self.assertRaises(BridgeError) as error:
            await self.bridge.sync("embedding", self.config)
        self.assertEqual(error.exception.code, "recursive_provider")
        self.assertFalse(self.manager.calls)
        self.assertFalse((self.data / "framework-connections.json").exists())

    async def test_concurrent_syncs_do_not_duplicate_or_run_parallel_lifecycle_changes(self):
        await asyncio.gather(*(self.bridge.sync("embedding", self.config) for _ in range(4)))
        self.assertEqual(len(self.manager.providers_config), 1)
        self.assertEqual(sum(call[0] == "create" for call in self.manager.calls), 1)
        self.assertEqual(self.manager.max_active, 1)

    async def test_ownership_storage_failure_prevents_framework_mutation(self):
        with patch.object(Path, "open", side_effect=PermissionError("read-only")):
            with self.assertRaises(BridgeError) as error:
                await self.bridge.sync("embedding", self.config)
        self.assertEqual(error.exception.code, "provider_ownership_unavailable")
        self.assertFalse(self.manager.calls)

    async def test_saved_configuration_after_framework_error_remains_owned_and_retryable(self):
        self.manager.fail_after_save = True
        with self.assertLogs(level="WARNING"), self.assertRaises(BridgeError) as error:
            await self.bridge.sync("embedding", self.config)
        self.assertNotIn("api-key-must-not-leak", str(error.exception))
        snapshot = await self.bridge.snapshot(self.config)
        self.assertTrue(snapshot["providers"]["embedding"]["can_remove"])
        self.assertFalse(snapshot["providers"]["embedding"]["registered"]["loaded"])
        self.manager.fail_after_save = False
        retried = await self.bridge.sync("embedding", self.config)
        self.assertEqual(retried["action"], "updated")
        self.assertTrue(retried["providers"]["embedding"]["registered"]["loaded"])
        self.assertEqual(len(self.manager.providers_config), 1)

    async def test_snapshot_redacts_keys_headers_ownership_token_and_url_credentials(self):
        await self.bridge.sync("embedding", self.config)
        self.manager.providers_config[0].update(embedding_api_base="https://alice:private-password@example.test/v1?api_key=private-query",
                                                custom_headers='{"Authorization":"private-header"}')
        serialized = json.dumps(await self.bridge.snapshot(self.config))
        for secret in ("test-bridge-key", "private-password", "private-query", "private-header", "instance_id"):
            self.assertNotIn(secret, serialized)
        self.assertIn("https://example.test/v1", serialized)

    async def test_duplicate_ids_and_duplicate_output_ids_are_rejected(self):
        self.manager.providers_config[:] = [{"id": "bridge-embedding"}, {"id": "bridge-embedding"}]
        with self.assertRaises(BridgeError):
            await self.bridge.sync("embedding", self.config)
        self.manager.providers_config.clear()
        self.config["vllm_rerank_fields"]["provider_id"] = "bridge-embedding"
        with self.assertRaises(BridgeError) as error:
            await self.bridge.sync("rerank", self.config)
        self.assertEqual(error.exception.code, "provider_id_conflict")

    async def test_pending_port_change_uses_the_current_listener(self):
        self.config["port"] = 32151
        await self.bridge.sync("embedding", self.config)
        self.assertEqual(self.manager.providers_config[0]["embedding_api_base"], "http://127.0.0.1:32150/v1")

    async def test_restart_on_new_port_keeps_ownership_and_updates_published_target(self):
        for kind in ("embedding", "rerank"):
            await self.bridge.sync(kind, self.config)
        self.config["port"] = 32151
        restarted = FrameworkConnections(self.context, self.data, 32151, service_factory=Service)
        before = await restarted.snapshot(self.config)
        for kind in ("embedding", "rerank"):
            self.assertEqual(before["providers"][kind]["status"], "managed")
            self.assertTrue(before["providers"][kind]["needs_sync"])
            self.assertTrue(before["providers"][kind]["can_remove"])
            await restarted.sync(kind, self.config)
            row = self.manager.get_provider_config_by_id("bridge-" + kind)
            self.assertIn(":32151", row[kind + "_api_base"])
            self.assertEqual(row[ORIGIN_KEY]["api_base"], row[kind + "_api_base"])
            self.assertEqual(row[ORIGIN_KEY]["port"], 32151)
            self.assertEqual(row[ORIGIN_KEY]["api_suffix"], "/v1/rerank" if kind == "rerank" else "")
        after = await restarted.snapshot(self.config)
        self.assertTrue(all(not row["needs_sync"] for row in after["providers"].values()))

    async def test_restart_can_delete_owned_old_port_registrations_without_resync(self):
        for kind in ("embedding", "rerank"):
            await self.bridge.sync(kind, self.config)
        restarted = FrameworkConnections(self.context, self.data, 32151, service_factory=Service)
        for kind in ("embedding", "rerank"):
            result = await restarted.remove(kind, self.config)
            self.assertEqual(result["action"], "removed")
        self.assertFalse(self.manager.providers_config)

    async def test_manually_changed_port_or_suffix_does_not_match_published_target(self):
        for kind in ("embedding", "rerank"):
            await self.bridge.sync(kind, self.config)
        restarted = FrameworkConnections(self.context, self.data, 32151, service_factory=Service)
        for kind, changes in (("embedding", {"embedding_api_base": "http://127.0.0.1:32152/v1"}),
                              ("rerank", {"rerank_api_suffix": "/other/rerank"})):
            row = next(row for row in self.manager.providers_config if row["id"] == "bridge-" + kind)
            original = copy.deepcopy(row)
            row.update(changes)
            for action in (restarted.sync, restarted.remove):
                with self.assertRaises(BridgeError) as error:
                    await action(kind, self.config)
                self.assertEqual(error.exception.code, "provider_ownership_conflict")
            row.clear()
            row.update(original)

    async def test_published_marker_cannot_authorize_external_urls_or_invalid_ports(self):
        await self.bridge.sync("embedding", self.config)
        row = self.manager.providers_config[0]
        original = copy.deepcopy(row)
        for base, port in (("https://outside.example:32150/v1", 32150), ("http://127.0.0.1:80/v1", 80),
                           ("http://127.0.0.1:32152/v1", 32150)):
            row["embedding_api_base"] = row[ORIGIN_KEY]["api_base"] = base
            row[ORIGIN_KEY]["port"] = port
            with self.assertRaises(BridgeError):
                await self.bridge.remove("embedding", self.config)
            row.clear()
            row.update(copy.deepcopy(original))

    async def test_unsupported_framework_reports_without_writing(self):
        bridge = FrameworkConnections(types.SimpleNamespace(), self.data, 32150)
        result = await bridge.snapshot(self.config)
        self.assertFalse(result["api"]["available"])
        self.assertEqual(result["providers"]["embedding"]["status"], "unsupported")
        with self.assertRaises(BridgeError) as error:
            await bridge.sync("embedding", self.config)
        self.assertEqual(error.exception.code, "provider_api_unavailable")


if __name__ == "__main__":
    unittest.main()
