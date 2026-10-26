# 0.3.1 验证记录

日期：2026-10-27。目标API：AstrBot4.29.3，Python3.12。

## 已完成

- 50项Python检查通过：评分/向量校验、后端12种组合、HTTP协议、认证、缓存和超时。
- 使用AstrBot4.29.3官方配置类的完整原版类定义（只隔离无关导入），复现0.3.0加载错误：
  deepcopy(AstrBotConfig)与插件start均触发cannot pickle '_thread.lock' object。
- 修复后同一个原版类通过插件初始化、视图状态、预览、测试、保存及原生异步配置落盘，
  从配置文件重新创建原版对象后也可在相同端口再次启动。提供商/上下文是测试替身，没有调用付费LLM。
- 原有两个测试配置替身现都包含线程锁，避免普通dict替身漏掉框架对象状态。
- 已覆盖用户给出的两种模型布局：
  插件安装目录/fastembed/MiniLM-L-6-v2与插件数据目录/fastembed/fast-bge-small-zh-v1.5。
- Hugging Face refs/main与snapshots版本目录解析、显式覆盖、旧路径一次迁移均有测试。
- 只读字段写入被服务端拒绝；预览不保存、不加载模型。
- 保存应用、配置版本冲突、端口待重启、在途请求继续使用旧后端、旧模型延后释放均有检查。
- 未保存配置的测试使用临时后端，不改变当前配置/记忆库。
- 插件官方View放在views/settings，使用AstrBotPluginView与context.register_web_api。
- 19项Happy DOM页面交互通过，运行的是实际app.js和HTML，调用真实本机管理/模型API。
- 页面交互覆盖：各后端切换字段、推荐方案、保存、统一根目录、两组只读参数、复制按钮处理、
  测试按钮、512维真实BGE输出、真实MiniLM排序、模型缺失错误展示。
- 两个本地模型来自用户缓存，模型测试未调用外部LLM。
- JavaScript、Python与JSON语法检查通过。

运行库：FastEmbed0.9.1、ONNX Runtime1.31.0。
页面DOM运行库Happy DOM20用于本地验证，不是插件生产依赖，不打包进插件。
DOM测试只模拟官方SDK传输外壳，真实模型请求由本机插件代码处理。
付费聊天模型用测试替身，没有消耗用户DS接口。

缓存ZIP SHA-256：

~~~text
7f5b42b76df56d443c13313756b699b32a7cd22bc1c98cc048c30d51343ab5a4
~~~

## 视觉/部署限制

- computer-use本机管道不可用，按技能要求重试及重建会话后仍不可用。
- 独立Edge自动启动被spawn EPERM拒绝；直接启动临时headless进程也因Mojo/Crashpad命名管道拒绝访问退出。
- 因此没有完成真实浏览器截图、响应式视觉排版或完整AstrBot Dashboard iframe的端到端验证。
- Happy DOM交互验证不等于浏览器渲染验证，不能据此宣称排版已经视觉验收。
- 未部署到用户服务器，未改真实模型文件或记忆数据库。
- 未验证用户2核2GB机器实际性能/内存，也未评估中文MiniLM排序质量。
- 普通聊天LLM仍不被当成数值向量编码器；LLM增强嵌入始终接真实编码器。

## 官方依据

- AstrBot4.29.3 astrbot/api/web.py、context.register_web_api接口及Plugin View文档。
- AstrBot4.29.3 PluginDetailPage、PluginWorkspacePage、plugin_page_bridge.js。
- AstrBot4.29.3 AstrBotConfig.vue条件显示与ConfigItemRenderer.vue只读控件。
- FastEmbed0.9.1 TextEmbedding/TextCrossEncoder本地加载实现。
