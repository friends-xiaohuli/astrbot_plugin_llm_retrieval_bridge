"""Verify real offline embedding/rerank weights without calling a paid LLM."""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path

plugin_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(plugin_root.parent))
bridge = importlib.import_module(plugin_root.name + ".bridge")
backends = importlib.import_module(plugin_root.name + ".backends")
settings = importlib.import_module(plugin_root.name + ".settings")


async def verify(data_dir: Path) -> None:
    paths = settings.model_paths(settings.default_config(), data_dir)
    embedder = backends.EmbeddingService(
        bridge.FastEmbedBackend(Path(paths["embedding_model_dir"]), data_dir / "runtime-cache"),
        model=bridge.EMBEDDING_MODEL, dimensions=512,
    )
    ranker = backends.RerankService(
        backends.LocalRerankBackend(Path(paths["rerank_model_dir"]), data_dir / "runtime-cache",
                                   model_name="Xenova/ms-marco-MiniLM-L-6-v2"),
        model="local-rerank",
    )
    try:
        vectors = await embedder.embed(["用户喜欢喝茶", "服务器磁盘不足"])
        results = await ranker.rerank({"query": "What beverage does the user like?",
                                     "documents": ["The user likes green tea.", "The disk is full."]})
        print(json.dumps({"dimensions": [len(v) for v in vectors], "rerank": results}, ensure_ascii=False, indent=2))
    finally:
        await asyncio.gather(embedder.close(), ranker.close())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", type=Path)
    asyncio.run(verify(parser.parse_args().data_dir))
