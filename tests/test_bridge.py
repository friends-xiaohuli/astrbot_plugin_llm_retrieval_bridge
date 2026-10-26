from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE.parent))
from astrbot_plugin_llm_retrieval_bridge.bridge import (
    BridgeError, EMBEDDING_DIM, EMBEDDING_MODEL, LLMReranker, MODEL_FOLDER,
    REQUIRED_MODEL_FILES, embedding_response, import_model_cache, parse_scores,
)


class ScoreTests(unittest.TestCase):
    def test_indices_and_scores(self):
        rows = parse_scores('{"scores":[{"index":0,"score":0.1},{"index":1,"score":0.9}]}', 2)
        self.assertEqual([r["index"] for r in rows], [1, 0])

    def test_fenced_json(self):
        self.assertEqual(parse_scores('```json\n{"scores":[{"index":0,"score":1}]}\n```', 1)[0]["index"], 0)

    def test_duplicate_and_missing_and_invented_indices(self):
        for scores in [[{"index": 0, "score": .1}] * 2,
                       [{"index": 0, "score": .1}],
                       [{"index": 0, "score": .1}, {"index": 7, "score": .2}]]:
            with self.subTest(scores=scores), self.assertRaises(BridgeError):
                parse_scores(json.dumps({"scores": scores}), 2)

    def test_invalid_numeric_values(self):
        for value in [True, float("nan"), float("inf"), -.1, 1.1, "0.8"]:
            with self.subTest(value=value), self.assertRaises(BridgeError):
                parse_scores(json.dumps({"scores": [{"index": 0, "score": value}]}), 1)

    def test_no_explanation_or_malformed_json(self):
        for raw in ['结果如下：{"scores":[]}', '{}', '[]', '{"scores":', 'null']:
            with self.subTest(raw=raw), self.assertRaises(BridgeError):
                parse_scores(raw, 1)


class RerankTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        async def generate(prompt, system):
            self.calls.append((json.loads(prompt), system))
            return '{"scores":[{"index":0,"score":0.1},{"index":1,"score":0.9}]}'
        self.ranker = LLMReranker(generate)
        self.payload = {"query": "问题", "documents": ["第一条", "第二条"]}

    async def test_cache_and_top_n(self):
        a = await self.ranker.rerank({**self.payload, "top_n": 1})
        b = await self.ranker.rerank(self.payload)
        self.assertEqual(a["results"][0]["index"], 1)
        self.assertEqual(len(b["results"]), 2)
        self.assertEqual(len(self.calls), 1)

    async def test_parallel_identical_request_only_one_generation(self):
        await asyncio.gather(*(self.ranker.rerank(self.payload) for _ in range(6)))
        self.assertEqual(len(self.calls), 1)

    async def test_document_limit_is_explicit(self):
        with self.assertRaises(BridgeError):
            await self.ranker.rerank({"query": "x", "documents": ["x"] * 17})
        self.assertFalse(self.calls)

    async def test_empty_documents_does_not_call_model(self):
        self.assertEqual((await self.ranker.rerank({"query": "x", "documents": []}))["results"], [])
        self.assertFalse(self.calls)

    async def test_timeout_is_not_silent_fallback(self):
        async def slow(prompt, system):
            await asyncio.sleep(5)
        ranker = LLMReranker(slow, timeout=1)
        with self.assertRaises(BridgeError) as error:
            await ranker.rerank(self.payload)
        self.assertEqual(error.exception.status, 504)

    async def test_truncation_keeps_original_index(self):
        await self.ranker.rerank({**self.payload, "documents": ["x" * 900, "y"]})
        self.assertEqual(len(self.calls[0][0]["documents"][0]["text"]), 800)
        self.assertEqual(self.calls[0][0]["documents"][1]["index"], 1)


class DummyEmbedding:
    async def embed(self, texts):
        return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]


class EmbeddingTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_order_and_dimensions(self):
        response = await embedding_response({"input": ["甲", "乙"]}, DummyEmbedding())
        self.assertEqual([r["index"] for r in response["data"]], [0, 1])
        self.assertEqual(len(response["data"][0]["embedding"]), 512)

    async def test_token_input_is_rejected(self):
        with self.assertRaises(BridgeError):
            await embedding_response({"input": [101, 102]}, DummyEmbedding())

    async def test_wrong_dimensions_and_model(self):
        for extra in [{"dimensions": 384}, {"dimensions": True}, {"model": "other"}]:
            with self.subTest(extra=extra), self.assertRaises(BridgeError):
                await embedding_response({"input": "x", **extra}, DummyEmbedding())


class ImportTests(unittest.TestCase):
    def test_only_known_model_files_are_imported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive, destination = root / "cache.zip", root / "model"
            with zipfile.ZipFile(archive, "w") as z:
                for name in REQUIRED_MODEL_FILES:
                    content = json.dumps({"hidden_size": 512, "model_type": "bert"}) if name == "config.json" else "test"
                    z.writestr(MODEL_FOLDER + "/" + name, content)
                z.writestr("../escape.txt", "untrusted")
                z.writestr("models--other/model.onnx", "unused")
            imported = import_model_cache(archive, destination)
            self.assertEqual(set(imported), set(REQUIRED_MODEL_FILES))
            self.assertFalse((root / "escape.txt").exists())
            self.assertFalse((destination / "models--other").exists())
            with self.assertRaises(FileExistsError):
                import_model_cache(archive, destination)


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        from astrbot_plugin_llm_retrieval_bridge.http_api import build_app
        async def generate(prompt, system):
            return '{"scores":[{"index":0,"score":0.2},{"index":1,"score":0.9}]}'
        self.server = TestServer(build_app(LLMReranker(generate), DummyEmbedding(), "test-key"))
        self.client = TestClient(self.server)
        await self.client.start_server()
        self.auth = {"Authorization": "Bearer test-key"}

    async def asyncTearDown(self):
        await self.client.close()

    async def test_auth_required(self):
        response = await self.client.post("/v1/rerank", json={"query": "x", "documents": []})
        self.assertEqual(response.status, 401)

    async def test_vllm_response_contract(self):
        response = await self.client.post("/v1/rerank", headers=self.auth,
            json={"model": "llm-rerank", "query": "x", "documents": ["a", "b"], "top_n": 1})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["results"], [{"index": 1, "relevance_score": .9}])

    async def test_malformed_request_is_json_error(self):
        response = await self.client.post("/v1/rerank", headers=self.auth, data="{bad")
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["error"]["code"], "invalid_request")

    async def test_openai_sdk_default_base64_encoding(self):
        from openai import AsyncOpenAI
        sdk = AsyncOpenAI(api_key="test-key", base_url=str(self.server.make_url("/v1")))
        try:
            response = await sdk.embeddings.create(model=EMBEDDING_MODEL, input=["甲", "乙"])
            self.assertEqual(len(response.data), 2)
            self.assertEqual(response.data[0].embedding[0], 1.0)
            self.assertEqual(len(response.data[0].embedding), 512)
        finally:
            await sdk.close()


if __name__ == "__main__":
    unittest.main()
