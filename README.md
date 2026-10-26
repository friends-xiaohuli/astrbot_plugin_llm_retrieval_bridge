# 重排和嵌入 0.4.1

目标：AstrBot4.29.3、Python3.12。本插件提供OpenAI Embedding/vLLM Rerank兼容接口。
推荐从插件自带的可视化视图完成所有配置与测试。

## 0.4.1 更新源与模型就绪状态修复

运行库状态现在实际导入FastEmbed嵌入与重排接口，区分包缺失、依赖或DLL导入失败。
模型目录文件齐全只代表通过文件检查；页面分别展示嵌入与重排的就绪状态，推理测试通过后才显示已就绪。
缺少权重、模型不兼容、加载失败与推理失败会显示各自原因及实际目录，不再统一提示缺少FastEmbed。
测试未保存配置时只更新该配置的测试结果，修改模型、路径或上游提供商后需要重新测试。

元数据已补齐repo，用于AstrBot识别插件更新源；本地Git的origin不会替代这个字段。
如果已安装的旧版提示“当前插件缺少仓库地址”，先在服务器插件目录的metadata.yaml中添加：

~~~yaml
repo: https://github.com/friends-xiaohuli/astrbot_plugin_llm_retrieval_bridge
~~~

保存并重载插件后，框架会重新读取仓库地址，再使用更新或更换插件源功能。
这一步只修改元数据，保留原有fastembed模型目录与插件数据。

## 0.4.0 模型选择与运行库

视图入口现为 **插件详情 → 视图 → 重排和嵌入**。
每种重排/嵌入方式直接展示优点、代价与接口位置，还可展开比较全部方式。

### 框架提供商就是原有模型

“框架嵌入提供商”“框架重排提供商”是你在AstrBot模型供应商页已经配置的真正嵌入/重排模型。
选择它们是直接调用原提供商，不是把聊天LLM转换成该能力，也不重新创建或复制供应商密钥。

框架确实提供以下管理API：

- GET /api/v1/providers?capability=chat
- GET /api/v1/providers?capability=embedding
- GET /api/v1/providers?capability=rerank
- GET /api/v1/provider-sources/{source_id}/providers

这些接口使用provider作用域认证。文档中“条件性敏感子权限”为“—”不代表免认证。
本插件后端直接复用这些API和框架原生ProviderSelectMenu使用的
ProviderConfigService.list_providers()服务，包含模型供应商继承配置。
仅向视图返回ID、模型名、供应商ID和接口位置；API Key与自定义请求头不会传给视图。

页面按能力分成聊天、嵌入、重排三个选择列表，保存原来的provider ID。
禁用的提供商不列入；尚未加载成功、能力不匹配或指向桥接自身的模型不能选择。
框架嵌入模型已配置输出维度时，选中后会自动带入。

4.29.3的插件视图SDK可以调用插件后端API，但没有直接打开父页面原生Vue选择器的桥接动作。
所以本页使用同一框架服务返回的数据实现类型下拉框，而不是让iframe访问父页面DOM或登录凭据。
供应商本机HTTP、局域网、网络API分别标记；这是接口地址位置，代理后面的推理位置仍由原配置决定。

### 本地模型先检测再选择

本地模型名不再手填：先从下方统一目录、覆盖目录与兼容目录检查已存在的模型。
只有config.json、分词器、所选FastEmbed模型所需ONNX及外部数据文件齐全，才会出现在下拉框。
可用模型为空时选择器禁用；修改路径或复制模型后点击“刷新模型检测”。
选择模型会同步实际目录和嵌入维度。保存时再次检查，避免页面加载后模型文件被移动而继续使用。
目录检查不代表权重内容一定正确，仍应使用页面的真实推理测试。

### requirements会自动安装吗

**通过AstrBot安装或更新插件时会。** v4.29.3在加载前调用
_ensure_plugin_requirements()，检查requirements.txt：满足则跳过，缺失或版本不符则通过框架安装器处理。
安装使用AstrBot自身环境、配置的软件源和核心依赖冲突保护，不是无关的系统Python。

