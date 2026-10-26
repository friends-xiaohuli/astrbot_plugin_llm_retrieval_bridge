"""Business API shared by the official AstrBot Plugin View and local tests."""
from __future__ import annotations

import asyncio
import copy
import json
import math
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .bridge import BridgeError, bounded_int, embedding_response
from .factory import create_services
from .settings import (
    LOCAL_EMBEDDING, LOCAL_RERANK, LLM_RERANK, connection_fields,
    configuration_data, default_config, model_paths, normalize_config,
)

DERIVED = {"openai_embedding_fields", "vllm_rerank_fields", "runtime_paths", "view_entry", "_storage_migrated"}
LEGACY = {"embedding_model_dir"}
EDITABLE_ROOTS = {"port", "api_key", "embedding_backend", "rerank_backend", "model_storage",
                  "llm_provider_id", "embedding_threads", "embedding_timeout_seconds",
                  "rerank_timeout_seconds", "max_rerank_documents", "max_document_chars", "rerank_cache_seconds"}


def editable_settings(config: dict) -> dict:
    out = {k: copy.deepcopy(v) for k, v in config.items() if k in EDITABLE_ROOTS}
    out.get("rerank_backend", {}).pop("local_model_dir", None)
    return out


def merge_settings(base: dict, patch: dict) -> dict:
    if not isinstance(patch, dict) or any(k not in EDITABLE_ROOTS for k in patch):
        raise BridgeError("只允许修改配置项；对接展示与实际路径为只读")
    result = configuration_data(base)
    defaults = default_config()
    for key, value in patch.items():
        if isinstance(defaults[key], dict):
            if not isinstance(value, dict):
                raise BridgeError(f"{key}必须是配置对象")
            allowed = set(defaults[key])
            if key == "rerank_backend":
                allowed.discard("local_model_dir")
            if any(child not in allowed for child in value):
                raise BridgeError(f"{key}包含未知或只读配置")
            result[key].update(copy.deepcopy(value))
        else:
            result[key] = value
    # An empty key in the editor means keep the existing key; keys are not auto-rotated.
    if not result.get("api_key"):
        result["api_key"] = base.get("api_key", "")
    for key, value in editable_settings(result).items():
        expected = defaults[key]
        children = value.items() if isinstance(expected, dict) else [(key, value)]
        for child, current in children:
            sample = expected[child] if isinstance(expected, dict) else expected
            if type(current) is not type(sample):
                raise BridgeError(f"配置{key}.{child}类型错误")
            if isinstance(current, str) and len(current) > 4000:
                raise BridgeError("配置文本过长")
    ranges = {
        "port": (1024, 65535), "embedding_threads": (1, 4), "embedding_timeout_seconds": (1, 180),
        "rerank_timeout_seconds": (1, 120), "max_rerank_documents": (1, 64),
        "max_document_chars": (64, 4096), "rerank_cache_seconds": (0, 3600),
    }
    for key, (low, high) in ranges.items():
        bounded_int(result[key], key, low, high)
    e, r = result["embedding_backend"], result["rerank_backend"]
    for group, bounds in [(e, {"dimensions": (1, 8192), "batch_size": (1, 64), "llm_timeout_seconds": (1, 120),
                              "llm_batch_size": (1, 16), "llm_max_chars": (64, 16000), "llm_cache_seconds": (0, 86400)}),
                           (r, {"threads": (1, 4), "batch_size": (1, 32)})]:
        for key, (low, high) in bounds.items():
            bounded_int(group[key], key, low, high)
    schema = json.loads(Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8"))
    for key in ("embedding_backend", "rerank_backend"):
        if result[key]["mode"] not in schema[key]["items"]["mode"]["options"]:
            raise BridgeError("不支持的后端方式")
        if not result[key]["api_model"].strip():
            raise BridgeError("对外模型名称不能为空")
    normalize_config(result)
    return result


def recommendations() -> list[dict]:
    return [
        {"id": "balanced", "title": "中文记忆 · 本地嵌入＋LLM重排", "badge": "推荐",
         "description": "BGE负责稳定编码，DS4.1F比较少量候选；会增加一次LLM调用。2核2GB先用1线程、8–16条候选。",
         "patch": {"embedding_backend": {"mode": LOCAL_EMBEDDING, "local_model_name": "BAAI/bge-small-zh-v1.5", "dimensions": 512},
                   "rerank_backend": {"mode": LLM_RERANK}, "embedding_threads": 1, "max_rerank_documents": 16}},
        {"id": "local", "title": "全本地 · 优先减少延迟", "badge": "无云端调用",
         "description": "使用缓存中的BGE和MiniLM。MiniLM主要面向英语；中文效果请用下方真实查询测试后决定。",
         "patch": {"embedding_backend": {"mode": LOCAL_EMBEDDING, "local_model_name": "BAAI/bge-small-zh-v1.5", "dimensions": 512},
                   "rerank_backend": {"mode": LOCAL_RERANK, "local_model_name": "Xenova/ms-marco-MiniLM-L-6-v2", "threads": 1, "batch_size": 4},
                   "embedding_threads": 1, "max_rerank_documents": 16}},
    ]


@dataclass
class Generation:
    embedder: Any
    reranker: Any
    config: dict
    users: int = 0
    drained: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self):
        self.drained.set()


