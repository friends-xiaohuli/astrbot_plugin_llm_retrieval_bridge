from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_llm_retrieval_bridge.bridge import BridgeError
from astrbot_plugin_llm_retrieval_bridge.local_models import discover_local_models, validate_local_selection
from astrbot_plugin_llm_retrieval_bridge.settings import default_config, model_paths

DESCRIPTIONS = {
    "embedding": [{"model": "BAAI/bge-small-zh-v1.5", "dim": 512, "model_file": "model_optimized.onnx", "additional_files": []}],
    "rerank": [{"model": "Xenova/ms-marco-MiniLM-L-6-v2", "model_file": "onnx/model.onnx", "additional_files": []}],
}


def model_files(directory, kind, *, missing=None):
    directory.mkdir(parents=True, exist_ok=True)
    metadata = {"_name_or_path": "BAAI/bge-small-zh-v1.5" if kind == "embedding" else "cross-encoder/ms-marco-MiniLM-L-6-v2",
                "architectures": ["BertModel" if kind == "embedding" else "BertForSequenceClassification"], "hidden_size": 512 if kind == "embedding" else 384}
    (directory / "config.json").write_text(json.dumps(metadata), encoding="utf-8")
    for name in ["tokenizer.json", DESCRIPTIONS[kind][0]["model_file"]]:
        if name == missing:
            continue
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("test", encoding="utf-8")


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry = patch("astrbot_plugin_llm_retrieval_bridge.local_models.supported_descriptions", return_value=DESCRIPTIONS)
        self.registry.start()
        self.config = default_config()

    def tearDown(self):
        self.registry.stop()
        self.temp.cleanup()

    def test_missing_directory_has_no_choices_and_is_not_created(self):
        result = discover_local_models(self.config, self.root)
        self.assertEqual(result["embedding"], [])
        self.assertEqual(result["rerank"], [])
        self.assertFalse((self.root / "fastembed").exists())

    def test_only_complete_models_are_selectable(self):
        model_files(self.root / "fastembed/fast-bge-small-zh-v1.5", "embedding")
        model_files(self.root / "fastembed/MiniLM-L-6-v2", "rerank", missing="tokenizer.json")
        result = discover_local_models(self.config, self.root)
        self.assertEqual(len(result["embedding"]), 1)
        self.assertEqual(result["embedding"][0]["dimensions"], 512)
        self.assertEqual(result["rerank"], [])

    def test_real_model_identity_not_folder_label_controls_type(self):
        model_files(self.root / "fastembed/custom-name", "rerank")
        result = discover_local_models(self.config, self.root)
        self.assertEqual(result["rerank"][0]["model"], "Xenova/ms-marco-MiniLM-L-6-v2")
        self.assertEqual(result["embedding"], [])

    def test_huggingface_snapshots_and_plugin_directory_are_discovered(self):
        plugin = self.root / "installed-plugin"
        snapshot = plugin / "fastembed/MiniLM-L-6-v2/snapshots/abc123"
        model_files(snapshot, "rerank")
        result = discover_local_models(self.config, self.root, plugin)
        self.assertEqual(result["rerank"][0]["directory"], str(snapshot.resolve()))

    def test_save_rejects_model_removed_since_page_load(self):
        directory = self.root / "fastembed/fast-bge-small-zh-v1.5"
        model_files(directory, "embedding")
        catalog = discover_local_models(self.config, self.root)
        validate_local_selection(self.config, catalog, model_paths(self.config, self.root))
        (directory / "model_optimized.onnx").unlink()
        with self.assertRaises(BridgeError):
            validate_local_selection(self.config, discover_local_models(self.config, self.root), model_paths(self.config, self.root))

    def test_save_rejects_wrong_dimensions(self):
        model_files(self.root / "fastembed/fast-bge-small-zh-v1.5", "embedding")
        self.config["embedding_backend"]["dimensions"] = 384
        with self.assertRaises(BridgeError):
            validate_local_selection(self.config, discover_local_models(self.config, self.root), model_paths(self.config, self.root))
