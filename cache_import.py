"""Import the two known models without extracting arbitrary archive paths."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .bridge import BridgeError, import_model_cache


def import_rerank_cache(archive: Path, destination: Path) -> list[str]:
    repo = "models--Xenova--ms-marco-MiniLM-L-6-v2/"
    required = ("config.json", "tokenizer.json", "tokenizer_config.json",
                "special_tokens_map.json", "onnx/model.onnx")
    optional = ("vocab.txt",)
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise BridgeError("ZIP存在重复条目，请重新生成缓存包")
        if repo + "refs/main" in names:
            revision = z.read(repo + "refs/main").decode("ascii").strip()
            if not revision or any(c not in "0123456789abcdef" for c in revision):
                raise BridgeError("重排缓存revision无效")
        else:
            revisions = {name[len(repo + "snapshots/"):].split("/", 1)[0]
                         for name in names if name.startswith(repo + "snapshots/")}
            if len(revisions) != 1:
                raise BridgeError("无法唯一确定重排模型snapshot，请检查refs/main")
            revision = revisions.pop()
        prefix = repo + "snapshots/" + revision + "/"
        mapping = {name: z.getinfo(prefix + name) for name in (*required, *optional)
                   if prefix + name in names}
        if any(name not in mapping for name in required):
            raise BridgeError("缓存包缺少重排ONNX或分词器文件")
        if any(i.file_size <= 0 or i.file_size > 200_000_000 for i in mapping.values()):
            raise BridgeError("重排缓存文件大小异常")
        if any((destination / name).exists() for name in mapping):
            raise FileExistsError("重排目标包含已有模型文件，导入不会覆盖")
        for name, info in mapping.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
        return list(mapping)


def import_cache_pair(archive: Path, data_dir: Path, *, kind: str = "both") -> dict[str, list[str]]:
    if kind not in {"embedding", "rerank", "both"}:
        raise ValueError("kind必须是embedding、rerank或both")
    imported = {}
    if kind in {"embedding", "both"}:
        imported["embedding"] = import_model_cache(archive, data_dir / "fastembed/fast-bge-small-zh-v1.5")
    if kind in {"rerank", "both"}:
        imported["rerank"] = import_rerank_cache(archive, data_dir / "fastembed/MiniLM-L-6-v2")
    return imported
