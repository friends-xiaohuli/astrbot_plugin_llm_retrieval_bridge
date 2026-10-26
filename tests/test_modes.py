from __future__ import annotations

import asyncio
import copy
import importlib
import json
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_llm_retrieval_bridge.backends import (
    EmbeddingService, FrameworkEmbeddingBackend, FrameworkRerankBackend,
    LLMEnhancedEmbeddingBackend, RerankService, validate_vectors,
)
from astrbot_plugin_llm_retrieval_bridge.bridge import BridgeError, embedding_response
from astrbot_plugin_llm_retrieval_bridge.cache_import import import_rerank_cache
from astrbot_plugin_llm_retrieval_bridge.settings import (
    LOCAL_EMBEDDING, REMOTE_EMBEDDING, LLM_LOCAL_EMBEDDING, LLM_REMOTE_EMBEDDING,
    LOCAL_RERANK, REMOTE_RERANK, LLM_RERANK, connection_fields, default_config,
    normalize_config, reject_self_provider, resolve_model_path,
)


class TinyEncoder:
    def __init__(self, *args, **kwargs):
        self.texts = []
        self.closed = False

    async def embed(self, texts):
        self.texts.extend(texts)
        return [[.6, .8] for _ in texts]

    async def close(self):
        self.closed = True


class VectorValidationTests(unittest.TestCase):
    def test_wrong_shape_and_nonfinite_and_zero_are_rejected(self):
        for vectors in [[], [[1]], [[True, 1]], [[float("nan"), 1]], [[0., 0.]]]:
            with self.subTest(vectors=vectors), self.assertRaises(BridgeError):
                validate_vectors(vectors, 1, 2)


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_enhanced_llm_never_creates_numeric_vectors(self):
        calls = []
        encoder = TinyEncoder()
        async def generate(prompt, system):
            calls.append(json.loads(prompt))
            return json.dumps({"texts": [{"index": row["index"], "text": row["text"] + "整理"}
                                        for row in calls[-1]["texts"]]}, ensure_ascii=False)
        backend = LLMEnhancedEmbeddingBackend(generate, encoder)
        first = await backend.embed(["喝茶", "喝茶", "跑步"])
        second = await backend.embed(["喝茶"])
        self.assertEqual(first, [[.6, .8]] * 3)
        self.assertEqual(second, [[.6, .8]])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]["texts"]), 2)
        self.assertEqual(encoder.texts, ["喝茶整理", "喝茶整理", "跑步整理", "喝茶整理"])
        await backend.close()
        self.assertTrue(encoder.closed)

    async def test_enhanced_llm_bad_json_indices_and_omissions_are_rejected(self):
        invalid = ['bad', '{}', '{"texts":[]}', '{"texts":[{"index":1,"text":"x"}]}',
                   '{"texts":[{"index":0,"text":""}]}', '{"texts":[{"index":0,"text":[0.4]}]}']
        for raw in invalid:
            async def generate(prompt, system, output=raw):
                return output
            encoder = TinyEncoder()
            with self.subTest(raw=raw), self.assertRaises(BridgeError):
                await LLMEnhancedEmbeddingBackend(generate, encoder).embed(["原文"])
            self.assertEqual(encoder.texts, [])

    async def test_llm_input_limit_returns_error_before_generation(self):
        async def generate(prompt, system):
            self.fail("超长输入不能调用模型")
        with self.assertRaises(BridgeError):
            await LLMEnhancedEmbeddingBackend(generate, TinyEncoder(), max_chars=64).embed(["a" * 65])

    async def test_remote_embedding_is_validated_at_boundary(self):
        provider = types.SimpleNamespace(get_embeddings=TinyEncoder().embed)
        backend = FrameworkEmbeddingBackend(lambda: provider)
        service = EmbeddingService(backend, model="custom-model", dimensions=2)
        result = await embedding_response({"model": "custom-model", "input": ["a", "b"]}, service)
        self.assertEqual(result["model"], "custom-model")
        self.assertEqual(result["data"][1]["embedding"], [.6, .8])

    async def test_native_rerank_results_keep_indices_even_when_unsorted(self):
        class Provider:
            async def rerank(self, query, documents, top_n):
                return [types.SimpleNamespace(index=0, relevance_score=-2.),
                        types.SimpleNamespace(index=1, relevance_score=3.)]
        service = RerankService(FrameworkRerankBackend(lambda: Provider()), model="custom-rank")
        response = await service.rerank({"model": "custom-rank", "query": "x", "documents": ["a", "b"], "top_n": 1})
        self.assertEqual(response["results"], [{"index": 1, "relevance_score": 3.}])

    async def test_remote_rerank_partial_output_is_not_accepted(self):
        class Provider:
            async def rerank(self, query, documents, top_n):
                return [types.SimpleNamespace(index=0, relevance_score=1.)]
        with self.assertRaises(BridgeError):
            await RerankService(FrameworkRerankBackend(lambda: Provider()), model="r").rerank(
                {"query": "x", "documents": ["a", "b"]})


