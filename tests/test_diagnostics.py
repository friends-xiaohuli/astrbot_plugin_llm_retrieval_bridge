from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_llm_retrieval_bridge.diagnostics import build_diagnostics


class DiagnosticsTests(unittest.TestCase):
    def dll_runtime(self):
        message = "DLL load failed while importing onnxruntime_pybind11_state: 找不到指定的模块。"
        return {"available": False, "detected": True, "packages": [{"name": "fastembed", "version": "0.9.1", "present": True}],
                "capabilities": {kind: {"available": False, "code": "local_runtime_import_failed", "message": f"FastEmbed的{label}接口导入失败：{message}",
                                        "error": {"type": "ImportError", "message": message}}
                                 for kind, label in (("embedding", "嵌入"), ("rerank", "重排"))}}

    def test_shared_dll_cause_is_shown_once_and_models_do_not_repeat_it(self):
        runtime = self.dll_runtime()
        models = {kind: {"state": "runtime_error", "local": True, "message": capability["message"]}
                  for kind, capability in runtime["capabilities"].items()}
        result = build_diagnostics(runtime, models)
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["issues"][0]["id"], "onnxruntime-dll")
        self.assertEqual(result["issues"][0]["kinds"], ["embedding", "rerank"])
        self.assertIn("pip", " ".join(result["issues"][0]["suggestions"]))
        for model in result["models"].values():
            self.assertEqual(model["label"], "运行库异常")
            self.assertNotIn("pybind11_state", model["message"])

    def test_older_capabilities_are_deduplicated_without_exception_metadata(self):
        runtime = self.dll_runtime()
        for capability in runtime["capabilities"].values():
            capability.pop("error")
        result = build_diagnostics(runtime, {})
        self.assertEqual(len(result["issues"]), 1)

    def test_cloud_rerank_is_not_marked_broken_by_local_embedding_dll(self):
        result = build_diagnostics(self.dll_runtime(), {"embedding": {"state": "runtime_error", "local": True},
                                                      "rerank": {"state": "untested", "local": False}})
        self.assertEqual(result["models"]["embedding"]["state"], "runtime_error")
        self.assertEqual(result["models"]["rerank"]["label"], "待测试")

    def test_distinct_missing_dependencies_remain_separate(self):
        runtime = {"capabilities": {kind: {"available": False, "code": "local_runtime_import_failed",
                                           "error": {"type": "ModuleNotFoundError", "message": f"No module named '{module}'", "module": module}}
                                    for kind, module in (("embedding", "tokenizers"), ("rerank", "numpy"))}}
        result = build_diagnostics(runtime, {})
        self.assertEqual(len(result["issues"]), 2)

    def test_model_load_error_is_distinct_from_runtime_failure(self):
        result = build_diagnostics({"available": True}, {"embedding": {"state": "failed", "code": "embedding_model_load_failed", "message": "InvalidProtobuf"},
                                                       "rerank": {"state": "ready"}})
        self.assertEqual(result["issues"][0]["title"], "嵌入模型加载失败")
        self.assertEqual(result["models"]["rerank"]["label"], "已就绪")

    def test_ready_remote_models_are_not_blocked_by_unused_local_runtime(self):
        result = build_diagnostics(self.dll_runtime(), {"embedding": {"state": "ready", "local": False}, "rerank": {"state": "ready", "local": False}})
        self.assertEqual(result["state"], "ready")
        self.assertEqual(len(result["issues"]), 1)


if __name__ == "__main__":
    unittest.main()
