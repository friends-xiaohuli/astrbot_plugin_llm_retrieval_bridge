"""Report local runtime packages and repair them through AstrBot's installer."""
from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import importlib.util
from pathlib import Path
from typing import Any, Callable

from .bridge import BridgeError


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
        available = all(row["present"] for row in packages)
        return {"available": available, "installing": self._lock.locked(), "packages": packages,
                "requirements": str(self.plugin_dir / "requirements.txt"),
                "message": "已检测到运行库，模型可用性请执行推理测试" if available else "缺少本地运行库，可使用下方按钮让框架安装requirements.txt",
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
        result = self.status()
        result["message"] = "框架依赖安装完成，请刷新模型检测并执行测试" if result["available"] else "安装器已完成，但仍有模块未被当前进程发现，请重载插件后检查日志"
        return result
