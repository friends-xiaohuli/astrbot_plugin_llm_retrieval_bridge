from __future__ import annotations

from pathlib import Path
from typing import Any

from .backends import (
    EmbeddingService, FrameworkEmbeddingBackend, FrameworkRerankBackend,
    LLMEnhancedEmbeddingBackend, LocalRerankBackend, RerankService,
)
from .bridge import BridgeError, FastEmbedBackend, LLMReranker, bounded_int
from .settings import (
    LLM_LOCAL_EMBEDDING, LLM_REMOTE_EMBEDDING, LLM_RERANK, LOCAL_EMBEDDING,
    LOCAL_RERANK, REMOTE_EMBEDDING, REMOTE_RERANK, reject_self_provider,
    model_paths,
)


async def create_services(context: Any, config: dict, data_dir: Path) -> tuple[Any, Any]:
    """Use public Context APIs; never wrap events or mutate provider registries."""
    from astrbot.core.provider.provider import EmbeddingProvider, Provider, RerankProvider

    port = bounded_int(config["port"], "port", 1024, 65535)
    ec, rc = config["embedding_backend"], config["rerank_backend"]
    paths = model_paths(config, data_dir)
    if ec["mode"] not in {LOCAL_EMBEDDING, REMOTE_EMBEDDING, LLM_LOCAL_EMBEDDING, LLM_REMOTE_EMBEDDING}:
        raise BridgeError("不支持的嵌入方式")
    if rc["mode"] not in {LLM_RERANK, LOCAL_RERANK, REMOTE_RERANK}:
        raise BridgeError("不支持的重排方式")
    for name in (ec["api_model"], rc["api_model"]):
        if not isinstance(name, str) or not name.strip():
            raise BridgeError("对外模型名称不能为空")

    def resolve(provider_id: str, kind: type) -> Any:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise BridgeError("尚未选择上游提供商ID", status=503, code="provider_not_ready")
        provider = context.get_provider_by_id(provider_id.strip())
        if not isinstance(provider, kind):
            raise BridgeError("所选提供商尚未就绪或类型不匹配", status=503, code="provider_not_ready")
        reject_self_provider(provider, port)
        return provider

    def chat_generator(provider_id: str):
        async def generate(prompt: str, system_prompt: str) -> str:
            resolve(provider_id, Provider)
            response = await context.llm_generate(
                chat_provider_id=provider_id, prompt=prompt,
                system_prompt=system_prompt, contexts=[], tools=None, temperature=0,
            )
            return response.completion_text
        return generate

    embedder = None
    encoder = None
    try:
        if ec["mode"] in {LOCAL_EMBEDDING, LLM_LOCAL_EMBEDDING}:
            encoder = FastEmbedBackend(
                Path(paths["embedding_model_dir"]),
                data_dir / "runtime-cache", threads=config["embedding_threads"],
                model_name=ec["local_model_name"], dimensions=ec["dimensions"], batch_size=ec["batch_size"],
            )
        else:
            encoder = FrameworkEmbeddingBackend(lambda: resolve(ec["provider_id"], EmbeddingProvider))
        if ec["mode"] in {LLM_LOCAL_EMBEDDING, LLM_REMOTE_EMBEDDING}:
            chat_id = ec["llm_provider_id"].strip() or config["llm_provider_id"].strip()
            encoder = LLMEnhancedEmbeddingBackend(
                chat_generator(chat_id), encoder, timeout=ec["llm_timeout_seconds"],
                batch_size=ec["llm_batch_size"], max_chars=ec["llm_max_chars"],
                cache_seconds=ec["llm_cache_seconds"],
            )
        embedder = EmbeddingService(encoder, model=ec["api_model"], dimensions=ec["dimensions"],
                                    timeout=config["embedding_timeout_seconds"])
        if rc["mode"] == LLM_RERANK:
            ranker = LLMReranker(
                chat_generator(config["llm_provider_id"]), model=rc["api_model"],
                timeout=config["rerank_timeout_seconds"], max_documents=config["max_rerank_documents"],
                max_document_chars=config["max_document_chars"], cache_seconds=config["rerank_cache_seconds"],
            )
        else:
            if rc["mode"] == LOCAL_RERANK:
                score_backend = LocalRerankBackend(
                    Path(paths["rerank_model_dir"]),
                    data_dir / "runtime-cache", model_name=rc["local_model_name"],
                    threads=rc["threads"], batch_size=rc["batch_size"], sigmoid=rc["sigmoid"],
                )
            else:
                score_backend = FrameworkRerankBackend(lambda: resolve(rc["provider_id"], RerankProvider))
            try:
                ranker = RerankService(
                    score_backend, model=rc["api_model"], timeout=config["rerank_timeout_seconds"],
                    max_documents=config["max_rerank_documents"], max_document_chars=config["max_document_chars"],
                )
            except BaseException:
                if callable(getattr(score_backend, "close", None)):
                    await score_backend.close()
                raise
        return embedder, ranker
    except BaseException:
        target = embedder or encoder
        if callable(getattr(target, "close", None)):
            await target.close()
        raise
