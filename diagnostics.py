"""Present each root cause once, with separate concise model readiness states."""
from __future__ import annotations

import hashlib
import re
from typing import Any

LABELS = {"embedding": "嵌入", "rerank": "重排"}


def _runtime_issue(capability: dict) -> dict:
    error = capability.get("error") or {}
    detail = str(error.get("message") or capability.get("message") or "运行库接口无法导入")
    # Older snapshots include a per-capability prefix around an identical underlying exception.
    normalized = re.sub(r"FastEmbed的(?:嵌入|重排)接口", "FastEmbed接口", detail)
    code = capability.get("code", "local_runtime_import_failed")
    dll = "onnxruntime" in detail.lower() and any(token in detail.lower() for token in ("dll", "pybind11_state", "动态链接库"))
    if dll:
        identity = "onnxruntime-dll"
        title = "ONNX Runtime 本机组件加载失败"
        summary = "已安装 Python 包，但 Windows 无法加载所需 DLL；使用本地模型的功能暂不可用。"
        suggestions = ["检查运行 AstrBot 的 Python 位数，以及与其匹配的 Microsoft Visual C++ 运行库。",
                       "检查 ONNX Runtime 的依赖版本和 DLL；仅重新运行 pip 不保证补齐 Windows 系统组件。",
                       "修复后重启 AstrBot，再分别测试嵌入和重排。"]
        links = [{"label": "ONNX Runtime 安装要求", "url": "https://onnxruntime.ai/docs/install/"},
                 {"label": "Microsoft Visual C++ 运行库", "url": "https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist"}]
    elif code == "local_runtime_missing":
        identity = "fastembed-missing"
        title = "缺少 FastEmbed 依赖"
        summary = "当前运行 AstrBot 的 Python 环境尚未安装 FastEmbed。"
        suggestions = ["点击“安装/修复 Python 依赖”，由框架安装本插件 requirements.txt。",
                       "安装完成后重新检测，再执行模型测试。"]
        links = []
    else:
        identity = "import-" + hashlib.sha256(normalized.encode()).hexdigest()[:12]
        title = "Python 依赖导入失败"
        module = error.get("module")
        summary = f"无法导入依赖 {module}，需要检查当前 Python 环境。" if module else "已发现依赖文件，但接口尚不能正常导入。"
        suggestions = ["展开详情查看缺失模块或版本错误。",
                       "使用框架安装器修复 Python 依赖，完成后重启 AstrBot 再测试。"]
        links = []
    return {"id": identity, "title": title, "summary": summary, "details": detail,
            "kinds": [], "suggestions": suggestions, "links": links}


def build_diagnostics(runtime: dict, model_status: dict, paths: dict | None = None) -> dict[str, Any]:
    issues: dict[str, dict] = {}
    for kind, capability in runtime.get("capabilities", {}).items():
        if capability.get("available", False):
            continue
        issue = _runtime_issue(capability)
        existing = issues.setdefault(issue["id"], issue)
        if kind not in existing["kinds"]:
            existing["kinds"].append(kind)

    models = {}
    for kind, status in model_status.items():
        label = LABELS.get(kind, kind)
        state = status.get("state", "untested")
        code = status.get("code", "")
        runtime_failure = state == "runtime_error" or code.startswith("local_runtime_")
        if runtime_failure:
            state, badge, message = "runtime_error", "运行库异常", "查看 Debug 页的运行库诊断。"
        elif state == "ready":
            badge, message = "已就绪", "当前配置已完成真实推理。"
        elif state == "files_missing":
            badge, message = "检查文件", "在本地数据页确认模型目录、文件与维度。"
        elif state == "failed":
            badge, message = "测试失败", "展开下方诊断查看失败原因。"
        else:
            badge, message = "待测试", "在测试页执行一次真实请求。"
        models[kind] = {"state": state, "label": badge, "message": message, "mode": status.get("mode", ""),
                        "local": status.get("local", False)}
        if state not in {"failed", "files_missing"} or runtime_failure:
            continue
        if state == "files_missing":
            title, summary = f"{label}模型文件需检查", "尚未检测到与当前配置匹配的完整模型文件。"
            details = str((paths or {}).get(kind + "_model_dir", ""))
            suggestions = ["在本地数据页检查实际目录、ONNX 与分词文件，再刷新模型检测。",
                           "从已检测列表重新选择模型；嵌入维度需要与模型一致。"]
        else:
            action = "加载" if "load_failed" in code else "推理" if "inference_failed" in code else "测试"
            title, summary = f"{label}模型{action}失败", "当前配置未通过真实模型请求。"
            details = status.get("message", "模型请求失败")
            suggestions = ["检查模型实际路径、模型格式、可用内存或框架提供商状态。", "修复后重新测试当前配置。"]
        identity = "model-" + kind
        issues[identity] = {"id": identity, "title": title, "summary": summary, "details": details,
                            "kinds": [kind], "suggestions": suggestions, "links": []}

    blocked = any(model["state"] in {"runtime_error", "failed", "files_missing"} for model in models.values())
    all_ready = bool(models) and all(model["state"] == "ready" for model in models.values())
    if blocked:
        state, title, summary = "error", "当前配置需要处理", "先处理下面的诊断，再运行相应模型的测试。"
    elif all_ready:
        state, title, summary = "ready", "当前配置已就绪", "嵌入与重排均已完成真实推理。"
    elif issues:
        state, title, summary = "warning", "本地运行库需要处理", "框架提供商可单独测试；本地模型请先修复运行库。"
    else:
        state, title, summary = "pending", "等待模型测试", "配置已读取；分别测试嵌入与重排以确认可用。"
    return {"state": state, "title": title, "summary": summary, "issues": list(issues.values()),
            "models": models, "packages": runtime.get("packages", []), "environment": runtime.get("environment", {})}