class BridgeConsole:
    def __init__(self, context: Any, config: dict, data_dir: Path, *, service_factory: Callable = create_services):
        self.context, self.config, self.data_dir = context, config, data_dir
        self.service_factory = service_factory
        self.generation: Generation | None = None
        self.listener_port = config["port"]
        self.revision = 1
        self._apply_lock = asyncio.Lock()
        self._retired: set[asyncio.Task] = set()
        self._stopping = False
        self._test_lock = asyncio.Lock()

    async def start(self):
        snapshot = configuration_data(self.config)
        embedder, reranker = await self.service_factory(self.context, snapshot, self.data_dir)
        self.generation = Generation(embedder, reranker, snapshot)

    @property
    def active_config(self) -> dict:
        return self.generation.config if self.generation is not None else self.config

    @asynccontextmanager
    async def lease(self):
        generation = self.generation
        if self._stopping or generation is None:
            raise BridgeError("桥接尚未就绪或已停止", status=503, code="bridge_not_ready")
        generation.users += 1
        generation.drained.clear()
        try:
            yield generation.embedder, generation.reranker
        finally:
            generation.users -= 1
            if not generation.users:
                generation.drained.set()

    async def _close_generation(self, generation: Generation):
        await generation.drained.wait()
        tasks = [obj.close() for obj in (generation.embedder, generation.reranker)
                 if callable(getattr(obj, "close", None))]
        if tasks:
            await asyncio.gather(*tasks)

    def _retire(self, generation: Generation):
        task = asyncio.create_task(self._close_generation(generation))
        self._retired.add(task)
        def completed(done):
            self._retired.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(completed)

    async def providers(self) -> dict:
        result = {"chat": [], "embedding": [], "rerank": []}
        try:
            from astrbot.core.provider.provider import EmbeddingProvider, Provider, RerankProvider
            manager = getattr(self.context, "provider_manager", None)
            instances = getattr(manager, "inst_map", {})
            if not instances:
                instances = getattr(self.context, "providers", {})
            for pid, provider in instances.items():
                kind = "embedding" if isinstance(provider, EmbeddingProvider) else "rerank" if isinstance(provider, RerankProvider) else "chat" if isinstance(provider, Provider) else None
                if kind:
                    result[kind].append({"id": str(pid), "label": str(pid)})
        except ImportError:
            pass
        return result

    async def snapshot(self, config: dict | None = None) -> dict:
        selected = config or self.config
        info = connection_fields(self.active_config, self.active_config["api_key"], self.data_dir)
        return {
            "version": "0.3.1", "revision": self.revision, "settings": editable_settings(selected),
            "connections": {k: v for k, v in info.items() if k != "runtime_paths"},
            "paths": model_paths(selected, self.data_dir), "providers": await self.providers(),
            "recommendations": recommendations(), "requires_restart": selected["port"] != self.listener_port,
            "listener_port": self.listener_port,
            "active_modes": {"embedding": self.active_config["embedding_backend"]["mode"], "rerank": self.active_config["rerank_backend"]["mode"]},
        }

    async def preview(self, payload: dict) -> dict:
        candidate = merge_settings(self.config, payload.get("settings", {}))
        return {"paths": model_paths(candidate, self.data_dir), "requires_restart": candidate["port"] != self.listener_port}

    async def save(self, payload: dict) -> dict:
        async with self._apply_lock:
            if self._stopping:
                raise BridgeError("插件正在停止", status=503)
            if payload.get("revision") != self.revision:
                raise BridgeError("配置已在其他页面更新，请刷新后再保存", status=409, code="stale_revision")
            candidate = merge_settings(self.config, payload.get("settings", {}))
            active = configuration_data(candidate)
            active["port"] = self.listener_port
            embedder, reranker = await self.service_factory(self.context, active, self.data_dir)
            fresh = Generation(embedder, reranker, active)
            previous = configuration_data(self.config)
            try:
                info = connection_fields(active, active["api_key"], self.data_dir)
                for key, value in info.items():
                    candidate[key] = copy.deepcopy(value)
                # Derived API keys are copied in the View, not editable native fields.
                candidate["openai_embedding_fields"]["embedding_api_key"] = "在可视化视图中查看与复制"
                candidate["vllm_rerank_fields"]["rerank_api_key"] = "在可视化视图中查看与复制"
                self.config.clear()
                self.config.update(candidate)
                saver = getattr(self.config, "save_config_async", None)
                if callable(saver):
                    await saver()
                elif callable(getattr(self.config, "save_config", None)):
                    self.config.save_config()
                (self.data_dir / "connection-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            except BaseException:
                self.config.clear()
                self.config.update(previous)
                await self._close_generation(fresh)
                raise
            old, self.generation = self.generation, fresh
            self.revision += 1
            if old is not None:
                self._retire(old)
            state = await self.snapshot()
            state["message"] = "配置已应用；端口变更需重启插件" if state["requires_restart"] else "已保存并应用"
            return state

    async def test(self, payload: dict) -> dict:
        if self._test_lock.locked():
            raise BridgeError("已有测试在进行，请等待完成", status=409, code="test_busy")
        kind = payload.get("kind")
        if kind not in {"embedding", "rerank"}:
            raise BridgeError("测试类型必须是embedding或rerank")
        settings = payload.get("settings")
        candidate = merge_settings(self.config, settings) if settings is not None else self.active_config
        candidate = configuration_data(candidate)
        candidate["port"] = self.listener_port
        temporary = editable_settings(candidate) != editable_settings(self.active_config)
        async with self._test_lock:
            started = time.perf_counter()
            if temporary:
                embedder, reranker = await self.service_factory(self.context, candidate, self.data_dir)
                try:
                    output = await self._run_test(kind, payload, embedder, reranker)
                finally:
                    tasks = [obj.close() for obj in (embedder, reranker) if callable(getattr(obj, "close", None))]
                    if tasks:
                        await asyncio.gather(*tasks)
            else:
                async with self.lease() as (embedder, reranker):
                    output = await self._run_test(kind, payload, embedder, reranker)
            output.update(elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                          configuration="未保存配置（临时测试）" if temporary else "当前已应用配置")
            return output

    async def _run_test(self, kind: str, payload: dict, embedder: Any, reranker: Any) -> dict:
        if kind == "embedding":
            texts = payload.get("texts", ["我喜欢喝绿茶", "他爱喝茶", "服务器磁盘空间不足"])
            if not isinstance(texts, list) or not 1 <= len(texts) <= 8 or any(not isinstance(t, str) or len(t) > 4000 for t in texts):
                raise BridgeError("测试需提供1到8条文本，每条最多4000字符")
            response = await embedding_response({"input": texts, "model": embedder.model}, embedder)
            vectors = [row["embedding"] for row in response["data"]]
            norms = [math.sqrt(sum(x * x for x in row)) for row in vectors]
            cosine = [[round(sum(a * b for a, b in zip(x, y)) / (norms[i] * norms[j]), 5)
                       for j, y in enumerate(vectors)] for i, x in enumerate(vectors)]
            return {"kind": kind, "model": embedder.model, "count": len(vectors),
                    "dimensions": [len(v) for v in vectors], "norms": norms, "cosine_similarity": cosine,
                    "sample_first_8": vectors[0][:8]}
        query = payload.get("query", "用户喜欢喝什么饮料？")
        documents = payload.get("documents", ["用户喜欢喝绿茶", "服务器磁盘不足", "用户喜欢跑步"])
        if not isinstance(documents, list) or not 1 <= len(documents) <= 16:
            raise BridgeError("重排测试需提供1到16条候选")
        response = await reranker.rerank({"model": reranker.model, "query": query, "documents": documents})
        return {"kind": kind, "model": reranker.model,
                "results": [{**row, "text": documents[row["index"]]} for row in response["results"]]}

    async def close(self):
        self._stopping = True
        async with self._apply_lock:
            current, self.generation = self.generation, None
            if current is not None:
                await self._close_generation(current)
            if self._retired:
                await asyncio.gather(*self._retired, return_exceptions=True)