class SettingsTests(unittest.TestCase):
    def test_old_flat_settings_are_preserved(self):
        config = {"llm_provider_id": "already-configured", "port": 33333,
                  "embedding_model_dir": "D:/Models/BGE", "api_key": "existing-key"}
        normalize_config(config)
        self.assertEqual(config["llm_provider_id"], "already-configured")
        self.assertEqual(config["embedding_model_dir"], "D:/Models/BGE")
        self.assertEqual(config["port"], 33333)

    def test_connection_view_tracks_actual_runtime(self):
        config = default_config()
        config["port"] = 33333
        config["embedding_backend"].update(api_model="another-embed", dimensions=2)
        config["rerank_backend"]["api_model"] = "another-rank"
        info = connection_fields(config, "new-key", ROOT / "test-data")
        ef, rf = info["openai_embedding_fields"], info["vllm_rerank_fields"]
        self.assertEqual((ef["type"], ef["provider_type"]), ("openai_embedding", "embedding"))
        self.assertEqual((rf["type"], rf["provider_type"]), ("vllm_rerank", "rerank"))
        self.assertEqual(ef["origin_plugin"], "astrbot_plugin_llm_retrieval_bridge")
        self.assertEqual(ef["embedding_api_base"], "http://127.0.0.1:33333/v1")
        self.assertEqual(ef["embedding_model"], "another-embed")
        self.assertEqual(ef["embedding_dimensions"], 2)
        self.assertEqual(rf["rerank_model"], "another-rank")
        self.assertEqual(rf["rerank_api_key"], "new-key")

    def test_relative_and_absolute_paths(self):
        root = (ROOT / "test-data").resolve()
        self.assertEqual(resolve_model_path(root, "", "model"), root / "model")
        other = (ROOT / "elsewhere").resolve()
        self.assertEqual(resolve_model_path(root, str(other), "unused"), other)

    def test_self_recursion_is_rejected(self):
        for addr in ["http://127.0.0.1:32150/v1", "http://localhost:32150", "http://[::1]:32150/v1"]:
            with self.subTest(addr=addr), self.assertRaises(BridgeError):
                reject_self_provider(types.SimpleNamespace(provider_config={"embedding_api_base": addr}), 32150)
        reject_self_provider(types.SimpleNamespace(provider_config={"embedding_api_base": "http://127.0.0.1:32151/v1"}), 32150)

    def test_native_config_has_both_connection_sections(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        for name in ["openai_embedding_fields", "vllm_rerank_fields", "embedding_backend", "rerank_backend", "runtime_paths"]:
            self.assertEqual(schema[name]["type"], "object")
        self.assertTrue(schema["openai_embedding_fields"]["items"]["embedding_api_key"]["readonly"])
        self.assertTrue(schema["vllm_rerank_fields"]["items"]["rerank_api_key"]["readonly"])

    def test_rerank_cache_imports_only_selected_snapshot_and_preserves_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "cache.zip"
            repo = "models--Xenova--ms-marco-MiniLM-L-6-v2/"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(repo + "refs/main", "abc123")
                for name in ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "onnx/model.onnx"]:
                    z.writestr(repo + "snapshots/abc123/" + name, "test")
                z.writestr(repo + "snapshots/abc123/../../escape", "untrusted")
            imported = import_rerank_cache(archive, root / "rerank")
            self.assertIn("onnx/model.onnx", imported)
            self.assertTrue((root / "rerank/onnx/model.onnx").is_file())
            self.assertFalse((root / "escape").exists())
            with self.assertRaises(FileExistsError):
                import_rerank_cache(archive, root / "rerank")


