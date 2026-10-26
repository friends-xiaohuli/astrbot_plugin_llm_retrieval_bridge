"""Configuration defaults shared by AstrBot's native settings view and runtime."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .bridge import BridgeError

LOCAL_EMBEDDING = "本地模型（FastEmbed）"
REMOTE_EMBEDDING = "框架嵌入提供商"
LLM_LOCAL_EMBEDDING = "LLM增强＋本地嵌入"
LLM_REMOTE_EMBEDDING = "LLM增强＋框架嵌入"
LLM_RERANK = "LLM评分"
LOCAL_RERANK = "本地模型（FastEmbed）"
REMOTE_RERANK = "框架重排提供商"


def configuration_data(config: Any) -> dict[str, Any]:
    """Copy JSON configuration entries, never the framework wrapper's locks/state."""
    return copy.deepcopy(dict(config))


def default_config() -> dict[str, Any]:
    schema = json.loads(Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8"))
    def parse(items: dict[str, Any]) -> dict[str, Any]:
        return {k: parse(v["items"]) if v["type"] == "object" else copy.deepcopy(v["default"])
                for k, v in items.items()}
    return parse(schema)


def normalize_config(config: Any) -> dict[str, Any]:
    """Keep the 0.1 flat settings; new backend fields are independently selectable."""
    defaults = default_config()
    for key, value in defaults.items():
        if key not in config:
            config[key] = copy.deepcopy(value)
        elif isinstance(value, dict):
            if not isinstance(config[key], dict):
                raise BridgeError(f"配置组{key}必须是对象")
            for child, default in value.items():
                config[key].setdefault(child, copy.deepcopy(default))
    storage = config.get("model_storage")
    if isinstance(storage, dict) and not config.get("_storage_migrated", False):
        old_embedding = config.get("embedding_model_dir", "")
        old_rerank = config.get("rerank_backend", {}).get("local_model_dir", "")
        if old_embedding not in {"", "fast-bge-small-zh-v1.5"} and not storage.get("embedding_dir"):
            storage["embedding_dir"] = old_embedding
        if old_rerank not in {"", "ms-marco-MiniLM-L-6-v2", "MiniLM-L-6-v2"} and not storage.get("rerank_dir"):
            storage["rerank_dir"] = old_rerank
        config["_storage_migrated"] = True
    return config


def resolve_model_path(data_dir: Path, configured: str, fallback: str) -> Path:
    value = configured.strip() or fallback
    path = Path(value).expanduser()
    return (path if path.is_absolute() else data_dir / path).resolve()


def model_paths(config: dict[str, Any], data_dir: Path, plugin_dir: Path | None = None) -> dict[str, Any]:
    """Honor explicit paths; auto-discover known existing layouts without moving files."""
    plugin_dir = plugin_dir or Path(__file__).resolve().parent
    storage = config.get("model_storage", {})
    root = resolve_model_path(data_dir, storage.get("root_dir", "fastembed"), "fastembed")
    candidates = {
        "embedding": [root / "fast-bge-small-zh-v1.5", plugin_dir / "fastembed/fast-bge-small-zh-v1.5", data_dir / "fast-bge-small-zh-v1.5"],
        "rerank": [root / "MiniLM-L-6-v2", plugin_dir / "fastembed/MiniLM-L-6-v2",
                   root / "ms-marco-MiniLM-L-6-v2", data_dir / "ms-marco-MiniLM-L-6-v2"],
    }
    result: dict[str, Any] = {"data_dir": str(data_dir.resolve()), "model_root": str(root)}
    def locate_snapshot(directory: Path, kind: str) -> Path:
        model_file = "model_optimized.onnx" if kind == "embedding" else "onnx/model.onnx"
        if (directory / model_file).is_file():
            return directory
        repos = [directory, directory / ("models--Qdrant--bge-small-zh-v1.5" if kind == "embedding" else "models--Xenova--ms-marco-MiniLM-L-6-v2")]
        for repo in repos:
            ref = repo / "refs/main"
            try:
                revision = ref.read_text(encoding="ascii").strip() if ref.is_file() else ""
            except (OSError, ValueError):
                revision = ""
            snapshots = [repo / "snapshots" / revision] if revision and all(c in "0123456789abcdef" for c in revision) else list((repo / "snapshots").glob("*"))
            usable = [path for path in snapshots if (path / model_file).is_file() and (path / "tokenizer.json").is_file()]
            if len(usable) == 1:
                return usable[0]
        return directory
    for kind, choices in candidates.items():
        override = storage.get(kind + "_dir", "")
        if "model_storage" not in config:
            override = config.get("embedding_model_dir", "") if kind == "embedding" else config.get("rerank_backend", {}).get("local_model_dir", "")
        if override:
            selected = locate_snapshot(resolve_model_path(data_dir, override, ""), kind)
            origin = "手动指定"
        else:
            resolved = [locate_snapshot(p, kind) for p in choices]
            required_model = "model_optimized.onnx" if kind == "embedding" else "onnx/model.onnx"
            selected = next((p for p in resolved if (p / required_model).is_file() and (p / "tokenizer.json").is_file()),
                            next((p for p in resolved if p.is_dir()), resolved[0]))
            origin = "统一fastembed目录" if selected == resolved[0] else "兼容现有目录（未移动文件）"
        required = ("config.json", "tokenizer.json", "model_optimized.onnx") if kind == "embedding" else ("config.json", "tokenizer.json", "onnx/model.onnx")
        result[kind + "_model_dir"] = str(selected.resolve())
        result[kind + "_origin"] = origin
        result[kind + "_directory_exists"] = selected.is_dir()
        result[kind + "_files_present"] = all((selected / name).is_file() for name in required)
    result["connection_file"] = str((data_dir / "connection-info.json").resolve())
    return result


def reject_self_provider(provider: Any, own_port: int) -> None:
    """A provider pointing back to this bridge would recurse until timeouts."""
    pc = getattr(provider, "provider_config", {})
    if not isinstance(pc, dict):
        return
    for key in ("embedding_api_base", "rerank_api_base", "api_base"):
        value = pc.get(key)
        if not isinstance(value, str):
            continue
        parsed = urlsplit(value)
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise BridgeError("上游提供商API地址端口无效", status=503, code="provider_not_ready") from exc
        if parsed.hostname in {"localhost", "127.0.0.1", "::1"} and port == own_port:
            raise BridgeError("上游提供商指向桥接自身，会形成递归；请选择真实上游提供商", status=503, code="recursive_provider")


def connection_fields(config: dict[str, Any], api_key: str, data_dir: Path) -> dict[str, Any]:
    port = config["port"]
    embedding = config["embedding_backend"]
    rerank = config["rerank_backend"]
    fields = {
        "openai_embedding_fields": {
            "type": "openai_embedding", "provider_type": "embedding", "provider_id": "bridge-embedding",
            "origin_plugin": "astrbot_plugin_llm_retrieval_bridge", "origin_plugin_name": "LLM重排与本地嵌入桥接",
            "embedding_api_base": f"http://127.0.0.1:{port}/v1",
            "endpoint": f"http://127.0.0.1:{port}/v1/embeddings",
            "embedding_api_key": api_key,
            "embedding_model": embedding["api_model"],
            "embedding_dimensions": embedding["dimensions"],
            "embedding_dimensions_mode": "never", "timeout": config["embedding_timeout_seconds"],
            "proxy": "", "custom_headers": "{}", "encoding_format": "float / base64",
        },
        "vllm_rerank_fields": {
            "type": "vllm_rerank", "provider_type": "rerank", "provider_id": "bridge-rerank",
            "origin_plugin": "astrbot_plugin_llm_retrieval_bridge", "origin_plugin_name": "LLM重排与本地嵌入桥接",
            "rerank_api_base": f"http://127.0.0.1:{port}", "rerank_api_suffix": "/v1/rerank",
            "endpoint": f"http://127.0.0.1:{port}/v1/rerank",
            "rerank_api_key": api_key, "rerank_model": rerank["api_model"],
            "timeout": config["rerank_timeout_seconds"] + 10, "custom_headers": "{}",
            "top_n": "由调用方指定，不超过候选数量",
        },
        "runtime_paths": model_paths(config, data_dir),
    }
    # These sections are connection previews, never input to the service configuration.
    for key in ("openai_embedding_fields", "vllm_rerank_fields"):
        current = config.get(key)
        if isinstance(current, dict):
            for name in ("provider_id",):
                if isinstance(current.get(name), str) and current[name].strip():
                    fields[key][name] = current[name].strip()
    return fields
