from __future__ import annotations

import json  # JSON
import logging  # 日志
import math  # 数学
import re  # 正则
from dataclasses import dataclass  # 数据类
from typing import Hashable  # 可哈希类型

from pypdf import PdfReader  # PDF 解析
from sqlalchemy.orm import Session  # ORM 会话

from app.core.config import Settings  # 全局配置
from app.models.entities import KnowledgeChunk  # 知识分块实体
from app.services.vector_store import FALLBACK_RETRIEVAL_LABEL, PRIMARY_RETRIEVAL_LABEL, ChromaKnowledgeStore  # 向量检索


logger = logging.getLogger(__name__)  # 模块日志


@dataclass
class SearchResult:
    # 一次检索命中的结果
    chunk_id: int | None  # 知识分块 ID（可能为空）
    source: str  # 来源（文件名）
    content: str  # 命中内容
    score: float  # 相关度得分


@dataclass
class RetrievalCandidate:
    # 融合排序的候选：同时记录向量分数与 BM25 分数
    result: SearchResult  # 检索结果
    vector_score: float = 0.0  # 归一化后的向量分数
    bm25_score: float = 0.0  # 归一化后的 BM25 分数


class KnowledgeService:
    # 知识检索服务：RAG 主入口（数据库分块 + 向量/BM25 混合召回 + 本地重排）
    def __init__(self, db: Session, settings: Settings):
        self.db = db  # 数据库会话
        self.settings = settings  # 全局配置
        self.vector_store = ChromaKnowledgeStore(settings)  # 向量存储

    def count(self) -> int:
        # 返回数据库中的知识分块总数
        return self.db.query(KnowledgeChunk).count()

    def ensure_source(self, source: str, content: str) -> int:
        # 确保某来源的分块已入库；内容不变则跳过，否则重新摄入
        chunks = chunk_text(content, self.settings.knowledge_chunk_size, self.settings.knowledge_chunk_overlap)  # 切分
        existing = [  # 取该来源已有内容
            chunk.content  # 内容
            for chunk in self.db.query(KnowledgeChunk)
            .filter(KnowledgeChunk.source == source)  # 按来源过滤
            .order_by(KnowledgeChunk.source_index.asc())  # 按序号排序
            .all()
        ]
        if existing == chunks:  # 内容不变直接返回
            return len(existing)
        return self.ingest(source, content)  # 变化则重摄

    def status(self) -> dict:
        """ 
        返回知识检索 / 向量检索模块当前运行状态。 
        主要用于： 1. 查看当前检索策略。 
        2. 检查数据库知识块数量。 
        3. 检查向量检索是否启用、是否可用。 
        4. 查看 Embedding 模型和 Chroma 配置。 
        5. 查看 Hybrid Retrieval 和 Rerank 配置。 
        6. 暴露向量存储初始化或调用异常，方便健康检查和排障。 
        """
        # 向量库中的知识 Chunk 数量。
        # 默认使用 None， 
        # # 因为当向量能力不可用时无法获取真实数量。
        vector_chunks = None
        # 获取 VectorStore 当前已经记录的错误信息。 
        # 如果 vector_store 没有 error 属性， 
        # 则默认返回空字符串。
        vector_error = getattr(self.vector_store, "error", "")
        # can_embed 表示当前向量存储具备 Embedding / Vector 能力。 
        # 通常需要：  
        # - OpenAI API Key 可用 
        # - Embedding 模型可调用  
        # - ChromaDB 正常
        if self.vector_store.can_embed:
            try:
                # 查询向量数据库中已经保存的 Chunk 数量。
                vector_chunks = self.vector_store.count()
            except Exception as exc:
                # 如果访问向量库失败， # 不让 status() 本身抛出异常， 
                # 而是将错误转换成字符串返回给调用方。 
                #  示例： 
                #  RuntimeError: chromadb connection failed
                vector_error = f"{type(exc).__name__}: {exc}"
        return {
            "retrievalOrder": [  # 检索顺序
                PRIMARY_RETRIEVAL_LABEL,  # 主检索
                f"{FALLBACK_RETRIEVAL_LABEL} when OPENAI_API_KEY/chromadb/vector call is unavailable",  # 降级检索
            ],
            # 当前首选检索方式
            "primaryRetrieval": PRIMARY_RETRIEVAL_LABEL,
            # 主检索不可用时采用的降阶检索方式
            "fallbackRetrieval": FALLBACK_RETRIEVAL_LABEL,
            # 数据库中 KnowledgeChunk 的总数量。
            # 这里通常统计关系型数据库中的知识块，
            "databaseChunks": self.count(),
            # 配置层面是否开启向量检索。
            "vectorEnabled": self.settings.knowledge_vector_enabled,
            # 当前运行环境中向量能力是否真正可用。
            "vectorAvailable": self.vector_store.can_embed,
            "vectorRequired": self.settings.knowledge_vector_required,  # 向量是否必需
            "embeddingModel": self.settings.openai_embedding_model,  # embedding 模型
            "vectorChunks": vector_chunks,  # 向量分块数
            "chromaPersistDir": self.settings.chroma_persist_dir,  # 持久化目录
            "chromaCollectionName": self.settings.chroma_collection_name,  # 集合名
            "chromaSnapshotDir": self.settings.chroma_snapshot_dir,  # 快照目录
            "candidateK": self.settings.knowledge_candidate_k,  # 候选数
            "hybridVectorWeight": self.settings.knowledge_hybrid_vector_weight,  # 向量权重
            "hybridBm25Weight": self.settings.knowledge_hybrid_bm25_weight,  # BM25 权重
            "rerankEnabled": self.settings.knowledge_rerank_enabled,  # 是否重排
            "vectorError": vector_error,  # 向量错误信息
        }

    def rebuild_vector_index(self) -> int:
        # 全量重建向量索引（同步所有分块）
        if not self.vector_store.can_embed:  # 向量不可用则报错
            raise RuntimeError(getattr(self.vector_store, "error", "") or "Chroma 向量库不可用")
        rows = self.db.query(KnowledgeChunk).order_by(KnowledgeChunk.source.asc(), KnowledgeChunk.source_index.asc()).all()  # 取全部分块
        self._sync_vector_chunks(rows)  # 同步向量
        self.db.commit()
        return len(rows)

    def backup_vector_index(self) -> str:
        # 备份向量索引快照
        if not self.vector_store.can_embed:  # 向量不可用则报错
            raise RuntimeError(getattr(self.vector_store, "error", "") or "Chroma 向量库不可用")
        snapshot = self.vector_store.snapshot()  # 打快照
        if snapshot is None:  # 无快照则报错
            raise RuntimeError("Chroma 持久化目录不存在，无法生成快照")
        return snapshot

    def ingest(self, source: str, content: str) -> int:
        # 摄入一份知识：切分、删除旧数据、入库、建向量索引
        chunks = chunk_text(content, self.settings.knowledge_chunk_size, self.settings.knowledge_chunk_overlap)  # 切分
        self._delete_vector_source(source)  # 删旧向量
        self.db.query(KnowledgeChunk).filter(KnowledgeChunk.source == source).delete()  # 删旧数据库记录
        rows = []  # 新分块行
        for index, chunk in enumerate(chunks):  # 逐块建行
            row = KnowledgeChunk(source=source, source_index=index, content=chunk)  # 构造分块
            self.db.add(row)
            rows.append(row)
        self.db.flush()  # 落库取 ID
        self._index_vector_chunks(rows)  # 建向量索引
        self.db.commit()
        return len(chunks)

    def ingest_file(self, filename: str, data: bytes) -> int:
        # 摄入文件：PDF 走抽取，其余按 UTF-8 解码
        lower = filename.lower()  # 文件名小写
        if lower.endswith(".pdf"):  # PDF 抽取文本
            text = extract_pdf(data)
        else:  # 普通文本解码
            text = data.decode("utf-8", errors="ignore")
        return self.ingest(filename, text)

    def retrieve(self, query: str, top_k: int | None = None) -> list[SearchResult]:
        # 检索入口：向量+BM25 混合召回，再本地重排，最后展开最佳命中
        top_k = top_k or self.settings.knowledge_top_k  # 返回条数
        candidate_k = self._candidate_k(top_k)  # 候选条数
        chunks = self.db.query(KnowledgeChunk).all()  # 全部分块
        # Primary retrieval now uses hybrid recall: semantic vector candidates
        # plus BM25 keyword candidates, followed by deterministic local rerank.
        vector_results = self._retrieve_vector(query, candidate_k)  # 向量召回
        bm25_results = self._retrieve_bm25(query, candidate_k, chunks)  # BM25 召回
        ranked = self._fuse_and_rerank(query, vector_results, bm25_results, top_k)  # 融合重排
        if ranked:  # 有结果则展开最佳命中
            return self._expand_best(ranked, top_k)
        return []  # 无结果

    def _retrieve_bm25(self, query: str, top_k: int, chunks: list[KnowledgeChunk] | None = None) -> list[SearchResult]:
        # BM25 关键词召回
        chunks = chunks if chunks is not None else self.db.query(KnowledgeChunk).all()  # 分块
        scores = bm25_scores(query, chunks)  # 计算 BM25 分数
        ranked = [  # 保留有分的
            SearchResult(chunk.id, chunk.source, chunk.content, scores.get(chunk.id, 0.0))  # 构造结果
            for chunk in chunks
            if chunk.id is not None and scores.get(chunk.id, 0.0) > 0
        ]
        ranked.sort(key=lambda item: item.score, reverse=True)  # 降序排序
        return ranked[:top_k]  # 取前 top_k

    def _fuse_and_rerank(
        self,
        query: str,
        vector_results: list[SearchResult],
        bm25_results: list[SearchResult],
        top_k: int,
    ) -> list[SearchResult]:
        # 融合排序：向量分数与 BM25 分数按权重加权，再本地重排
        candidates: dict[Hashable, RetrievalCandidate] = {}  # 候选池
        vector_scores = {result_key(item): item.score for item in vector_results if item.score > 0}  # 向量分数表
        bm25_scores_by_key = {result_key(item): item.score for item in bm25_results if item.score > 0}  # BM25 分数表
        normalized_vector = normalize_scores(vector_scores)  # 归一化向量分
        normalized_bm25 = normalize_scores(bm25_scores_by_key)  # 归一化 BM25 分

        for item in [*vector_results, *bm25_results]:  # 合并两个来源
            key = result_key(item)  # 结果键
            candidate = candidates.get(key)  # 取候选
            if candidate is None:  # 无则新建
                candidate = RetrievalCandidate(result=item)
                candidates[key] = candidate
            candidate.vector_score = max(candidate.vector_score, normalized_vector.get(key, 0.0))  # 取更高向量分
            candidate.bm25_score = max(candidate.bm25_score, normalized_bm25.get(key, 0.0))  # 取更高 BM25 分

        if not candidates:  # 无候选
            return []

        vector_weight = max(0.0, self.settings.knowledge_hybrid_vector_weight) if vector_results else 0.0  # 向量权重
        bm25_weight = max(0.0, self.settings.knowledge_hybrid_bm25_weight)  # BM25 权重
        if vector_weight == 0.0 and bm25_weight == 0.0:  # 双零则退回纯 BM25
            bm25_weight = 1.0
        total_weight = vector_weight + bm25_weight  # 总权重

        fused = []  # 融合结果
        for candidate in candidates.values():  # 逐候选加权
            score = (  # 加权求和
                candidate.vector_score * vector_weight
                + candidate.bm25_score * bm25_weight
            ) / total_weight
            fused.append(replace_score(candidate.result, score))  # 替换分数

        fused.sort(key=lambda item: item.score, reverse=True)  # 降序
        fused = fused[:self._candidate_k(top_k)]  # 截断到候选数
        return self._rerank(query, fused, top_k)  # 本地重排

    def _rerank(self, query: str, candidates: list[SearchResult], top_k: int) -> list[SearchResult]:
        # 本地重排：结合词法、覆盖率、短语打分
        if not self.settings.knowledge_rerank_enabled:  # 未开启直接截断
            return candidates[:top_k]
        reranked = [  # 逐条重新打分
            replace_score(item, rerank_score(query, item.content, item.score))  # 重排分
            for item in candidates
        ]
        reranked.sort(key=lambda item: item.score, reverse=True)  # 降序
        return reranked[:top_k]  # 取前 top_k

    def _candidate_k(self, top_k: int) -> int:
        # 候选数至少为 top_k 与配置值的较大者
        return max(top_k, self.settings.knowledge_candidate_k)

    def _retrieve_vector(self, query: str, top_k: int) -> list[SearchResult]:
        # 向量召回候选
        if not self.vector_store.can_embed:  # 不可用返回空
            return []
        try:
            self._ensure_vector_index()  # 确保索引就绪
            query_embedding = self.vector_store.embed_texts([query])[0]  # 查询向量
            hits = self.vector_store.query(query_embedding, top_k)  # 向量查询
        except Exception as exc:  # 出错降级
            self._handle_vector_error("retrieve", exc)
            return []
        results = []  # 组装结果
        for hit in hits:  # 逐命中回溯分块
            chunk = self.db.get(KnowledgeChunk, hit.chunk_id) if hit.chunk_id is not None else None  # 查分块
            results.append(  # 构造结果
                SearchResult(
                    chunk.id if chunk is not None else hit.chunk_id,  # ID
                    chunk.source if chunk is not None else hit.source,  # 来源
                    chunk.content if chunk is not None else hit.content,  # 内容
                    hit.score,  # 得分
                )
            )
        return results

    def _ensure_vector_index(self) -> None:
        # 确保向量索引与数据库一致，不一致则同步
        rows = self.db.query(KnowledgeChunk).order_by(KnowledgeChunk.source.asc(), KnowledgeChunk.source_index.asc()).all()  # 全部分块
        if not rows:  # 无分块
            return
        if (  # 已同步则跳过
            self.vector_store.count() == len(rows)  # 数量一致
            and all(row.embedding_json for row in rows)  # 都有向量
            and self.vector_store.has_exact_chunk_ids(rows)  # ID 完全一致
        ):
            return
        self._sync_vector_chunks(rows)  # 同步向量
        self.db.commit()

    def _delete_vector_source(self, source: str) -> None:
        # 删除某来源的向量记录
        if not self.vector_store.can_embed:  # 不可用则跳过
            return
        try:
            self.vector_store.delete_source(source)  # 删除
        except Exception as exc:  # 出错降级
            self._handle_vector_error("delete_source", exc)

    def _index_vector_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        # 为分块生成向量并 upsert
        if not chunks or not self.vector_store.can_embed:  # 无分块或不可用则跳过
            return
        try:  # 索引全流程
            embeddings = self._embeddings_for_chunks(chunks)  # 生成向量
            for chunk, embedding in zip(chunks, embeddings):  # 回填向量 JSON
                chunk.embedding_json = json.dumps(embedding, separators=(",", ":"))
            self.vector_store.upsert_chunks(chunks, embeddings)  # 写入向量库
        except Exception as exc:  # 出错降级
            self._handle_vector_error("index", exc)

    def _sync_vector_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        # 同步分块向量（全量对齐）
        if not chunks or not self.vector_store.can_embed:  # 无分块或不可用则跳过
            return
        try:  # 同步全流程
            embeddings = self._embeddings_for_chunks(chunks)  # 生成向量
            for chunk, embedding in zip(chunks, embeddings):  # 回填向量 JSON
                chunk.embedding_json = json.dumps(embedding, separators=(",", ":"))
            self.vector_store.sync_chunks(chunks, embeddings)  # 全量同步
        except Exception as exc:  # 出错降级
            self._handle_vector_error("sync", exc)

    def _embeddings_for_chunks(self, chunks: list[KnowledgeChunk]) -> list[list[float]]:
        # 为分块准备向量：缺失的调用 embedding 接口补齐
        embeddings: list[list[float] | None] = []  # 向量列表
        missing_indexes = []  # 缺失向量位置
        missing_texts = []  # 缺失向量文本
        for index, chunk in enumerate(chunks):  # 逐块检查
            embedding = parse_embedding(chunk.embedding_json)  # 解析已有向量
            embeddings.append(embedding)
            if embedding is None:  # 缺失则记录
                missing_indexes.append(index)
                missing_texts.append(chunk.content)
        if missing_texts:  # 有缺失则批量生成
            new_embeddings = self.vector_store.embed_texts(missing_texts)  # 调 embedding
            for index, embedding in zip(missing_indexes, new_embeddings):  # 回填
                embeddings[index] = embedding
        resolved = [embedding for embedding in embeddings if embedding is not None]  # 过滤缺失
        if len(resolved) != len(chunks):  # 数量不匹配则报错
            raise ValueError("Embedding response count did not match knowledge chunks.")
        return resolved

    def _handle_vector_error(self, action: str, exc: Exception) -> None:
        # 向量错误处理：必需则上抛，否则降级并告警
        if self.settings.knowledge_vector_required:  # 必需模式直接抛
            raise exc
        logger.warning(  # 可选模式记录降级日志
            "%s %s failed; falling back to %s: %s",
            PRIMARY_RETRIEVAL_LABEL,
            action,
            FALLBACK_RETRIEVAL_LABEL,
            exc,
        )

    def _expand_best(self, ranked: list[SearchResult], top_k: int) -> list[SearchResult]:
        # 展开最佳命中并合并其余，填满 top_k
        if not ranked:  # 空则返回空
            return []
        best = ranked[0]  # 最佳命中
        expanded = self._expand(best)  # 展开相邻分块
        results = [expanded]  # 首元素为展开结果
        for item in ranked[1:]:  # 填充其余
            if item.chunk_id != expanded.chunk_id and len(results) < top_k:  # 去重且未满
                results.append(item)
        return results

    def _expand(self, result: SearchResult) -> SearchResult:
        # 把命中扩展到同来源前后相邻分块，提供更完整上下文
        if result.chunk_id is None:  # 无 ID 直接返回
            return result
        chunk = self.db.get(KnowledgeChunk, result.chunk_id)  # 查分块
        if chunk is None:  # 不存在直接返回
            return result
        neighbors = (  # 查相邻分块
            self.db.query(KnowledgeChunk)
            .filter(KnowledgeChunk.source == chunk.source)  # 同来源
            .filter(KnowledgeChunk.source_index >= max(0, chunk.source_index - 1))  # 前一个
            .filter(KnowledgeChunk.source_index <= chunk.source_index + 1)  # 后一个
            .order_by(KnowledgeChunk.source_index.asc())  # 顺序
            .all()
        )
        return SearchResult(chunk.id, chunk.source, "\n\n".join(item.content for item in neighbors), result.score)  # 拼接相邻内容


