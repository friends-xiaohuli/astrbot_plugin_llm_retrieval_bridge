from __future__ import annotations

import asyncio
import copy
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_llm_retrieval_bridge.bridge import BridgeError
from astrbot_plugin_llm_retrieval_bridge.console import BridgeConsole
from astrbot_plugin_llm_retrieval_bridge.http_api import build_app
from astrbot_plugin_llm_retrieval_bridge.settings import default_config, LOCAL_EMBEDDING, REMOTE_EMBEDDING


class ConnectionConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        config = default_config()
        config["api_key"] = "test-only"
        config["embedding_backend"]["mode"] = REMOTE_EMBEDDING
        state = {"origin": {"plugin_id": "astrbot_plugin_llm_retrieval_bridge"}, "api": {"available": True}, "providers": {}}
        self.manager = types.SimpleNamespace(snapshot=AsyncMock(return_value=copy.deepcopy(state)),
                                             sync=AsyncMock(return_value={"action": "created", **state}),
                                             remove=AsyncMock(return_value={"action": "deleted", **state}))
        async def factory(context, settings, data_dir):
            return types.SimpleNamespace(model=settings["embedding_backend"]["api_model"], dimensions=512), types.SimpleNamespace(model="rank")
        self.console = BridgeConsole(types.SimpleNamespace(), config, Path(self.temp.name), service_factory=factory,
                                     connection_manager=self.manager)
        await self.console.start()

    async def asyncTearDown(self):
        await self.console.close()
        self.temp.cleanup()

    async def test_sync_uses_applied_configuration_and_does_not_save_drafts(self):
        applied_model = self.console.active_config["embedding_backend"]["api_model"]
        self.console.config["embedding_backend"]["api_model"] = "unapplied-draft"
        result = await self.console.sync_connection({"kind": "embedding"})
        kind, supplied = self.manager.sync.await_args.args
        self.assertEqual(kind, "embedding")
        self.assertEqual(supplied["embedding_backend"]["api_model"], applied_model)
        self.assertEqual(self.console.config["embedding_backend"]["api_model"], "unapplied-draft")
        self.assertIn("framework_providers", result)

    async def test_arbitrary_provider_ids_and_fields_are_not_forwarded(self):
        for payload in ({"kind": "chat"}, {"kind": []}, {"kind": "embedding", "id": "someone-else"}, {"kind": "embedding", "settings": {}}):
            with self.subTest(payload=payload), self.assertRaises(BridgeError):
                await self.console.remove_connection(payload)
        self.manager.remove.assert_not_awaited()

    async def test_sync_and_remove_are_serialized_with_configuration_updates(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def blocked(*args):
            started.set()
            await release.wait()
            return {"action": "created"}
        self.manager.sync.side_effect = blocked
        first = asyncio.create_task(self.console.sync_connection({"kind": "embedding"}))
        await started.wait()
        second = asyncio.create_task(self.console.remove_connection({"kind": "embedding"}))
        await asyncio.sleep(0)
        self.manager.remove.assert_not_awaited()
        release.set()
        await asyncio.gather(first, second)
        self.manager.remove.assert_awaited_once()

    async def test_runtime_failure_is_not_misreported_as_missing_files(self):
        runtime = {"available": False, "capabilities": {"embedding": {"available": False, "code": "local_runtime_import_failed", "message": "DLL load failed: onnxruntime"}}}
        with patch.object(self.console.dependencies, "status", return_value=runtime):
            state = await self.console.preview({"settings": {"embedding_backend": {"mode": LOCAL_EMBEDDING}}})
        self.assertEqual(state["model_status"]["embedding"]["state"], "runtime_error")
        self.assertEqual(len(state["diagnostics"]["issues"]), 1)

    async def test_management_http_routes_require_auth_and_preserve_provenance(self):
        from aiohttp.test_utils import TestClient, TestServer
        client = TestClient(TestServer(build_app(None, None, "test-only", console=self.console)))
        await client.start_server()
        try:
            for path in ("/console/connections/sync", "/console/connections/remove"):
                response = await client.post(path, json={"kind": "embedding"})
                self.assertEqual(response.status, 401)
            headers = {"Authorization": "Bearer test-only"}
            response = await client.get("/console/connections", headers=headers)
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["origin"]["plugin_id"], "astrbot_plugin_llm_retrieval_bridge")
            response = await client.post("/console/connections/sync", headers=headers, json={"kind": "embedding"})
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["action"], "created")
            response = await client.post("/console/connections/remove", headers=headers, json={"kind": "embedding", "id": "other"})
            self.assertEqual(response.status, 400)
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
