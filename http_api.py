from __future__ import annotations

import asyncio
import hmac
from typing import Any

from aiohttp import web

from .bridge import BridgeError, EMBEDDING_DIM, EMBEDDING_MODEL, embedding_response


def build_app(reranker: Any, embedder: Any, api_key: Any, *, connection_info: dict | None = None, console: Any = None) -> web.Application:
    if not (api_key() if callable(api_key) else api_key):
        raise ValueError("桥接密钥不能为空")

    @web.middleware
    async def auth_errors(request: web.Request, handler: Any) -> web.StreamResponse:
        supplied = request.headers.get("Authorization", "").encode()
        current_key = api_key() if callable(api_key) else api_key
        expected = ("Bearer " + current_key).encode()
        if not hmac.compare_digest(supplied, expected):
            return web.json_response({"error": {"code": "unauthorized", "message": "桥接密钥不匹配"}}, status=401)
        try:
            return await handler(request)
        except BridgeError as exc:
            return web.json_response({"error": {"code": exc.code, "message": str(exc)}}, status=exc.status)
        except asyncio.CancelledError:
            raise
        except web.HTTPException:
            raise
        except Exception:
            # Avoid serializing provider exceptions which may contain credentials or prompts.
            return web.json_response({"error": {"code": "internal_error", "message": "桥接内部错误，请检查模型目录与运行库"}}, status=500)

    app = web.Application(middlewares=[auth_errors], client_max_size=512 * 1024)

    async def payload(request: web.Request) -> dict[str, Any]:
        try:
            obj = await request.json()
        except (ValueError, UnicodeError) as exc:
            raise BridgeError("请求正文必须是合法JSON") from exc
        if not isinstance(obj, dict):
            raise BridgeError("请求正文必须是JSON对象")
        return obj

    async def rerank(request: web.Request) -> web.Response:
        if console is not None:
            body = await payload(request)
            async with console.lease() as (_, active_reranker):
                return web.json_response(await active_reranker.rerank(body))
        return web.json_response(await reranker.rerank(await payload(request)))

    async def embeddings(request: web.Request) -> web.Response:
        if console is not None:
            body = await payload(request)
            async with console.lease() as (active_embedder, _):
                return web.json_response(await embedding_response(body, active_embedder))
        return web.json_response(await embedding_response(await payload(request), embedder))

    async def health(request: web.Request) -> web.Response:
        active_embedder = console.generation.embedder if console is not None and console.generation else embedder
        active_reranker = console.generation.reranker if console is not None and console.generation else reranker
        backend = getattr(active_reranker, "backend", active_reranker)
        return web.json_response({"status": "listening", "model_test_required": True,
                                  "embedding_model": getattr(active_embedder, "model", EMBEDDING_MODEL),
                                  "embedding_dimensions": getattr(active_embedder, "dimensions", EMBEDDING_DIM),
                                  "embedding_loaded": bool(getattr(active_embedder, "loaded", False)),
                                  "rerank_loaded": bool(getattr(backend, "loaded", False)),
                                  "rerank_model": getattr(active_reranker, "model", "llm-rerank")})

    async def connections(request: web.Request) -> web.Response:
        if console is not None:
            state = await console.snapshot()
            return web.json_response({**state["connections"], "runtime_paths": state["paths"]})
        return web.json_response(connection_info or {})

    async def models(request: web.Request) -> web.Response:
        active_embedder = console.generation.embedder if console is not None and console.generation else embedder
        active_reranker = console.generation.reranker if console is not None and console.generation else reranker
        return web.json_response({"object": "list", "data": [
            {"id": getattr(active_embedder, "model", EMBEDDING_MODEL), "object": "model", "owned_by": "retrieval-bridge", "capability": "embedding"},
            {"id": getattr(active_reranker, "model", "llm-rerank"), "object": "model", "owned_by": "retrieval-bridge", "capability": "rerank"},
        ]})

    if console is not None:
        async def console_state(request):
            return web.json_response(await console.snapshot())
        async def console_preview(request):
            return web.json_response(await console.preview(await payload(request)))
        async def console_save(request):
            return web.json_response(await console.save(await payload(request)))
        async def console_test(request):
            return web.json_response(await console.test(await payload(request)))
        async def console_dependencies(request):
            await payload(request)
            return web.json_response(await console.install_dependencies())
        app.router.add_get("/console/state", console_state)
        app.router.add_post("/console/preview", console_preview)
        app.router.add_post("/console/save", console_save)
        app.router.add_post("/console/test", console_test)
        app.router.add_post("/console/dependencies", console_dependencies)

    app.router.add_get("/health", health)
    app.router.add_get("/v1/models", models)
    app.router.add_get("/v1/connection-info", connections)
    app.router.add_post("/v1/rerank", rerank)
    app.router.add_post("/rerank", rerank)
    app.router.add_post("/v1/embeddings", embeddings)
    return app