def chunk_text(content: str, size: int, overlap: int) -> list[str]:
    # 滑窗切分文本为分块（带重叠）
    text = re.sub(r"\s+", " ", content or "").strip()  # 合并空白并去首尾
    if not text:  # 空文本
        return []
    chunks = []  # 分块列表
    start = 0  # 起始位置
    step = max(1, size - overlap)  # 步长
    while start < len(text):  # 滑动到末尾
        chunks.append(text[start:start + size])  # 取块
        start += step  # 前进
    return chunks


def hybrid_score(query: str, content: str) -> float:
    # 混合相似度：余弦为主、关键词为辅
    return token_cosine(query, content) * 0.75 + keyword_score(query, content) * 0.25  # 加权


def bm25_scores(query: str, chunks: list[KnowledgeChunk]) -> dict[int, float]:
    # BM25 打分：对每个分块计算与查询的相关度
    query_terms = counts(tokenize(query))  # 查询词频
    if not query_terms or not chunks:  # 空查询或空分块
        return {}

    documents = []  # 文档表
    doc_freqs: dict[str, int] = {}  # 词在文档中的出现次数
    for chunk in chunks:  # 逐块统计
        if chunk.id is None:  # 无 ID 跳过
            continue
        token_counts = counts(tokenize(chunk.content))  # 词频
        documents.append((chunk.id, token_counts, sum(token_counts.values())))  # 记录
        for term in token_counts:  # 累加文档频率
            doc_freqs[term] = doc_freqs.get(term, 0) + 1

    total_docs = len(documents)  # 文档总数
    if total_docs == 0:  # 无文档
        return {}
    average_length = sum(length for _, _, length in documents) / total_docs or 1.0  # 平均长度
    k1 = 1.5  # 词频饱和度参数
    b = 0.75  # 长度归一化参数
    scores: dict[int, float] = {}  # 分数表

    for chunk_id, token_counts, doc_length in documents:  # 逐文档打分
        score = 0.0  # 累计分
        length_norm = k1 * (1.0 - b + b * doc_length / average_length)  # 长度归一化
        for term, query_frequency in query_terms.items():  # 逐查询词
            term_frequency = token_counts.get(term, 0)  # 词频
            if term_frequency == 0:  # 无此词跳过
                continue
            doc_frequency = doc_freqs.get(term, 0)  # 文档频率
            idf = math.log(1.0 + (total_docs - doc_frequency + 0.5) / (doc_frequency + 0.5))  # IDF
            query_boost = 1.0 + math.log(query_frequency)  # 查询词加权
            score += idf * query_boost * (term_frequency * (k1 + 1.0)) / (term_frequency + length_norm)  # 累加
        if score > 0:  # 仅保留正分
            scores[chunk_id] = score
    return scores


