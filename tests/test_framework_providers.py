from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_llm_retrieval_bridge.framework_providers import list_framework_providers
from test_modes import fake_astrbot_modules


class FrameworkProviderTests(unittest.TestCase):
    def test_same_service_as_provider_rest_api_is_used_without_exposing_keys(self):
        modules, _, Context = fake_astrbot_modules(ROOT)
        context = Context()
        context.provider_manager = types.SimpleNamespace(inst_map=context.providers)
        context.get_config = lambda: {}
        calls = []
        rows = {
            "chat": [{"id": "chat", "model": "ds", "provider_source_id": "existing-source", "api_base": "https://cloud.example/v1", "key": ["never-return-this"]}],
            "embedding": [{"id": "embed", "embedding_model": "bge", "embedding_dimensions": 1536, "embedding_api_base": "http://127.0.0.1:9999/v1", "embedding_api_key": "also-secret"}],
            "rerank": [{"id": "rank", "rerank_model": "cross-encoder", "rerank_api_base": "http://10.1.2.3:8000"}, {"id": "disabled", "enable": False}],
        }
        class Service:
            def __init__(self, lifecycle):
                self.lifecycle = lifecycle
            def list_providers(self, *, capability):
                calls.append(capability)
                return {"providers": rows[capability]}
        for name in ["astrbot.dashboard", "astrbot.dashboard.services", "astrbot.dashboard.services.config_service"]:
            modules[name] = types.ModuleType(name)
        modules["astrbot.dashboard.services.config_service"].ProviderConfigService = Service
        with patch.dict(sys.modules, modules):
            result, source = list_framework_providers(context, 32150)
        self.assertEqual(calls, ["chat", "embedding", "rerank"])
        self.assertIn("GET /api/v1/providers", source)
        self.assertEqual(result["embedding"][0]["id"], "embed")
        self.assertEqual(result["embedding"][0]["location"], "local")
        self.assertEqual(result["embedding"][0]["dimensions"], 1536)
        self.assertEqual(result["rerank"][0]["location"], "lan")
        self.assertEqual(result["chat"][0]["source_id"], "existing-source")
        serialized = json.dumps(result)
        self.assertNotIn("never-return-this", serialized)
        self.assertNotIn("also-secret", serialized)
        self.assertEqual(len(result["rerank"]), 1)

    def test_self_bridge_is_disabled_but_other_local_api_remains_selectable(self):
        modules, _, Context = fake_astrbot_modules(ROOT)
        context = Context()
        context.providers["embed"].provider_config = {"embedding_api_base": "http://127.0.0.1:32150/v1"}
        context.providers["rank"].provider_config = {"rerank_api_base": "http://127.0.0.1:32151"}
        with patch.dict(sys.modules, modules):
            result, _ = list_framework_providers(context, 32150)
        self.assertTrue(result["embedding"][0]["disabled"])
        self.assertFalse(result["rerank"][0]["disabled"])

    def test_missing_runtime_instance_is_visible_but_not_selectable(self):
        modules, _, Context = fake_astrbot_modules(ROOT)
        context = Context()
        context.provider_manager = types.SimpleNamespace(inst_map=context.providers)
        class Service:
            def __init__(self, lifecycle):
                pass
            def list_providers(self, *, capability):
                return {"providers": [{"id": "not-loaded", "provider_type": "embedding"}] if capability == "embedding" else []}
        for name in ["astrbot.dashboard", "astrbot.dashboard.services", "astrbot.dashboard.services.config_service"]:
            modules[name] = types.ModuleType(name)
        modules["astrbot.dashboard.services.config_service"].ProviderConfigService = Service
        with patch.dict(sys.modules, modules):
            result, _ = list_framework_providers(context, 32150)
        self.assertTrue(result["embedding"][0]["disabled"])
