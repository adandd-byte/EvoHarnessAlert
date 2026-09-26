from __future__ import annotations

import shutil  # 目录复制/删除
from dataclasses import dataclass  # 数据类
from datetime import datetime  # 时间
from pathlib import Path  # 路径

import httpx  # HTTP 客户端

from app.core.config import Settings  # 全局配置
from app.models.entities import KnowledgeChunk  # 知识分块实体


PRIMARY_RETRIEVAL_LABEL = "Chroma vector + BM25 hybrid + local reranker"  # 主检索方案标签
FALLBACK_RETRIEVAL_LABEL = "local BM25 + hybrid_score reranker"  # 降级检索方案标签


class VectorStoreUnavailable(RuntimeError):
    pass  # 向量库不可用异常


@dataclass
class VectorSearchHit:
    chunk_id: int | None  # 知识分块 ID
    source: str           # 来源（文件名）
    source_index: int     # 来源内序号
    content: str          # 命中内容
    score: float          # 相关度得分


class ChromaKnowledgeStore:
    # 主检索路径：使用 OpenAI text-embedding-3-small 在 Chroma 中存储与查询
    def __init__(self, settings: Settings):
        self.settings = settings  # 全局配置
        self.can_embed = False  # 是否可用 embedding
        self.error = ""  # 不可用原因
        if not settings.knowledge_vector_enabled:  # 未启用向量检索
            self.error = "Chroma 向量库未启用"
            return
        if not settings.openai_api_key:  # 缺 API key
            if settings.knowledge_vector_required:  # 必需则抛异常
                raise VectorStoreUnavailable("缺少 OPENAI_API_KEY，无法启用 Chroma + text-embedding-3-small 主检索方案")
            self.error = f"缺少 OPENAI_API_KEY，Chroma + text-embedding-3-small 不可用，已回退到{FALLBACK_RETRIEVAL_LABEL}"
            return
        try:
            import chromadb  # 延迟导入
        except ImportError as exc:  # 缺依赖
            if settings.knowledge_vector_required:  # 必需则抛异常
                raise VectorStoreUnavailable("缺少 chromadb 依赖，无法启用 Chroma + text-embedding-3-small 主检索方案") from exc
            self.error = f"缺少 chromadb 依赖，Chroma + text-embedding-3-small 不可用，已回退到{FALLBACK_RETRIEVAL_LABEL}"
            return

        persist_dir = self._resolve_path(settings.chroma_persist_dir)  # 持久化目录
        persist_dir.mkdir(parents=True, exist_ok=True)  # 确保存在
        self.persist_dir = persist_dir  # 保存
        self.client = chromadb.PersistentClient(path=str(persist_dir))  # 建客户端
        self.collection = self.client.get_or_create_collection(  # 取或建集合
            name=settings.chroma_collection_name,  # 集合名
            embedding_function=None,  # embedding 由外部提供
            metadata={"hnsw:space": "cosine", "embedding_model": settings.openai_embedding_model},  # 余弦相似度
        )
        self.can_embed = settings.knowledge_vector_enabled  # 标记可用

    def upsert_chunks(self, chunks: list[KnowledgeChunk], embeddings: list[list[float]]) -> int:
        # 批量写入/更新分块，返回写入数量
        rows = [chunk for chunk in chunks if chunk.id is not None and chunk.content.strip()]  # 过滤无效行
        if not rows:
            return 0
        ids = [self._id(chunk.id) for chunk in rows]  # Chroma ID
        documents = [chunk.content for chunk in rows]  # 文档内容
        metadatas = [  # 元数据
            {"db_id": int(chunk.id), "source": chunk.source, "source_index": int(chunk.source_index)}  # 数据库归属
            for chunk in rows
        ]
        self.collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)  # 写入
        self.snapshot()  # 打快照
        return len(rows)

    def sync_chunks(self, chunks: list[KnowledgeChunk], embeddings: list[list[float]]) -> int:
        # 全量同步：删除过期分块后写入，返回写入数量
        valid_ids = {self._id(int(chunk.id)) for chunk in chunks if chunk.id is not None}  # 有效 ID 集合
        current_ids = set(self.collection.get().get("ids", []))  # 当前 ID 集合
        stale_ids = sorted(current_ids - valid_ids)  # 待删 ID
        if stale_ids:  # 有过期则删除
            self.collection.delete(ids=stale_ids)
        return self.upsert_chunks(chunks, embeddings)  # 重写

    def has_exact_chunk_ids(self, chunks: list[KnowledgeChunk]) -> bool:
        # 判断库中是否恰好包含给定的分块 ID 集合
        valid_ids = {self._id(int(chunk.id)) for chunk in chunks if chunk.id is not None}  # 有效 ID
        current_ids = set(self.collection.get().get("ids", []))  # 当前 ID
        return current_ids == valid_ids

    def delete_source(self, source: str) -> None:
        # 删除某个来源的所有分块
        if not self.can_embed:  # 不可用则跳过
            return
        self.collection.delete(where={"source": source})

    def query(self, query_embedding: list[float], top_k: int) -> list[VectorSearchHit]:
        # 向量检索返回 top_k 结果
        result = self.collection.query(  # 查询
            query_embeddings=[query_embedding],  # 查询向量
            n_results=top_k,  # 返回条数
            include=["documents", "metadatas", "distances"],  # 需要的字段
        )
        documents = (result.get("documents") or [[]])[0]  # 文档
        metadatas = (result.get("metadatas") or [[]])[0]  # 元数据
        distances = (result.get("distances") or [[]])[0]  # 距离
        hits = []
        for index, document in enumerate(documents):  # 逐条组装
            metadata = metadatas[index] if index < len(metadatas) else {}  # 元数据
            distance = float(distances[index]) if index < len(distances) else 1.0  # 距离
            hits.append(  # 组装命中
                VectorSearchHit(
                    chunk_id=int(metadata["db_id"]) if metadata.get("db_id") is not None else None,  # ID
                    source=str(metadata.get("source", "")),  # 来源
                    source_index=int(metadata.get("source_index", 0)),  # 序号
                    content=document or "",  # 内容
                    score=1.0 / (1.0 + max(0.0, distance)),  # 距离转得分
                )
            )
        return hits

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # 对文本批量生成 embedding
        if not self.can_embed:  # 不可用则抛异常
            raise VectorStoreUnavailable(self.error or "Chroma + text-embedding-3-small 主检索方案不可用")
        return self._embed(texts)

    def snapshot(self) -> str | None:
        # 对持久化目录打快照并清理旧快照
        if not self.can_embed:  # 不可用则跳过
            return None
        if not self.persist_dir.exists():  # 目录不存在则跳过
            return None
        snapshot_root = self._resolve_path(self.settings.chroma_snapshot_dir)  # 快照根目录
        snapshot_root.mkdir(parents=True, exist_ok=True)  # 确保存在
        destination = snapshot_root / datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")  # 时间戳快照目录
        shutil.copytree(self.persist_dir, destination)  # 复制目录
        self._prune_snapshots(snapshot_root)  # 清理过期快照
        return str(destination)

    def count(self) -> int:
        # 返回分块总数
        if not self.can_embed:  # 不可用返回 0
            return 0
        return int(self.collection.count())

    def _embed(self, texts: list[str]) -> list[list[float]]:
        # 调用 OpenAI embeddings 接口做向量化
        payload = {  # 请求体
            "model": self.settings.openai_embedding_model,  # 模型
            "input": [text if text.strip() else " " for text in texts],  # 文本（空串用空格兜底）
        }
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}  # 鉴权
        response = httpx.post(  # 调接口
            f"{self.settings.openai_base_url}/embeddings",
            headers=headers,
            json=payload,
            timeout=self.settings.embedding_timeout_seconds,
        )
        response.raise_for_status()  # 出错即抛
        rows = sorted(response.json().get("data", []), key=lambda item: item.get("index", 0))  # 按 index 排序
        embeddings = [row.get("embedding") for row in rows]  # 取向量
        if len(embeddings) != len(texts) or any(not embedding for embedding in embeddings):  # 数量不匹配则报错
            raise VectorStoreUnavailable("OpenAI embeddings 接口返回向量数量不匹配")
        return [[float(value) for value in embedding] for embedding in embeddings]  # 转 float

    def _resolve_path(self, value: str) -> Path:
        # 相对路径补全为项目根下的绝对路径
        path = Path(value)
        return path if path.is_absolute() else self.settings.project_root / path

    def _prune_snapshots(self, snapshot_root: Path) -> None:
        # 保留最近 N 份快照，删除更旧的
        keep = max(1, self.settings.chroma_snapshot_keep)  # 保留份数
        snapshots = sorted([path for path in snapshot_root.iterdir() if path.is_dir()], reverse=True)  # 按名字倒序
        for stale in snapshots[keep:]:  # 删除超出的
            shutil.rmtree(stale, ignore_errors=True)

    def _id(self, chunk_id: int) -> str:
        # 生成 Chroma 的字符串 ID
        return f"knowledge-chunk-{chunk_id}"


ChromaKnowledgeVectorStore = ChromaKnowledgeStore  # 别名兼容