def rerank_score(query: str, content: str, base_score: float) -> float:
    # 重排分：基准分 + 词法 + 覆盖率 + 短语
    lexical = hybrid_score(query, content)  # 词法分
    coverage = query_token_coverage(query, content)  # 覆盖率
    phrase = phrase_score(query, content)  # 短语分
    return base_score * 0.55 + lexical * 0.25 + coverage * 0.15 + phrase * 0.05  # 加权


def query_token_coverage(query: str, content: str) -> float:
    # 查询词在内容中的覆盖率
    query_tokens = set(tokenize(query))  # 查询词集
    if not query_tokens:  # 空查询
        return 0.0
    content_tokens = set(tokenize(content))  # 内容词集
    return len(query_tokens & content_tokens) / len(query_tokens)  # 交集占比


def phrase_score(query: str, content: str) -> float:
    # 短语分：整句包含则满分，否则用关键词分
    normalized_query = compact_text(query)  # 压缩查询
    if not normalized_query:  # 空查询
        return 0.0
    normalized_content = compact_text(content)  # 压缩内容
    if normalized_query in normalized_content:  # 完全包含
        return 1.0
    return keyword_score(query, content)  # 回落关键词分


def compact_text(text: str) -> str:
    # 去掉所有空白并转小写（用于短语匹配）
    return re.sub(r"\s+", "", text.lower())


