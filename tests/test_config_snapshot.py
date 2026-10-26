from __future__ import annotations

import copy
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_llm_retrieval_bridge.console import merge_settings
from astrbot_plugin_llm_retrieval_bridge.settings import configuration_data, default_config


class LockedFrameworkConfig(dict):
    def __init__(self, values):
        super().__init__(values)
        # Match the two runtime attributes of AstrBot4.29.3 AstrBotConfig.
        self._save_state_lock = threading.Lock()
        self._save_commit_lock = threading.Lock()


class ConfigSnapshotTests(unittest.TestCase):
    def test_whole_wrapper_deepcopy_reproduces_installation_error(self):
        with self.assertRaisesRegex(TypeError, "cannot pickle.*lock"):
            copy.deepcopy(LockedFrameworkConfig(default_config()))

    def test_data_snapshot_is_plain_and_deeply_independent(self):
        config = LockedFrameworkConfig(default_config())
        snapshot = configuration_data(config)
        self.assertIs(type(snapshot), dict)
        self.assertFalse(hasattr(snapshot, "_save_state_lock"))
        snapshot["embedding_backend"]["dimensions"] = 384
        self.assertEqual(config["embedding_backend"]["dimensions"], 512)

    def test_preview_and_save_merge_only_data_not_wrapper_state(self):
        config = LockedFrameworkConfig(default_config())
        merged = merge_settings(config, {"max_rerank_documents": 8})
        self.assertIs(type(merged), dict)
        self.assertEqual(merged["max_rerank_documents"], 8)
        self.assertEqual(config["max_rerank_documents"], 16)


if __name__ == "__main__":
    unittest.main()
