from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_llm_retrieval_bridge.bridge import BridgeError
from astrbot_plugin_llm_retrieval_bridge.runtime_dependencies import RuntimeDependencies


class DependencyTests(unittest.IsolatedAsyncioTestCase):
    def test_status_distinguishes_missing_packages(self):
        manager = RuntimeDependencies(ROOT)
        with patch("importlib.util.find_spec", side_effect=lambda name: None if name == "fastembed" else object()), patch("importlib.metadata.version", return_value="1.0"):
            result = manager.status()
        self.assertFalse(result["available"])
        self.assertFalse(result["packages"][0]["present"])
        self.assertIn("安装/更新", result["automatic_install"])

    async def test_install_uses_framework_requirements_api(self):
        installer = AsyncMock()
        manager = RuntimeDependencies(ROOT, installer=installer)
        await manager.install()
        installer.assert_awaited_once_with(requirements_path=str(ROOT / "requirements.txt"))

    async def test_framework_installation_errors_are_actionable_and_sanitized(self):
        manager = RuntimeDependencies(ROOT, installer=AsyncMock(side_effect=RuntimeError("secret-key-value")))
        with self.assertRaises(BridgeError) as error:
            await manager.install()
        self.assertEqual(error.exception.code, "dependency_install_failed")
        self.assertNotIn("secret-key-value", str(error.exception))

    async def test_concurrent_install_is_not_started_twice(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def installer(**kwargs):
            started.set()
            await finish.wait()
        manager = RuntimeDependencies(ROOT, installer=installer)
        task = asyncio.create_task(manager.install())
        await started.wait()
        try:
            with self.assertRaises(BridgeError) as error:
                await manager.install()
            self.assertEqual(error.exception.status, 409)
            self.assertTrue(manager.status()["installing"])
        finally:
            finish.set()
            await task