def normalize_scores(scores: dict[Hashable, float]) -> dict[Hashable, float]:
    # 分数归一化到 [0,1]，保留正分信息
    positives = [score for score in scores.values() if score > 0]  # 正分
    if not positives:  # 无正分
        return {key: 0.0 for key in scores}
    lowest = min(positives)  # 最小值
    highest = max(positives)  # 最大值
    if math.isclose(lowest, highest):  # 全相同则正分为 1
        return {key: 1.0 if score > 0 else 0.0 for key, score in scores.items()}
    return {  # 线性归一化
        key: (score - lowest) / (highest - lowest) if score > 0 else 0.0  # 归一
        for key, score in scores.items()
    }


def result_key(result: SearchResult) -> Hashable:
    # 生成结果键：优先用 chunk_id，否则用来源+内容
    return result.chunk_id if result.chunk_id is not None else (result.source, result.content)


def replace_score(result: SearchResult, score: float) -> SearchResult:
    # 复制结果并替换分数（保持其他字段不变）
    return SearchResult(result.chunk_id, result.source, result.content, score)


def parse_embedding(raw: str | None) -> list[float] | None:
    # 解析分块的向量 JSON，非法则返回 None
    if not raw:  # 空值
        return None
    try:
        data = json.loads(raw)  # 反序列化
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data:  # 非列表或空
        return None
    if not all(isinstance(item, (int, float)) for item in data):  # 元素非数值
        return None
    return [float(item) for item in data]