**手动覆盖文件后仅重载，不保证补齐懒加载依赖。** 本插件在真实推理时才导入FastEmbed，
这个阶段报缺库不一定触发框架的“导入插件失败后恢复依赖”逻辑。
因此页面增加运行库状态及“安装/修复运行库”按钮，复用框架pip_installer.install(requirements_path=...)。
它只处理本插件固定的requirements.txt，同一时间只运行一次，失败会显示框架安装日志的排查方向。
不会在每次推理时重复运行pip，也不会自动下载模型权重。

实际安装仍需要软件源可达、运行目录可写，并满足Python与本机动态库要求；按钮不保证网络/版本冲突必然修复。
运行库状态表示嵌入与重排接口能否成功导入；模型是否能加载并完成推理，以各自的测试结果为准。

## 0.3.1 安装失败修复

0.3.0会对整个AstrBotConfig对象做deepcopy；AstrBot4.29.3原版配置对象内部有线程锁，
因此加载时会触发 cannot pickle '_thread.lock' object。
0.3.1统一先提取dict配置项，再复制数据；启动、预览、保存与测试路径都已修复。
已使用官方原版AstrBotConfig类复现旧错误，并验证修复后初始化、HTTP视图API、
异步配置落盘及重新加载成功。完整上下文/提供商仍为本地测试替身。

若模型放在插件安装目录的fastembed中，建议解压本包覆盖插件代码后点击重载，保留fastembed目录。
AstrBot的ZIP上传更新会替换整个插件安装目录，可能移除安装目录内未随ZIP提供的模型文件。
插件数据目录中的模型与配置不需要因为本次线程锁错误而重新安装。

## 打开整合页面

安装/更新并重新加载插件后，在AstrBot中进入：

**插件详情 → 视图 → 重排和嵌入**

唯一的视图位于 views/settings/index.html。
它使用AstrBot官方AstrBotPluginView桥接与公开的context.register_web_api接口，不需要另开Web管理端口，
也不把Dashboard登录信息或API密钥放进URL。

这一页整合：

1. 两个推荐方案。
2. 嵌入/重排独立后端选择，只显示所选方式需要的字段。
3. 一个“本地数据与模型”区，集中设置fastembed根目录、可选覆盖路径并查看实际路径。
4. 嵌入/重排真实测试，显示维度、相似度、排序、耗时或错误。
5. 两组只读对接参数，可逐项复制或复制JSON。

原生配置表单仅作为基础设置/信息兜底。复杂后端选项已集中到可视化视图。
两组对接信息在兜底表单中也为只读；密钥统一在视图内显示/复制。
普通字符串的readonly在AstrBot4.29.3默认控件中不起作用，因此兜底对接项采用禁用选择控件。

## 你的现有路径与默认fastembed根目录

统一默认根目录为：

~~~text
<core>/data/plugin_data/astrbot_plugin_llm_retrieval_bridge/fastembed/
  fast-bge-small-zh-v1.5/
  MiniLM-L-6-v2/
~~~

也兼容你当前的两处位置：

~~~text
<插件安装目录>/fastembed/MiniLM-L-6-v2/
<core>/data/plugin_data/astrbot_plugin_llm_retrieval_bridge/fastembed/fast-bge-small-zh-v1.5/
~~~

自动识别顺序：

- 嵌入：统一根目录中的BGE → 插件安装目录fastembed中的BGE → 旧版插件数据目录中的BGE。
- 重排：统一根目录中的MiniLM-L-6-v2 → 插件安装目录fastembed中的同名目录 → 旧ms-marco目录。
- 目录中保留Hugging Face的refs/main、snapshots/<revision>时，也尝试定位唯一可用的snapshot。
- 优先选择默认ONNX及tokenizer文件存在的候选，避免空目录遮挡现有有效模型。
- 手动填写覆盖路径时严格从该目录读取，不会悄悄换成其他位置。

视图把根目录、插件数据目录、嵌入与重排实际路径、识别来源和默认文件状态合在一个区域，
不再让你分别维护两套“配置路径”和“实际路径”表单。
实际路径为只读；根目录和覆盖路径可编辑。相对路径按插件数据目录解析，支持绝对路径。

