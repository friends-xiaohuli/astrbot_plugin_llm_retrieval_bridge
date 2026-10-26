"""Report local runtime packages and repair them through AstrBot's installer."""
from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import importlib.util
import logging
from pathlib import Path
from typing import Any, Callable

from .bridge import BridgeError


def load_fastembed_class(kind: str):
    """Distinguish an absent package from a broken import or incompatible API."""
    label = "嵌入" if kind == "embedding" else "重排"
    module, symbol = (("fastembed", "TextEmbedding") if kind == "embedding"
                      else ("fastembed.rerank.cross_encoder", "TextCrossEncoder"))
    try:
        model_class = getattr(importlib.import_module(module), symbol)
        if not callable(model_class):
            raise ImportError(f"{symbol}不是可用的模型接口")
        return model_class
    except Exception as exc:
        if isinstance(exc, ModuleNotFoundError) and exc.name == "fastembed":
            raise BridgeError("缺少FastEmbed运行库，请在「重排和嵌入」视图点击「安装/修复运行库」",
                              status=503, code="local_runtime_missing") from exc
        reason = f"{type(exc).__name__}: {str(exc).strip()}"[:400]
        raise BridgeError(f"FastEmbed的{label}接口导入失败（运行库依赖问题）。原因：{reason}。"
                          "请检查依赖版本或DLL，安装/修复运行库后重启AstrBot。",
                          status=503, code="local_runtime_import_failed") from exc


def local_model_failure(kind: str, phase: str, directory: Path, exc: Exception) -> BridgeError:
    label = "嵌入" if kind == "embedding" else "重排"
    action = "加载" if phase == "load" else "推理"
    logging.getLogger(__name__).warning("本地%s模型%s失败，目录=%s", label, action, directory, exc_info=True)
    # Loading errors concern local files/runtime; inference errors can contain input text.
    reason = f"{type(exc).__name__}: {str(exc).strip()}"[:400] if phase == "load" else type(exc).__name__
    return BridgeError(f"本地{label}模型未准备就绪：{action}失败（{reason}）。目录：{directory}。"
                       "请检查模型文件兼容性、可用内存和插件日志。",
                       status=503 if phase == "load" else 502, code=f"{kind}_model_{phase}_failed")


class RuntimeDependencies:
    def __init__(self, plugin_dir: Path | None = None, *, installer: Callable | None = None):
        self.plugin_dir = plugin_dir or Path(__file__).resolve().parent
        self.installer = installer
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        packages = []
        for module, distribution in [("fastembed", "fastembed"), ("onnxruntime", "onnxruntime"), ("numpy", "numpy")]:
            try:
                present = importlib.util.find_spec(module) is not None
            except (ImportError, ValueError):
                present = False
            try:
                version = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                version = ""
            packages.append({"name": distribution, "present": present, "version": version})
        detected = all(row["present"] for row in packages)
        capabilities = {}
        for kind in ("embedding", "rerank"):
            try:
                load_fastembed_class(kind)
                capabilities[kind] = {"available": True, "code": "", "message": "接口可导入，尚未验证模型"}
            except BridgeError as exc:
                capabilities[kind] = {"available": False, "code": exc.code, "message": str(exc)}
        available = detected and all(row["available"] for row in capabilities.values())
        message = ("运行库可导入；嵌入和重排模型的就绪状态分别显示在下方" if available
                   else "已检测到运行库文件，但接口导入失败，请查看下方详情" if detected
                   else "本地运行库缺失或不可导入，请查看下方详情")
        return {"available": available, "detected": detected, "capabilities": capabilities,
                "installing": self._lock.locked(), "packages": packages,
                "requirements": str(self.plugin_dir / "requirements.txt"),
                "message": message,
                "automatic_install": "通过AstrBot安装/更新插件时，框架自动检查并安装requirements.txt；手动覆盖代码仅重载不保证补装懒加载依赖。"}

    async def install(self) -> dict[str, Any]:
        if self._lock.locked():
            raise BridgeError("运行库正在安装，请等待完成", status=409, code="dependency_install_busy")
        async with self._lock:
            requirements = self.plugin_dir / "requirements.txt"
            if not requirements.is_file():
                raise BridgeError("插件requirements.txt不存在", status=503, code="requirements_missing")
            installer = self.installer
            if installer is None:
                try:
                    from astrbot.core import pip_installer
                except ImportError as exc:
                    raise BridgeError("当前框架未提供依赖安装器", status=503, code="installer_unavailable") from exc
                installer = pip_installer.install
            try:
                # Reuse the framework's environment, mirror and dependency conflict protections.
                await installer(requirements_path=str(requirements))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise BridgeError("框架安装依赖失败，请查看AstrBot安装日志中的网络、权限或版本冲突信息", status=502, code="dependency_install_failed") from exc
            importlib.invalidate_caches()
        from .local_models import clear_model_descriptions_cache
        clear_model_descriptions_cache()
        result = await asyncio.to_thread(self.status)
        result["message"] = ("框架依赖安装完成；运行库可导入，模型仍需分别通过推理测试" if result["available"]
                             else "安装器已完成，但运行库仍不可用，请查看导入详情；必要时重启AstrBot")
        return result
