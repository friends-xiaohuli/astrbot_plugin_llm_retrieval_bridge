from __future__ import annotations

import asyncio
import copy
import json
import secrets
from pathlib import Path

from aiohttp import web
from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import Context, Star, StarTools, register

from .bridge import BridgeError, bounded_int
from .console import BridgeConsole
from .http_api import build_app
from .settings import connection_fields, normalize_config


@register("astrbot_plugin_llm_retrieval_bridge", "xiaohuli", "LLM与本地模型嵌入重排桥接", "0.4.1")
class RetrievalBridgePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.runner = None
        self.embedder = None
        self.reranker = None
        self.console = None

    async def initialize(self):
        if self.runner is not None:
            return
        normalize_config(self.config)
        bounded_int(self.config["port"], "port", 1024, 65535)
        data_dir = Path(StarTools.get_data_dir()).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        key = str(self.config["api_key"]).strip()
        if not key:
            key_path = data_dir / "bridge-key.txt"
            try:
                with key_path.open("x", encoding="utf-8") as output:
                    output.write(secrets.token_urlsafe(32))
            except FileExistsError:
                pass
            key = key_path.read_text(encoding="utf-8").strip()
            if not key:
                raise ValueError("bridge-key.txt为空，请填写插件api_key")
        self.config["api_key"] = key
        info = connection_fields(self.config, key, data_dir)
        for name, fields in info.items():
            self.config[name] = copy.deepcopy(fields)
        self.config["openai_embedding_fields"]["embedding_api_key"] = "在可视化视图中查看与复制"
        self.config["vllm_rerank_fields"]["rerank_api_key"] = "在可视化视图中查看与复制"
        # Save connection previews into native AstrBot settings, with secret fields masked by the UI.
        save_async = getattr(self.config, "save_config_async", None)
        if callable(save_async):
            await save_async()
        elif callable(getattr(self.config, "save_config", None)):
            self.config.save_config()
        connection_file = data_dir / "connection-info.json"
        connection_file.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            self.console = BridgeConsole(self.context, self.config, data_dir)
            await self.console.start()
            self.embedder = self.console.generation.embedder
            self.reranker = self.console.generation.reranker
            self.runner = web.AppRunner(
                build_app(self.reranker, self.embedder, lambda: self.console.active_config["api_key"],
                          connection_info=info, console=self.console),
                access_log=None,
            )
            await self.runner.setup()
            await web.TCPSite(self.runner, "127.0.0.1", self.config["port"]).start()
            self.register_view_apis()
        except BaseException:
            await self.terminate()
            raise
        logger.info("[RetrievalBridge] 已监听127.0.0.1:%s；嵌入=%s；重排=%s；对接参数=%s",
                    self.config["port"], self.config["embedding_backend"]["mode"],
                    self.config["rerank_backend"]["mode"], connection_file)

    def register_view_apis(self):
        register_api = getattr(self.context, "register_web_api", None)
        if not callable(register_api):
            return
        prefix = "/astrbot_plugin_llm_retrieval_bridge/"
        for path, handler, methods, desc in [
            ("settings", self.view_state, ["GET"], "重排和嵌入视图状态"),
            ("settings/preview", self.view_preview, ["POST"], "预览模型实际路径"),
            ("settings/save", self.view_save, ["POST"], "保存并应用后端配置"),
            ("settings/test", self.view_test, ["POST"], "测试当前或未保存配置"),
            ("settings/dependencies", self.view_dependencies, ["POST"], "使用框架安装插件requirements.txt"),
        ]:
            register_api(prefix + path, handler, methods, desc)

    async def _view_call(self, action):
        from astrbot.api.web import error_response, json_response, request
        if self.console is None:
            return error_response("插件未启动或已停止", status_code=503)
        try:
            if action == "state":
                result = await self.console.snapshot()
            else:
                payload = await request.json(default={})
                if not isinstance(payload, dict):
                    raise BridgeError("请求必须是JSON对象")
                result = await getattr(self.console, action)(payload)
            return json_response(result)
        except BridgeError as exc:
            return error_response(str(exc), status_code=exc.status)
        except Exception as exc:
            logger.warning("[RetrievalBridge] 视图操作失败：%s", type(exc).__name__)
            return error_response("操作失败，请检查模型文件、提供商状态和插件日志", status_code=500)

    async def view_state(self):
        return await self._view_call("state")

    async def view_preview(self):
        return await self._view_call("preview")

    async def view_save(self):
        return await self._view_call("save")

    async def view_test(self):
        return await self._view_call("test")

    async def view_dependencies(self):
        return await self._view_call("install_dependencies")

    async def terminate(self):
        runner, self.runner = self.runner, None
        embedder, self.embedder = self.embedder, None
        reranker, self.reranker = self.reranker, None
        console, self.console = self.console, None
        try:
            if runner is not None:
                await runner.cleanup()
        finally:
            close_calls = [console.close()] if console is not None else [obj.close() for obj in (embedder, reranker)
                           if callable(getattr(obj, "close", None))]
            if close_calls:
                results = await asyncio.gather(*close_calls, return_exceptions=True)
                if any(isinstance(item, BaseException) for item in results):
                    logger.warning("[RetrievalBridge] 部分后端停止时发生异常，请核对进程状态")