兼容读取不会自动搬动、覆盖或删除你的模型。
路径状态只表示目录/默认文件是否存在，不代表模型已成功推理；用页面测试按钮验证加载。
其他FastEmbed已支持模型也可选择，但必须有对应ONNX结构与正确维度；默认文件状态以缓存中的BGE/MiniLM为准。

## 后端切换与推荐方案

| 能力 | 可选择的方式 |
|---|---|
| 重排 | LLM评分、本地FastEmbed交叉编码器、框架重排提供商 |
| 嵌入 | 本地FastEmbed、框架嵌入提供商、LLM增强＋本地嵌入、LLM增强＋框架嵌入 |

本地模式展示模型名、CPU线程和批量；框架模式展示相应提供商；
LLM模式展示聊天提供商、候选/超时或文本整理选项。
不需要的字段不会出现在视图中；修改后端不会清空你之前填写的另一种配置。

推荐方案：

- **中文记忆：本地BGE嵌入＋DS4.1F重排。** 2核2GB先用1线程、8–16条候选。
- **全本地：BGE嵌入＋MiniLM重排。** 降低云端调用，但缓存中的MiniLM主要面向英语，中文质量应实测。

点击推荐只更新表单，不自动保存，也不调用模型。

普通聊天LLM不能仅靠提示词变成可靠的嵌入编码器。
“LLM增强嵌入”先整理文本，再交给真正的嵌入模型生成向量，会增加成本、延迟和改写不确定性。
若某个基于LLM的模型本身支持/embeddings，可直接选择它对应的框架嵌入提供商。

## 保存与测试

**保存并应用**会保存配置并切换后端，无需重新加载整个AstrBot。
正在处理的请求继续使用旧后端，完成后才释放旧模型。
端口变更单独标记为“待重启”，对接区持续展示当前可用地址，避免复制到尚未监听的新地址。
API Key修改会直接生效，需要同步修改框架中使用它的提供商。

测试按当前表单运行，不要求先保存：

- 与已应用配置相同：复用当前后端。
- 修改但未保存：临时创建后端测试，结束后释放，不保存配置。
- 嵌入：1–8条文本，显示真实维度、向量范数、余弦矩阵、部分数值与耗时。
- 重排：问题＋1–16条候选，显示排序、分数、原文与耗时。
- 错误在视图中显示，不伪造成功结果。
- LLM/框架远端模式会调用对应提供商，可能产生费用；页面明确提示。
- 测试本身不改记忆库。

切换嵌入模型、维度或LLM整理策略后，按记忆插件维护功能备份并重建向量索引；
本插件不会擅自清空/迁移数据库。

## 只读对接区

两组对接信息由实际运行配置生成，不可直接编辑；服务端也拒绝通过视图写入这些派生字段。
要修改端口、密钥或对外模型名，使用对应配置区域，保存后对接区自动更新。
API Key默认遮挡，可显示或复制。主要字段直接展示，其余字段在“其他对接参数”中。

默认OpenAI Embedding：

| 字段 | 值 |
|---|---|
| 类型/建议ID | OpenAI Embedding / bridge-embedding |
| API Base | http://127.0.0.1:32150/v1 |
| 完整地址 | http://127.0.0.1:32150/v1/embeddings |
| API Key | 视图中复制 |
| 模型名/维度 | BAAI/bge-small-zh-v1.5 / 512 |
| dimensions发送模式 | never |
| 超时 | 60秒 |
| 代理/额外请求头 | 留空 |
| 编码 | float / base64 |

默认vLLM Rerank：

| 字段 | 值 |
|---|---|
| 类型/建议ID | vLLM Rerank / bridge-rerank |
| API Base | http://127.0.0.1:32150 |
| 后缀 | /v1/rerank |
| API Key | 同一个桥接密钥 |
| 模型别名 | llm-rerank；可与后端保持独立 |
| 超时 | 默认50秒，桥接超时40秒加10秒余量 |
| top_n | 由调用方传入 |

vLLM是框架协议适配器名称，不需要安装vLLM软件。
框架与记忆插件的外层超时都应大于桥接超时。
“框架提供商”不能选择桥接自身，避免递归；本插件检测常见本机自递归地址。