def fake_astrbot_modules(data_dir):
    class BaseProvider:
        provider_config = {}
    class Chat(BaseProvider):
        pass
    class Embedding(BaseProvider):
        async def get_embeddings(self, texts):
            return [[.6, .8] for _ in texts]
    class Rerank(BaseProvider):
        async def rerank(self, query, documents, top_n):
            return [types.SimpleNamespace(index=i, relevance_score=i / max(1, len(documents)))
                    for i in range(len(documents))]
    class Config(dict):
        def __init__(self, values):
            super().__init__(values)
            self._save_state_lock = threading.Lock()
            self._save_commit_lock = threading.Lock()
        async def save_config_async(self):
            self.saved = True
    class Star:
        def __init__(self, context):
            self.context = context
    modules = {name: types.ModuleType(name) for name in [
        "astrbot", "astrbot.api", "astrbot.api.star", "astrbot.core", "astrbot.core.provider", "astrbot.core.provider.provider"]}
    modules["astrbot.api"].AstrBotConfig = Config
    modules["astrbot.api"].logger = types.SimpleNamespace(info=lambda *a, **kw: None, warning=lambda *a, **kw: None)
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = Star
    modules["astrbot.api.star"].StarTools = types.SimpleNamespace(get_data_dir=lambda: data_dir)
    modules["astrbot.api.star"].register = lambda *a, **kw: lambda cls: cls
    modules["astrbot.core.provider.provider"].Provider = Chat
    modules["astrbot.core.provider.provider"].EmbeddingProvider = Embedding
    modules["astrbot.core.provider.provider"].RerankProvider = Rerank
    class Context:
        def __init__(self):
            self.providers = {"chat": Chat(), "embed": Embedding(), "rank": Rerank()}
            self.requests = []
        def get_provider_by_id(self, pid):
            return self.providers.get(pid)
        async def llm_generate(self, **kwargs):
            self.requests.append(kwargs)
            payload = json.loads(kwargs["prompt"])
            if "texts" in payload:
                output = {"texts": payload["texts"]}
            else:
                output = {"scores": [{"index": row["index"], "score": .8} for row in payload["documents"]]}
            return types.SimpleNamespace(completion_text=json.dumps(output))
    return modules, Config, Context


class ModeFactoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_twelve_backend_combinations(self):
        from astrbot_plugin_llm_retrieval_bridge.factory import create_services
        class LocalRank:
            def __init__(self, *args, **kwargs):
                pass
            async def score(self, query, documents):
                return [{"index": i, "relevance_score": .7} for i in range(len(documents))]
            async def close(self):
                pass
        modules, _, Context = fake_astrbot_modules(ROOT)
        with patch.dict(sys.modules, modules), patch("astrbot_plugin_llm_retrieval_bridge.factory.FastEmbedBackend", TinyEncoder), patch("astrbot_plugin_llm_retrieval_bridge.factory.LocalRerankBackend", LocalRank):
            for em in [LOCAL_EMBEDDING, REMOTE_EMBEDDING, LLM_LOCAL_EMBEDDING, LLM_REMOTE_EMBEDDING]:
                for rm in [LOCAL_RERANK, REMOTE_RERANK, LLM_RERANK]:
                    with self.subTest(embedding=em, rerank=rm):
                        context = Context()
                        config = default_config()
                        config["llm_provider_id"] = "chat"
                        config["embedding_backend"].update(mode=em, dimensions=2, provider_id="embed")
                        config["rerank_backend"].update(mode=rm, provider_id="rank")
                        embedder, ranker = await create_services(context, config, ROOT)
                        try:
                            self.assertEqual(await embedder.embed(["x"]), [[.6, .8]])
                            self.assertEqual(len((await ranker.rerank({"query": "x", "documents": ["a", "b"]}))["results"]), 2)
                            for request in context.requests:
                                self.assertEqual(request["contexts"], [])
                                self.assertIsNone(request["tools"])
                        finally:
                            await embedder.close()
                            if hasattr(ranker, "close"):
                                await ranker.close()

    async def test_native_view_populates_and_port_is_released_on_reload(self):
        import socket
        from aiohttp import ClientSession
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            modules, Config, Context = fake_astrbot_modules(directory)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            with patch.dict(sys.modules, modules):
                main_name = "astrbot_plugin_llm_retrieval_bridge.main"
                sys.modules.pop(main_name, None)
                plugin_class = importlib.import_module(main_name).RetrievalBridgePlugin
                config = Config(default_config())
                config["port"] = port
                config["llm_provider_id"] = "chat"
                config["embedding_backend"].update(mode=REMOTE_EMBEDDING, provider_id="embed", dimensions=2)
                for cycle in range(2):
                    plugin = plugin_class(Context(), config)
                    try:
                        await plugin.initialize()
                        self.assertTrue(config.saved)
                        self.assertEqual(config["openai_embedding_fields"]["embedding_dimensions"], 2)
                        self.assertEqual(config["runtime_paths"]["data_dir"], str(directory.resolve()))
                        info = json.loads((directory / "connection-info.json").read_text(encoding="utf-8"))
                        self.assertEqual(info["vllm_rerank_fields"]["rerank_api_key"], config["api_key"])
                        async with ClientSession() as client:
                            async with client.get(f"http://127.0.0.1:{port}/v1/connection-info", headers={"Authorization": "Bearer " + config["api_key"]}) as response:
                                self.assertEqual(response.status, 200)
                                self.assertEqual((await response.json())["runtime_paths"]["data_dir"], str(directory.resolve()))
                    finally:
                        await plugin.terminate()
                        await plugin.terminate()
                sys.modules.pop(main_name, None)


if __name__ == "__main__":
    unittest.main()
