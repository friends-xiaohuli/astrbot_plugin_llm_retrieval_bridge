from __future__ import annotations

import asyncio
import copy
import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_llm_retrieval_bridge.backends import EmbeddingService
from astrbot_plugin_llm_retrieval_bridge.bridge import BridgeError
from astrbot_plugin_llm_retrieval_bridge.console import BridgeConsole, editable_settings
from astrbot_plugin_llm_retrieval_bridge.settings import default_config, model_paths, normalize_config


class Config(dict):
    def __init__(self, values):
        super().__init__(values)
        self.writes = 0
        self._save_state_lock = threading.Lock()
        self._save_commit_lock = threading.Lock()
    async def save_config_async(self):
        self.writes += 1


class FakeEncoder:
    async def embed(self, texts):
        return [[.6, .8] for _ in texts]


class FakeRanker:
    def __init__(self, model):
        self.model, self.closed = model, False
    async def rerank(self, payload):
        return {"model": self.model, "results": [{"index": i, "relevance_score": 1 / (i + 1)} for i in range(len(payload["documents"]))]}
    async def close(self):
        self.closed = True


class ConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.config = Config(default_config())
        self.config["api_key"] = "test-console-key"
        self.config["embedding_backend"]["dimensions"] = 2
        self.config["embedding_backend"]["mode"] = "框架嵌入提供商"
        self.config["embedding_backend"]["provider_id"] = "embed"
        self.created = []
        async def factory(context, config, data_dir):
            pair = (EmbeddingService(FakeEncoder(), model=config["embedding_backend"]["api_model"], dimensions=2),
                    FakeRanker(config["rerank_backend"]["api_model"]))
            self.created.append(pair)
            return pair
        self.console = BridgeConsole(types.SimpleNamespace(), self.config, self.data, service_factory=factory)
        await self.console.start()

    async def asyncTearDown(self):
        await self.console.close()
        self.temp.cleanup()

    async def test_derived_fields_cannot_be_written(self):
        for key in ["openai_embedding_fields", "vllm_rerank_fields", "runtime_paths"]:
            with self.subTest(key=key), self.assertRaises(BridgeError):
                await self.console.save({"revision": 1, "settings": {key: {"endpoint": "forged"}}})
        self.assertEqual(self.config.writes, 0)

    async def test_preview_does_not_write_or_load_models(self):
        result = await self.console.preview({"settings": {"model_storage": {"root_dir": "another-root"}}})
        self.assertEqual(result["paths"]["model_root"], str((self.data / "another-root").resolve()))
        self.assertEqual(self.config.writes, 0)
        self.assertEqual(len(self.created), 1)

    async def test_save_applies_models_and_refreshes_readonly_connections(self):
        result = await self.console.save({"revision": 1, "settings": {"rerank_backend": {"api_model": "new-rank"}}})
        self.assertEqual(result["connections"]["vllm_rerank_fields"]["rerank_model"], "new-rank")
        self.assertEqual(self.console.generation.reranker.model, "new-rank")
        self.assertEqual(self.config.writes, 1)
        self.assertEqual(self.config["vllm_rerank_fields"]["rerank_api_key"], "在可视化视图中查看与复制")
        exported = json.loads((self.data / "connection-info.json").read_text(encoding="utf-8"))
        self.assertEqual(exported["vllm_rerank_fields"]["rerank_api_key"], "test-console-key")

    async def test_save_does_not_close_an_inflight_generation(self):
        old_ranker = self.console.generation.reranker
        async with self.console.lease():
            await self.console.save({"revision": 1, "settings": {"rerank_backend": {"api_model": "new-rank"}}})
            await asyncio.sleep(0)
            self.assertFalse(old_ranker.closed)
        await asyncio.sleep(.01)
        self.assertTrue(old_ranker.closed)

    async def test_port_change_is_saved_but_connections_show_running_port(self):
        port = self.config["port"]
        result = await self.console.save({"revision": 1, "settings": {"port": port + 1}})
        self.assertTrue(result["requires_restart"])
        self.assertIn(f":{port}/", result["connections"]["openai_embedding_fields"]["embedding_api_base"] + "/")
        self.assertEqual(self.config["port"], port + 1)

    async def test_stale_page_cannot_overwrite_another_save(self):
        await self.console.save({"revision": 1, "settings": {"max_rerank_documents": 8}})
        with self.assertRaises(BridgeError) as error:
            await self.console.save({"revision": 1, "settings": {"max_rerank_documents": 16}})
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.config["max_rerank_documents"], 8)

    async def test_embedding_test_reports_real_shape_and_similarity(self):
        result = await self.console.test({"kind": "embedding", "texts": ["a", "b"]})
        self.assertEqual(result["dimensions"], [2, 2])
        self.assertEqual(result["cosine_similarity"], [[1., 1.], [1., 1.]])
        self.assertEqual(self.config.writes, 0)

    async def test_unsaved_test_uses_temporary_services_without_saving(self):
        result = await self.console.test({"kind": "rerank", "settings": {"rerank_backend": {"api_model": "temporary"}}, "documents": ["a", "b"]})
        self.assertEqual(result["model"], "temporary")
        self.assertIn("未保存", result["configuration"])
        self.assertEqual(self.console.generation.reranker.model, "llm-rerank")
        self.assertTrue(self.created[-1][1].closed)
        self.assertEqual(self.config.writes, 0)

    async def test_invalid_numeric_settings_do_not_replace_services(self):
        with self.assertRaises(BridgeError):
            await self.console.save({"revision": 1, "settings": {"embedding_threads": 999}})
        self.assertEqual(len(self.created), 1)