## 安装与缓存导入

安装到 data/plugins/astrbot_plugin_llm_retrieval_bridge，依赖为requirements.txt。
FastEmbed使用CPU ONNX Runtime，不依赖PyTorch或CUDA；ZIP缓存不是运行库。

以下是替换路径后执行的命令模板，不会自动运行：

~~~powershell
& '<实例实际使用的python.exe>' -m pip install -r '<插件目录>\requirements.txt'
& '<实例实际使用的python.exe>' '<插件目录>\tools\import_fastembed_cache.py' 'C:\Models\fastembed_cache.zip'
~~~

默认只导入已知BGE/MiniLM文件到插件数据目录的fastembed下，不覆盖已有文件。
只导入一种或另选数据目录：

~~~powershell
& '<实例实际使用的python.exe>' '<插件目录>\tools\import_fastembed_cache.py' 'C:\Models\fastembed_cache.zip' --kind rerank --data-dir 'C:\Models\retrieval-bridge'
~~~

指定其他数据目录后，在统一视图设置根目录为该位置下的fastembed，或填写模型覆盖目录。

模型首次请求才加载，使用specific_model_path、local_files_only=True和CPUExecutionProvider；
不会自动下载模型。2核2GB需实际评估内存，别直接换成大型重排模型。

## 框架公开调用

先在框架创建以上两个提供商，然后在记忆插件中选其ID。
插件内部复用context.llm_generate()，不包装其他插件事件处理函数。

~~~python
from astrbot.core.provider.provider import EmbeddingProvider, RerankProvider

embedder = self.context.get_provider_by_id("bridge-embedding")
reranker = self.context.get_provider_by_id("bridge-rerank")
if not isinstance(embedder, EmbeddingProvider) or not isinstance(reranker, RerankProvider):
    raise RuntimeError("请先创建并启用提供商")
vectors = await embedder.get_embeddings(["喜欢喝茶", "喜欢跑步"])
ranked = await reranker.rerank("喜欢什么饮料", ["喜欢喝茶", "喜欢跑步"], top_n=1)
~~~

除Dashboard视图API使用官方登录桥接外，本机模型接口只监听127.0.0.1，要求Bearer密钥：

- POST /v1/embeddings
- POST /v1/rerank 或 /rerank
- GET /v1/models
- GET /v1/connection-info
- GET /health
- /console/*：与视图共用的本机管理API，同样要求Bearer密钥

/health表示监听状态，不等于模型已测试成功。缺模型/运行库返回503，输入错误400，
上游格式错误502，超时504。超时、非法JSON和数量/维度错误不伪装成成功。

## 验证范围

后端63项检查通过；真实Edge完成23项页面检查，并检查桌面/平板/手机宽度及明暗主题。
页面按钮实际调用本地BGE与MiniLM，模型列表使用官方ProviderConfigService原版方法。
另通过原版AstrBotConfig的加载/保存/重载检查。
本机验证模拟Dashboard SDK传输外壳；未在真实Dashboard登录iframe中执行，也未部署服务器。
完整细节见VALIDATION.md。

本插件未部署用户服务器，未调用付费DS接口。
可在页面上按自己实际配置测试。单独检查本地默认模型也可运行：

~~~powershell
& '<实例实际使用的python.exe>' '<插件目录>\tools\verify_local_models.py' '<插件数据目录>'
~~~

## Sentence Transformers本地嵌入

它指把训练好的句子模型放在本机，由本机CPU/GPU把文本编码成固定长度语义向量，
用于相似度搜索。不是让聊天LLM编一串数字。
本插件使用FastEmbed＋ONNX完成相同类型的编码任务，缓存中的BGE是真正的512维嵌入模型，
MiniLM是独立的交叉编码器重排模型。

官方参考：
- https://docs.astrbot.app/en/dev/star/guides/plugin-pages.html
- https://docs.astrbot.app/dev/openapi-scopes.html#provider
- https://sbert.net/docs/sentence_transformer/usage/usage.html
- https://github.com/qdrant/fastembed
