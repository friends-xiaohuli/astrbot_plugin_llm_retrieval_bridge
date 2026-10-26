"""Discover selectable ONNX models from existing directories, without downloading."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .bridge import BridgeError
from .settings import LLM_LOCAL_EMBEDDING, LOCAL_EMBEDDING, LOCAL_RERANK, model_paths


@lru_cache(maxsize=1)
def supported_descriptions() -> dict[str, list[dict]]:
    try:
        from fastembed import TextEmbedding
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        return {"embedding": TextEmbedding.list_supported_models(), "rerank": TextCrossEncoder.list_supported_models()}
    except Exception:
        # Directory discovery remains available while the user repairs runtime dependencies.
        return {
            "embedding": [{"model": "BAAI/bge-small-zh-v1.5", "dim": 512, "model_file": "model_optimized.onnx", "additional_files": []}],
            "rerank": [{"model": "Xenova/ms-marco-MiniLM-L-6-v2", "model_file": "onnx/model.onnx", "additional_files": []}],
        }


def clear_model_descriptions_cache() -> None:
    supported_descriptions.cache_clear()


def _canonical(name: str) -> str:
    return name.lower().replace("cross-encoder/ms-marco-", "xenova/ms-marco-").strip()


def _actual_directories(directory: Path) -> list[Path]:
    if (directory / "config.json").is_file():
        return [directory]
    snapshots = directory / "snapshots"
    ref = directory / "refs/main"
    try:
        revision = ref.read_text(encoding="ascii").strip() if ref.is_file() else ""
    except (OSError, ValueError):
        revision = ""
    if revision and all(c in "0123456789abcdef" for c in revision):
        choices = [snapshots / revision]
    else:
        choices = sorted(snapshots.glob("*"))[:8]
    return [path for path in choices if (path / "config.json").is_file()]


def _directory_label(directory: Path, model_root: Path, data_dir: Path, plugin_dir: Path) -> tuple[str, str]:
    """Show a model's logical directory, keeping absolute paths for the data page."""
    roots = (model_root, plugin_dir / "fastembed", data_dir / "fastembed", data_dir)
    parts = None
    for root in roots:
        root = root.resolve()
        try:
            relative = directory.relative_to(root)
        except ValueError:
            continue
        parts = ([] if root == data_dir.resolve() else [root.name]) + list(relative.parts)
        break
    if parts is None:
        # Explicit overrides may live outside both the plugin and configured root.
        parts = list(directory.parts)
        fastembed = next((i for i in range(len(parts) - 1, -1, -1) if parts[i].lower() == "fastembed"), None)
        if fastembed is not None:
            parts = parts[fastembed:]
        else:
            snapshot = next((i for i, part in enumerate(parts) if part == "snapshots"), len(parts))
            parts = parts[max(0, snapshot - 1):snapshot] if snapshot < len(parts) else parts[-1:]
    if "snapshots" in parts:
        parts = parts[:parts.index("snapshots")]
        if len(parts) > 2 and parts[-1].startswith("models--"):
            parts.pop()
    source = "自定义目录"
    sources = sorted(((data_dir.resolve(), "插件数据"), (plugin_dir.resolve(), "插件目录")), key=lambda row: len(row[0].parts), reverse=True)
    for root, label in sources:
        if directory.is_relative_to(root):
            source = label
            break
    return "\\".join(parts) or directory.name, source


def _distinguish_labels(rows: list[dict]) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["label"]] = counts.get(row["label"], 0) + 1
    duplicates: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        if counts[row["label"]] > 1:
            duplicates.setdefault((row["label"], row["source"]), []).append(row)
    for (label, source), choices in duplicates.items():
        for index, row in enumerate(choices, 1):
            suffix = source if len(choices) == 1 else f"{source} {index}"
            row["label"] = f"{label}（{suffix}）"


def discover_local_models(config: dict, data_dir: Path, plugin_dir: Path | None = None) -> dict[str, Any]:
    plugin_dir = plugin_dir or Path(__file__).resolve().parent
    paths = model_paths(config, data_dir, plugin_dir)
    roots = [Path(paths["model_root"]), plugin_dir / "fastembed"]
    candidates = [Path(paths["embedding_model_dir"]), Path(paths["rerank_model_dir"]),
                  data_dir / "fast-bge-small-zh-v1.5", data_dir / "ms-marco-MiniLM-L-6-v2"]
    for root in roots:
        if root.is_dir():
            candidates.append(root)
            try:
                candidates.extend(sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)[:128])
            except OSError:
                pass
    descriptions = supported_descriptions()
    result: dict[str, Any] = {"embedding": [], "rerank": [], "scanned_roots": [str(p.resolve()) for p in roots]}
    seen = set()
    for candidate in candidates:
        for directory in _actual_directories(candidate):
            resolved = directory.resolve()
            identity = str(resolved).casefold()
            if identity in seen:
                continue
            seen.add(identity)
            try:
                metadata_file = resolved / "config.json"
                if metadata_file.stat().st_size > 2_000_000:
                    continue
                metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(metadata, dict):
                continue
            source_name = _canonical(str(metadata.get("_name_or_path", "")))
            architectures = metadata.get("architectures", [])
            if not isinstance(architectures, list):
                continue
            classifier = any("SequenceClassification" in str(v) for v in architectures)
            kind = "rerank" if classifier else "embedding"
            for description in descriptions[kind]:
                model = description["model"]
                names = {_canonical(model)}
                sources = description.get("sources", {})
                if isinstance(sources, dict) and sources.get("hf"):
                    names.add(_canonical(sources["hf"]))
                folder_name = resolved.name.lower().removeprefix("fast-")
                if source_name not in names and folder_name != model.rsplit("/", 1)[-1].lower():
                    continue
                required = ["config.json", "tokenizer.json", description["model_file"], *description.get("additional_files", [])]
                try:
                    complete = all((resolved / name).is_file() and (resolved / name).stat().st_size > 0 for name in required)
                except OSError:
                    complete = False
                if not complete:
                    continue
                dimensions = description.get("dim")
                if kind == "embedding" and (type(dimensions) is not int or dimensions <= 0):
                    continue
                key = hashlib.sha256((kind + "\0" + model + "\0" + identity).encode()).hexdigest()[:16]
                label, source = _directory_label(resolved, Path(paths["model_root"]), data_dir, plugin_dir)
                result[kind].append({"key": key, "model": model, "directory": str(resolved),
                                     "dimensions": dimensions, "label": label, "source": source})
                break
    for kind in ("embedding", "rerank"):
        _distinguish_labels(result[kind])
    return result


def validate_local_selection(config: dict, catalog: dict, paths: dict) -> None:
    active = []
    if config["embedding_backend"]["mode"] in {LOCAL_EMBEDDING, LLM_LOCAL_EMBEDDING}:
        active.append("embedding")
    if config["rerank_backend"]["mode"] == LOCAL_RERANK:
        active.append("rerank")
    for kind in active:
        group = config[kind + "_backend"]
        matches = [row for row in catalog[kind]
                   if row["model"] == group["local_model_name"] and Path(row["directory"]) == Path(paths[kind + "_model_dir"])]
        if not matches:
            raise BridgeError(f"未在实际目录检测到所选{'嵌入' if kind == 'embedding' else '重排'}模型，请先检查路径并从已检测列表选择")
        if kind == "embedding" and matches[0]["dimensions"] != group["dimensions"]:
            raise BridgeError("嵌入维度与已检测模型不一致，请重新选择本地模型")
