"""Use the same provider-list service as GET /api/v1/providers."""
from __future__ import annotations

import ipaddress
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from .bridge import BridgeError
from .settings import reject_self_provider


def _location(config: dict) -> tuple[str, str]:
    base = next((config.get(k) for k in ("embedding_api_base", "rerank_api_base", "api_base", "base_url") if config.get(k)), "")
    if not isinstance(base, str) or not base:
        return "unknown", "接口位置依提供商配置"
    try:
        host = urlsplit(base).hostname or ""
    except ValueError:
        return "unknown", "接口地址配置无效"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if host == "localhost" or address is not None and address.is_loopback:
        return "local", "本机HTTP接口"
    if address is not None and address.is_private:
        return "lan", "局域网接口"
    return "network", "网络API接口"


def list_framework_providers(context: Any, listener_port: int) -> tuple[dict, str]:
    from astrbot.core.provider.provider import EmbeddingProvider, Provider, RerankProvider
    types = {"chat": Provider, "embedding": EmbeddingProvider, "rerank": RerankProvider}
    result = {kind: [] for kind in types}
    manager = getattr(context, "provider_manager", None)
    instances = getattr(manager, "inst_map", None) or getattr(context, "providers", {})
    rows = None
    if manager is not None:
        try:
            from astrbot.dashboard.services.config_service import ProviderConfigService
            config = context.get_config() if callable(getattr(context, "get_config", None)) else {}
            service = ProviderConfigService(SimpleNamespace(provider_manager=manager, astrbot_config=config))
            # This is the service called by the documented API and native ProviderSelectMenu.
            rows = {kind: service.list_providers(capability=kind)["providers"] for kind in types}
        except (ImportError, AttributeError):
            rows = None
    source = "GET /api/v1/providers 同一框架服务" if rows is not None else "框架已加载的提供商实例（兼容读取）"
    if rows is None:
        rows = {kind: [getattr(provider, "provider_config", {}) | {"id": pid}
                       for pid, provider in instances.items() if isinstance(provider, cls)]
                for kind, cls in types.items()}
    for kind, entries in rows.items():
        for pc in entries:
            if not isinstance(pc, dict) or pc.get("enable") is False or not pc.get("id"):
                continue
            pid = str(pc["id"])
            provider = instances.get(pid)
            disabled = not isinstance(provider, types[kind])
            reason = "提供商已配置，但尚未加载成功或能力类型不匹配" if disabled else ""
            if provider is not None:
                try:
                    reject_self_provider(provider, listener_port)
                except BridgeError:
                    disabled, reason = True, "指向本桥接自身，不能作为上游"
            model = next((pc.get(k) for k in ("embedding_model", "rerank_model", "model") if pc.get(k)), "")
            dimensions = pc.get("embedding_dimensions") if kind == "embedding" else None
            if kind == "embedding" and (type(dimensions) is not int or dimensions <= 0):
                getter = getattr(provider, "get_dim", None)
                try:
                    dimensions = getter() if callable(getter) else None
                except Exception:
                    dimensions = None
            if type(dimensions) is not int or dimensions <= 0:
                dimensions = None
            location, location_label = _location(pc)
            label = pid + (" · " + str(model) if model else "") + " · " + location_label
            # Never return API keys, headers, or unfiltered provider configuration to the iframe.
            result[kind].append({"id": pid, "label": label, "model": str(model),
                                 "source_id": str(pc.get("provider_source_id", "")),
                                 "location": location, "location_label": location_label,
                                 "dimensions": dimensions,
                                 "disabled": disabled, "reason": reason})
    return result, source
