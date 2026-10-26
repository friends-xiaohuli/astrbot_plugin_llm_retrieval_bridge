"""User-facing method trade-offs shared by the integrated view and API."""
from .settings import (
    LOCAL_EMBEDDING, REMOTE_EMBEDDING, LLM_LOCAL_EMBEDDING, LLM_REMOTE_EMBEDDING,
    LLM_RERANK, LOCAL_RERANK, REMOTE_RERANK,
)

METHOD_GUIDES = {
    "embedding": {
        LOCAL_EMBEDDING: {"execution": "local", "location": "本地CPU · 无外网模型请求",
            "pros": ["离线生成真实向量，没有云端调用费", "文本留在本机，延迟较稳定"],
            "cons": ["占用本机内存和CPU，需要完整模型文件及运行库", "能力取决于所选嵌入模型，长文本需切片"]},
        REMOTE_EMBEDDING: {"execution": "embedding_provider", "location": "由所选框架提供商接口决定",
            "pros": ["直接复用模型供应商页的嵌入模型和密钥", "可选本机服务、局域网服务或云端API"],
            "cons": ["必须是支持嵌入的提供商，普通聊天模型不能代替", "网络、费用、数据出站与维度取决于原提供商"]},
        LLM_LOCAL_EMBEDDING: {"execution": "llm_local", "location": "本地编码＋所选LLM接口",
            "pros": ["LLM可先整理口语和分散信息，再使用真实本地向量", "向量模型仍由本机运行"],
            "cons": ["多一次LLM调用，增加耗时且可能改写原意", "LLM可能在网络端；更改整理策略后需重建索引"]},
        LLM_REMOTE_EMBEDDING: {"execution": "llm_embedding_provider", "location": "LLM与嵌入提供商分别决定接口位置",
            "pros": ["可复用现有聊天模型和嵌入服务", "本机无需加载嵌入权重"],
            "cons": ["可能有两段网络请求和两份调用费用", "仍需要真正的嵌入模型，整理结果不保证完全一致"]},
    },
    "rerank": {
        LLM_RERANK: {"execution": "chat_provider", "location": "由所选框架聊天提供商接口决定",
            "pros": ["可直接复用DS4.1F等聊天模型", "能结合上下文比较候选，不需本地重排权重"],
            "cons": ["额外模型请求通常更慢，云端模型可能收费", "JSON格式和评分一致性需校验，适合较少候选"]},
        LOCAL_RERANK: {"execution": "local", "location": "本地CPU · 无外网模型请求",
            "pros": ["离线评分，没有云端调用费", "小模型通常延迟较低，结果容易复现"],
            "cons": ["需要本地权重和运行库，占用CPU与内存", "当前MiniLM主要面向英语，中文效果需测试"]},
        REMOTE_RERANK: {"execution": "rerank_provider", "location": "由所选框架重排提供商接口决定",
            "pros": ["复用模型供应商页的专用重排模型", "通常能批量评分，不需要LLM提示词适配"],
            "cons": ["必须配置真正的重排提供商", "延迟、费用与数据出站取决于原提供商地址"]},
    },
}
