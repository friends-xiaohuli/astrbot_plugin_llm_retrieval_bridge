from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_llm_retrieval_bridge.backends import LocalRerankBackend
from astrbot_plugin_llm_retrieval_bridge.bridge import BridgeError, FastEmbedBackend
from astrbot_plugin_llm_retrieval_bridge.runtime_dependencies import RuntimeDependencies, load_fastembed_class


class RuntimeProbeTests(unittest.TestCase):
    def test_package_files_do_not_prove_importability(self):
        missing_dependency = ModuleNotFoundError("No module named 'tokenizers'", name="tokenizers")
        with patch("importlib.util.find_spec", return_value=object()), \
             patch("importlib.metadata.version", return_value="0.9.1"), \
             patch("importlib.import_module", side_effect=missing_dependency):
            result = RuntimeDependencies(ROOT).status()
        self.assertTrue(result["detected"])
        self.assertFalse(result["available"])
        for capability in result["capabilities"].values():
            self.assertEqual(capability["code"], "local_runtime_import_failed")
            self.assertIn("tokenizers", capability["message"])
            self.assertNotIn("缺少FastEmbed", capability["message"])

    def test_only_absent_fastembed_is_reported_missing(self):
        for kind in ("embedding", "rerank"):
            with self.subTest(kind=kind), patch("importlib.import_module", side_effect=ModuleNotFoundError("No module named 'fastembed'", name="fastembed")):
                with self.assertRaises(BridgeError) as error:
                    load_fastembed_class(kind)
                self.assertEqual(error.exception.code, "local_runtime_missing")

    def test_incompatible_api_and_dll_errors_are_import_failures(self):
        for failure in (ImportError("cannot import TextEmbedding"), OSError("DLL load failed"), AttributeError("TextCrossEncoder")):
            with self.subTest(failure=failure), patch("importlib.import_module", side_effect=failure):
                with self.assertRaises(BridgeError) as error:
                    load_fastembed_class("embedding")
                self.assertEqual(error.exception.code, "local_runtime_import_failed")

    def test_status_imports_both_apis_without_loading_models(self):
        embedding, rerank = Mock(), Mock()
        def module(name):
            return types.SimpleNamespace(TextEmbedding=embedding) if name == "fastembed" else types.SimpleNamespace(TextCrossEncoder=rerank)
        with patch("importlib.util.find_spec", return_value=object()), \
             patch("importlib.metadata.version", return_value="0.9.1"), \
             patch("importlib.import_module", side_effect=module):
            result = RuntimeDependencies(ROOT).status()
        self.assertTrue(result["available"])
        embedding.assert_not_called()
        rerank.assert_not_called()


class LocalReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.backends = []

    async def asyncTearDown(self):
        for backend in self.backends:
            await backend.close()
        self.temp.cleanup()

    def backend(self, kind, *, complete=True):
        directory = self.root / kind
        directory.mkdir(exist_ok=True)
        if complete:
            for name in ("config.json", "tokenizer.json", "model.onnx"):
                (directory / name).write_text("fixture", encoding="utf-8")
        backend = (FastEmbedBackend(directory, self.root / "cache", model_name="test-model", dimensions=2)
                   if kind == "embedding" else LocalRerankBackend(directory, self.root / "cache", model_name="test-model"))
        self.backends.append(backend)
        model_class = Mock()
        model_class.list_supported_models.return_value = [{"model": "test-model", "dim": 2, "model_file": "model.onnx"}]
        return backend, model_class

    async def invoke(self, kind, backend, model_class):
        with patch("astrbot_plugin_llm_retrieval_bridge.runtime_dependencies.load_fastembed_class", return_value=model_class), \
             patch("astrbot_plugin_llm_retrieval_bridge.backends.load_fastembed_class", return_value=model_class):
            return await backend.embed(["text"]) if kind == "embedding" else await backend.score("query", ["text"])

    async def test_installed_runtime_with_missing_weights_is_a_model_error(self):
        for kind in ("embedding", "rerank"):
            backend, model_class = self.backend(kind, complete=False)
            with self.subTest(kind=kind), self.assertRaises(BridgeError) as error:
                await self.invoke(kind, backend, model_class)
            self.assertEqual(error.exception.code, f"{kind}_model_files_missing")
            self.assertIn("未准备就绪", str(error.exception))
            self.assertNotIn("运行库", str(error.exception))
            model_class.assert_not_called()
            self.assertFalse(backend.loaded)

    async def test_model_load_failure_reports_the_local_cause(self):
        for kind in ("embedding", "rerank"):
            backend, model_class = self.backend(kind)
            model_class.side_effect = ValueError("invalid ONNX graph")
            with self.subTest(kind=kind), self.assertLogs(level="WARNING"), self.assertRaises(BridgeError) as error:
                await self.invoke(kind, backend, model_class)
            self.assertEqual(error.exception.code, f"{kind}_model_load_failed")
            self.assertIn("invalid ONNX graph", str(error.exception))
            self.assertNotIn("安装/修复", str(error.exception))
            self.assertFalse(backend.loaded)

    async def test_ready_requires_successful_inference_and_failure_clears_it(self):
        for kind in ("embedding", "rerank"):
            backend, model_class = self.backend(kind)
            operation = model_class.return_value.embed if kind == "embedding" else model_class.return_value.rerank
            operation.return_value = [types.SimpleNamespace(tolist=lambda: [1., 0.])] if kind == "embedding" else [0.5]
            self.assertFalse(backend.loaded)

            await self.invoke(kind, backend, model_class)
            self.assertTrue(backend.loaded)
            def broken_iterator(*args, **kwargs):
                yield None
                raise ImportError("private-input-text")
            # FastEmbed returns generators: failures during iteration must be classified too.
            operation.side_effect = broken_iterator if kind == "rerank" else ImportError("private-input-text")
            with self.subTest(kind=kind), self.assertLogs(level="WARNING"), self.assertRaises(BridgeError) as error:
                await self.invoke(kind, backend, model_class)
            self.assertEqual(error.exception.code, f"{kind}_model_inference_failed")
            self.assertNotIn("private-input-text", str(error.exception))
            self.assertFalse(backend.loaded)


    async def test_zero_vector_does_not_mark_embedding_ready(self):
        backend, model_class = self.backend("embedding")
        model_class.return_value.embed.return_value = [types.SimpleNamespace(tolist=lambda: [0., 0.])]
        with self.assertRaises(BridgeError) as error:
            await self.invoke("embedding", backend, model_class)
        self.assertEqual(error.exception.code, "invalid_embedding")
        self.assertFalse(backend.loaded)


if __name__ == "__main__":
    unittest.main()