class PathTests(unittest.TestCase):
    def test_huggingface_snapshot_inside_model_directory_is_resolved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "fastembed/MiniLM-L-6-v2"
            snapshot = repo / "snapshots/abc123"
            (repo / "refs").mkdir(parents=True)
            (repo / "refs/main").write_text("abc123", encoding="ascii")
            (snapshot / "onnx").mkdir(parents=True)
            for name in ["onnx/model.onnx", "config.json", "tokenizer.json"]:
                (snapshot / name).write_text("test", encoding="utf-8")
            self.assertEqual(model_paths(default_config(), root)["rerank_model_dir"], str(snapshot.resolve()))

    def test_user_current_layout_is_detected_without_moving_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, plugin = root / "plugin_data/plugin", root / "plugins/plugin"
            embed = data / "fastembed/fast-bge-small-zh-v1.5"
            rank = plugin / "fastembed/MiniLM-L-6-v2"
            for directory, model in [(embed, "model_optimized.onnx"), (rank, "onnx/model.onnx")]:
                (directory / model).parent.mkdir(parents=True)
                for file in ["config.json", "tokenizer.json", model]:
                    (directory / file).write_text("test", encoding="utf-8")
            result = model_paths(default_config(), data, plugin)
            self.assertEqual(result["embedding_model_dir"], str(embed.resolve()))
            self.assertEqual(result["rerank_model_dir"], str(rank.resolve()))
            self.assertIn("兼容现有", result["rerank_origin"])
            self.assertFalse((data / "fastembed/MiniLM-L-6-v2").exists())

    def test_explicit_missing_override_is_not_replaced_by_another_directory(self):
        config = default_config()
        config["model_storage"]["rerank_dir"] = "manual-directory"
        result = model_paths(config, ROOT)
        self.assertEqual(result["rerank_model_dir"], str((ROOT / "manual-directory").resolve()))
        self.assertFalse(result["rerank_directory_exists"])

    def test_legacy_path_is_migrated_once_then_can_be_cleared(self):
        config = default_config()
        config["embedding_model_dir"] = "old-custom"
        normalize_config(config)
        self.assertEqual(config["model_storage"]["embedding_dir"], "old-custom")
        config["model_storage"]["embedding_dir"] = ""
        normalize_config(config)
        self.assertEqual(config["model_storage"]["embedding_dir"], "")


if __name__ == "__main__":
    unittest.main()
