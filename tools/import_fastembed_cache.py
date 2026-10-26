"""Import embedding/rerank weights from the supplied ZIP; never overwrite files."""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

plugin_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(plugin_root.parent))
bridge = importlib.import_module(plugin_root.name + ".bridge")
cache_import = importlib.import_module(plugin_root.name + ".cache_import")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path, nargs="?", help="兼容0.1：直接导入嵌入模型到该目录")
    parser.add_argument("--kind", choices=["embedding", "rerank", "both"], default="both")
    parser.add_argument("--data-dir", type=Path, help="插件数据目录；不指定时按data/plugins安装位置推导")
    args = parser.parse_args()
    if args.destination:
        if args.data_dir or args.kind != "both":
            parser.error("旧版destination写法不能与--data-dir或--kind混用")
        files = bridge.import_model_cache(args.archive, args.destination)
        print("已导入嵌入模型到：" + str(args.destination.resolve()))
    else:
        data_dir = args.data_dir
        if data_dir is None:
            if plugin_root.parent.name != "plugins" or plugin_root.parent.parent.name != "data":
                parser.error("当前不在AstrBot的data/plugins安装目录，请提供--data-dir")
            data_dir = plugin_root.parent.parent / "plugin_data" / plugin_root.name
        files = cache_import.import_cache_pair(args.archive, data_dir, kind=args.kind)
        print("已导入模型：" + ", ".join(files))
        print("插件数据目录：" + str(data_dir.resolve()))
