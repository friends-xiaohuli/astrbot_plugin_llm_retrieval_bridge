"""Publish bridge adapters through the same service as AstrBot's provider REST API."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from .bridge import BridgeError
from .settings import (
    LLM_LOCAL_EMBEDDING, LLM_REMOTE_EMBEDDING, LLM_RERANK, REMOTE_EMBEDDING,
    REMOTE_RERANK, connection_fields,
)

PLUGIN_ID = "astrbot_plugin_llm_retrieval_bridge"
PLUGIN_NAME = "LLM重排与本地嵌入桥接"
ORIGIN_KEY = "retrieval_bridge_origin"
KINDS = {"embedding": ("openai_embedding_fields", "openai_embedding", "嵌入"),
         "rerank": ("vllm_rerank_fields", "vllm_rerank", "重排")}


class FrameworkConnections:
    def __init__(self, context: Any, data_dir: Path, listener_port: int, *, service_factory: Callable | None = None):
        self.context, self.data_dir, self.listener_port = context, Path(data_dir), listener_port
        self.service_factory = service_factory
        self._lock = asyncio.Lock()
        self._registry = self.data_dir / "framework-connections.json"

    @staticmethod
    def _kind(kind: str) -> tuple[str, str, str]:
        if kind not in KINDS:
            raise BridgeError("对接类型必须是embedding或rerank")
        return KINDS[kind]

    def _service(self):
        manager = getattr(self.context, "provider_manager", None)
        if manager is None or not callable(getattr(manager, "get_provider_config_by_id", None)):
            raise BridgeError("当前框架未提供模型管理API，请检查AstrBot版本与提供商管理器", status=503, code="provider_api_unavailable")
        try:
            factory = self.service_factory
            if factory is None:
                from astrbot.dashboard.services.config_service import ProviderConfigService
                factory = ProviderConfigService
            getter = getattr(self.context, "get_config", None)
            config = getter() if callable(getter) else {}
            service = factory(SimpleNamespace(provider_manager=manager, astrbot_config=config))
        except (ImportError, AttributeError) as exc:
            raise BridgeError("当前框架的模型管理API尚未就绪", status=503, code="provider_api_unavailable") from exc
        if not all(callable(getattr(service, method, None)) for method in ("create_provider", "update_provider", "delete_provider")):
            raise BridgeError("当前框架缺少提供商创建、更新或删除API", status=503, code="provider_api_unavailable")
        return service, manager

    def _owner(self, *, create: bool = False) -> str | None:
        try:
            if self._registry.exists():
                if self._registry.stat().st_size > 8192:
                    raise ValueError("ownership registry too large")
                data = json.loads(self._registry.read_text(encoding="utf-8"))
                token = data.get("instance_id") if isinstance(data, dict) else None
                if data.get("plugin_id") != PLUGIN_ID or not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
                    raise ValueError("ownership registry invalid")
                return token
            if not create:
                return None
            self.data_dir.mkdir(parents=True, exist_ok=True)
            token = uuid.uuid4().hex
            try:
                with self._registry.open("x", encoding="utf-8") as output:
                    json.dump({"version": 1, "plugin_id": PLUGIN_ID, "instance_id": token}, output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
            except FileExistsError:
                return self._owner()
            return token
        except (OSError, ValueError, AttributeError) as exc:
            raise BridgeError("无法读取或保存本插件的提供商归属记录，请检查插件数据目录", status=503, code="provider_ownership_unavailable") from exc

    def _desired(self, kind: str, config: dict) -> dict:
        section, adapter, _ = self._kind(kind)
        applied = dict(config, port=self.listener_port)
        fields = connection_fields(applied, str(config["api_key"]), self.data_dir)[section]
        provider_id = fields["provider_id"]
        if not isinstance(provider_id, str) or not provider_id.strip() or len(provider_id) > 200 or any(ord(c) < 32 for c in provider_id):
            raise BridgeError("对接提供商ID不能为空、含控制字符或超过200字符", code="provider_id_invalid")
        other = "rerank" if kind == "embedding" else "embedding"
        other_fields = connection_fields(applied, str(config["api_key"]), self.data_dir)[KINDS[other][0]]
        if provider_id == other_fields["provider_id"]:
            raise BridgeError("嵌入和重排的对接提供商ID不能相同", status=409, code="provider_id_conflict")
        keys = (("embedding_api_base", "embedding_api_key", "embedding_model", "embedding_dimensions",
                 "embedding_dimensions_mode", "timeout", "proxy", "custom_headers") if kind == "embedding" else
                ("rerank_api_base", "rerank_api_key", "rerank_model", "rerank_api_suffix", "timeout", "custom_headers"))
        return {"id": provider_id, "type": adapter, "provider_type": kind, "enable": True,
                "display_name": f"{PLUGIN_NAME} · {KINDS[kind][2]}", **{key: fields[key] for key in keys}}

    @staticmethod
    def _reject_recursive(provider_id: str, config: dict) -> None:
        embedding, rerank = config["embedding_backend"], config["rerank_backend"]
        upstream = []
        if embedding["mode"] in {REMOTE_EMBEDDING, LLM_REMOTE_EMBEDDING}:
            upstream.append(embedding.get("provider_id", ""))
        if embedding["mode"] in {LLM_LOCAL_EMBEDDING, LLM_REMOTE_EMBEDDING}:
            upstream.append(embedding.get("llm_provider_id", "").strip() or config.get("llm_provider_id", ""))
        if rerank["mode"] == REMOTE_RERANK:
            upstream.append(rerank.get("provider_id", ""))
        if rerank["mode"] == LLM_RERANK:
            upstream.append(config.get("llm_provider_id", ""))
        if provider_id in upstream:
            raise BridgeError("桥接上游正在使用这个提供商ID，同步会形成递归；请先选择真实上游并保存", status=409, code="recursive_provider")

    @staticmethod
    def _current(manager, provider_id: str) -> dict | None:
        rows = getattr(manager, "providers_config", [])
        if sum(isinstance(row, dict) and row.get("id") == provider_id for row in rows) > 1:
            raise BridgeError("项目存在重复提供商ID，请先在框架模型页处理", status=409, code="provider_id_conflict")
        current = manager.get_provider_config_by_id(provider_id)
        return copy.deepcopy(current) if isinstance(current, dict) else None

    def _matches_published_target(self, row: dict, kind: str, marker: dict) -> bool:
        if row.get("provider_source_id") or row.get("type") != KINDS[kind][1] or row.get("provider_type") != kind:
            return False
        base, suffix, port = marker.get("api_base"), marker.get("api_suffix"), marker.get("port")
        if not isinstance(base, str) or type(port) is not int or not 1024 <= port <= 65535:
            return False
        if row.get(kind + "_api_base") != base:
            return False
        try:
            parsed = urlsplit(base)
            valid = (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                     and parsed.port == port and not parsed.username and not parsed.password
                     and not parsed.query and not parsed.fragment)
            if kind == "embedding":
                return valid and parsed.path == "/v1" and suffix == ""
            return valid and parsed.path == "" and suffix == "/v1/rerank" and row.get("rerank_api_suffix") == suffix
        except ValueError:
            return False

    def _managed(self, row: dict, kind: str, owner: str | None) -> bool:
        marker = row.get(ORIGIN_KEY)
        return (bool(owner) and isinstance(marker, dict) and marker.get("plugin_id") == PLUGIN_ID
                and marker.get("instance_id") == owner and marker.get("kind") == kind
                and self._matches_published_target(row, kind, marker))

    @staticmethod
    def _public_url(value: Any) -> str:
        try:
            parsed = urlsplit(str(value or ""))
            host = parsed.hostname or ""
            if ":" in host:
                host = "[" + host + "]"
            if parsed.port is not None:
                host += ":" + str(parsed.port)
            return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        except ValueError:
            return "接口地址无效"

    def _public_registration(self, row: dict, kind: str, manager, managed: bool) -> dict:
        return {"id": row.get("id", ""), "type": row.get("type", ""),
                "model": row.get(kind + "_model", ""), "enabled": bool(row.get("enable")),
                "api_base": self._public_url(row.get(kind + "_api_base")),
                "loaded": row.get("id") in getattr(manager, "inst_map", {}),
                "origin": {"plugin_id": PLUGIN_ID, "display_name": PLUGIN_NAME} if managed else None,
                "source": "由本插件同步" if managed else "项目已有配置，未归属本插件"}

    async def snapshot(self, config: dict) -> dict:
        api = {"available": True, "source": "AstrBot ProviderConfigService（与/api/v1/providers使用同一API服务）",
               "operations": [{"method": "GET", "path": "/api/v1/providers"},
                              {"method": "POST", "path": "/api/v1/providers"},
                              {"method": "PUT", "path": "/api/v1/providers/by-id"},
                              {"method": "DELETE", "path": "/api/v1/providers/by-id"}]}
        result = {"origin": {"plugin_id": PLUGIN_ID, "display_name": PLUGIN_NAME}, "api": api, "providers": {}}
        try:
            _, manager = self._service()
            owner = self._owner()
        except BridgeError as exc:
            api.update(available=False, message=str(exc))
            manager, owner = None, None
        for kind in KINDS:
            row = {"provider_id": "", "status": "unsupported", "managed": False,
                   "can_sync": False, "can_remove": False, "needs_sync": False, "registered": None,
                   "message": api.get("message", "")}
            try:
                desired = self._desired(kind, config)
                row["provider_id"] = desired["id"]
                if manager is None:
                    result["providers"][kind] = row
                    continue
                current = self._current(manager, desired["id"])
                managed = current is not None and self._managed(current, kind, owner)
                if current is None:
                    row.update(status="missing", can_sync=True, message="尚未同步到当前项目")
                elif managed:
                    changed = any(current.get(key) != value for key, value in desired.items())
                    row.update(status="managed", managed=True, can_sync=True, can_remove=True, needs_sync=changed,
                               message="参数已变化，可同步更新" if changed else "已同步到当前项目")
                else:
                    row.update(status="conflict", message="同名提供商不属于本插件，或已改变接口来源；不会覆盖或删除")
                if current is not None:
                    row["registered"] = self._public_registration(current, kind, manager, managed)
                    if managed and not row["registered"]["loaded"]:
                        row["message"] += "；框架实例尚未加载，请查看框架日志"
                try:
                    self._reject_recursive(desired["id"], config)
                except BridgeError as exc:
                    row.update(can_sync=False, message=str(exc))
            except BridgeError as exc:
                row.update(status="conflict", code=exc.code, can_sync=False, message=str(exc))
            result["providers"][kind] = row
        return result

    async def sync(self, kind: str, config: dict) -> dict:
        self._kind(kind)
        async with self._lock:
            service, manager = self._service()
            desired = self._desired(kind, config)
            self._reject_recursive(desired["id"], config)
            owner = self._owner()
            current = self._current(manager, desired["id"])
            if current is not None and not self._managed(current, kind, owner):
                raise BridgeError("同名提供商不属于本插件或接口来源已变化，不能同步覆盖", status=409, code="provider_ownership_conflict")
            owner = owner or self._owner(create=True)
            desired[ORIGIN_KEY] = {"plugin_id": PLUGIN_ID, "instance_id": owner, "kind": kind,
                                   "display_name": PLUGIN_NAME, "api_base": desired[kind + "_api_base"],
                                   "api_suffix": desired.get("rerank_api_suffix", ""), "port": self.listener_port}
            # Re-read immediately before the lifecycle API; the framework owns its own lock.
            current = self._current(manager, desired["id"])
            if current is not None and not self._managed(current, kind, owner):
                raise BridgeError("提供商配置已变化，请刷新对接状态后重试", status=409, code="provider_ownership_conflict")
            if (current is not None and desired["id"] in getattr(manager, "inst_map", {})
                    and all(current.get(key) == value for key, value in desired.items())):
                result = await self.snapshot(config)
                result.update(kind=kind, action="unchanged", message="当前项目中的对接参数已是最新，无需重复重载提供商")
                return result
            action = "updated" if current is not None else "created"
            try:
                if current is None:
                    await service.create_provider(desired)
                else:
                    await service.update_provider(desired["id"], {**current, **desired})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.getLogger(__name__).warning("框架提供商同步操作失败：%s", type(exc).__name__)
                raise BridgeError("框架同步提供商失败，请刷新注册状态并查看AstrBot日志", status=502, code="provider_sync_failed") from exc
            result = await self.snapshot(config)
            row = result["providers"][kind]
            result.update(kind=kind, action=action, message=row["message"])
            return result

    async def remove(self, kind: str, config: dict) -> dict:
        self._kind(kind)
        async with self._lock:
            service, manager = self._service()
            desired = self._desired(kind, config)
            current = self._current(manager, desired["id"])
            if current is None:
                return {**await self.snapshot(config), "kind": kind, "action": "missing", "message": "当前项目中已没有该提供商"}
            if not self._managed(current, kind, self._owner()):
                raise BridgeError("该提供商不归属本插件或接口来源已变化，不能从项目删除", status=409, code="provider_ownership_conflict")
            try:
                await service.delete_provider(desired["id"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.getLogger(__name__).warning("框架提供商删除操作失败：%s", type(exc).__name__)
                raise BridgeError("框架删除提供商失败，请刷新注册状态并查看AstrBot日志", status=502, code="provider_remove_failed") from exc
            return {**await self.snapshot(config), "kind": kind, "action": "removed", "message": "已从当前项目删除本插件的提供商配置"}