def tokenize(text: str) -> list[str]:
    # 分词：英文单词、数字、下划线，以及中文单字和双字组合
    words = re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]", text.lower())  # 词与中文字符
    grams = words[:]  # 拷贝
    compact = "".join(ch for ch in text.lower() if "\u4e00" <= ch <= "\u9fff")  # 提取中文
    grams.extend(compact[i:i + 2] for i in range(max(0, len(compact) - 1)))  # 中文双字组合
    return [item for item in grams if item.strip()]  # 过滤空


def token_cosine(left: str, right: str) -> float:
    # 基于词频向量的余弦相似度
    left_counts = counts(tokenize(left))  # 左侧词频
    right_counts = counts(tokenize(right))  # 右侧词频
    if not left_counts or not right_counts:  # 任一侧空
        return 0.0
    dot = sum(value * right_counts.get(key, 0) for key, value in left_counts.items())  # 点积
    left_norm = math.sqrt(sum(value * value for value in left_counts.values()))  # 左模
    right_norm = math.sqrt(sum(value * value for value in right_counts.values()))  # 右模
    return 0.0 if left_norm == 0 or right_norm == 0 else dot / (left_norm * right_norm)  # 余弦


def keyword_score(query: str, content: str) -> float:
    # 关键词分：查询关键词在内容中的命中比例
    terms = [term for term in re.split(r"[\s，。！？、；：,.!?;:]+", query.lower()) if len(term) >= 2]  # 拆分关键词
    if not terms:  # 无关键词
        return 0.0
    lower = content.lower()  # 内容小写
    matched = sum(1 for term in terms if term in lower)  # 命中数
    return min(1.0, matched / len(terms))  # 命中比例


def counts(values: list[str]) -> dict[str, int]:
    # 统计词频
    result: dict[str, int] = {}
    for value in values:  # 逐词
        result[value] = result.get(value, 0) + 1
    return result


def extract_pdf(data: bytes) -> str:
    # 从 PDF 字节流提取文本
    from io import BytesIO  # 字节流

    reader = PdfReader(BytesIO(data))  # 打开 PDF
    return "\n".join(page.extract_text() or "" for page in reader.pages)  # 逐页拼接