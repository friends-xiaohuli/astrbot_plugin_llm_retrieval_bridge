# 重排和嵌入 · 0.5.1

为 AstrBot 提供 OpenAI Embedding 与 vLLM Rerank 兼容接口，支持本地模型和框架已有提供商。

进入 **插件详情 → 视图 → 重排和嵌入**，通过菜单配置后端、测试模型、查看 Debug、本地数据和对接参数。

## 本次更新

- 本地嵌入、重排模型下拉框只显示相对目录，例如 `fastembed\fast-bge-small-zh-v1.5` 和 `fastembed\MiniLM-L-6-v2`。
- 去掉选项中重复的模型 ID 和冗长的服务器路径前缀，避免展开后横向撑出视图。
- 完整实际路径仍可在“本地数据”页检查；本次调整不改变模型选择、维度或推理配置。
- 新增 [CHANGELOG.md](CHANGELOG.md)，供 AstrBot 插件详情中的“更新日志”读取。
