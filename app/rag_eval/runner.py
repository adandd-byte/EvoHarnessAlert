from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.core.bootstrap import create_schema, seed_data
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.knowledge import KnowledgeService, SearchResult

def evaluate() -> dict:
    """
    执行完整的 RAG 检索效果评估。

    主要流程：
    1. 加载系统配置。
    2. 初始化数据库结构。
    3. 初始化评估所需数据。
    4. 读取 RAG 评估数据集。
    5. 对每个测试问题执行知识检索。
    6. 计算 Recall@K、Precision@K、MRR、NDCG@K 等指标。
    7. 汇总生成评估报告。
    8. 将报告保存为 JSON 文件。
    9. 返回评估结果。
    """

    # 获取系统配置。
    settings = get_settings()
    dataset_path = Path(settings.rag_eval_dataset)
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases, dataset_top_k = normalize_dataset(dataset)
    if dataset_top_k:
        settings.knowledge_top_k = dataset_top_k

    if settings.rag_eval_mock_knowledge:
        return evaluate_cases(
            MockKnowledgeService(cases),
            cases,
            settings.knowledge_top_k,
            settings.rag_eval_dataset,
            settings.rag_eval_output,
            mode="mock_knowledge",
        )

    # 确保数据库表结构已经创建。
    try:
        create_schema()
    except SQLAlchemyError as exc:
        raise RuntimeError(
            "RAG 评测需要 MySQL 可用。当前无法连接数据库；如果只是本地验证指标逻辑，"
            "可以设置 RAG_EVAL_MOCK_KNOWLEDGE=true 后运行。"
        ) from exc

    # 创建数据库 Session。
    db = SessionLocal()

    try:
        # 初始化评估所需的基础知识数据。
        # 例如预置知识文档、Runbook、FAQ 等。
        seed_data(db)

        # 创建知识检索服务。
        service = KnowledgeService(
            db,
            settings,
        )

        seed_eval_knowledge(service, cases)
        return evaluate_cases(service, cases, settings.knowledge_top_k, settings.rag_eval_dataset, settings.rag_eval_output, mode="mysql_knowledge")

    finally:
        # 无论评估成功还是发生异常，
        # 都确保数据库 Session 被关闭，
        # 防止连接泄漏。
        db.close()


def evaluate_cases(
    service: KnowledgeService,
    cases: list[dict],
    top_k: int,
    dataset: str,
    output_path: str,
    mode: str,
) -> dict:
    results = [evaluate_case(service, case, top_k) for case in cases]
    total = max(1, len(results))
    hits = [item for item in results if item["hit"]]
    report = {
        "createdAt": datetime.utcnow().isoformat(),
        "dataset": dataset,
        "mode": mode,
        "topK": top_k,
        "totalCases": len(results),
        "passed": bool(results) and len(hits) == len(results),
        "recallAtK": sum(item["recallAtK"] for item in results) / total,
        "precisionAtK": sum(item["precisionAtK"] for item in results) / total,
        "mrr": sum(item["reciprocalRank"] for item in results) / total,
        "ndcgAtK": sum(item["ndcgAtK"] for item in results) / total,
        "hitRate": len(hits) / total,
        "averageFirstRelevantRank": sum(item["firstRelevantRank"] for item in hits) / max(1, len(hits)),
        "results": results,
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def evaluate_case(
    service: KnowledgeService,
    case: dict,
    top_k: int,
) -> dict:
    """
    评估单个 RAG 测试 Case。

    对一个问题执行 Top-K 知识检索，
    然后判断每条召回结果是否相关，
    并计算该 Case 的检索指标。

    :param service:
        KnowledgeService 知识检索服务。

    :param case:
        单个测试用例，例如：
        {
            "id": "case-001",
            "question": "...",
            "expectedSources": [...],
            "expectedTerms": [...]
        }

    :param top_k:
        最大检索结果数量。

    :return:
        单个 Case 的评估结果。
    """

    # 使用 KnowledgeService 执行知识检索。
    #
    # retrieved 通常是按照相关性分数
    # 从高到低排列的知识 Chunk。
    retrieved = service.retrieve(
                case["query"],
        top_k,
    )

    # 获取期望命中的知识来源。
    #
    # 全部转换成小写，
    # 避免因为大小写不同导致匹配失败。
    expected_sources = {
        source.lower()
        for source in [
            *case.get("expectedSources", []),
            *case.get("expectedDocs", []),
        ]
    }

    # 获取期望出现的关键词。
    #
    # 同样统一转换成小写。
    expected_terms = expected_terms_for_case(case)

    # 保存 Top-K 检索结果的详细评估信息。
    items = []

    # 第一个相关结果的排名。
    #
    # 0 表示目前还没有找到相关结果。
    first_rank = 0

    # Top-K 中相关结果数量。
    relevant_count = 0
    relevant_sources = set()

    # 遍历召回结果。
    #
    # start=1：
    # 排名从 1 开始，而不是 Python 默认的 0。
    for index, item in enumerate(
        retrieved,
        start=1,
    ):
        # 判断当前知识 Chunk 是否与测试 Case 相关。
        relevant = is_relevant(
            item.source,
            item.content,
            expected_sources,
            expected_terms,
        )

        # 如果当前结果相关，则更新相关数量。
        if relevant:
            relevant_count += 1
            relevant_sources.add(item.source.lower())

            # 如果这是第一次发现相关结果，
            # 则记录它的排名。
            if first_rank == 0:
                first_rank = index

        # 保存当前召回 Chunk 的评估详情。
        items.append({
            # 检索排名。
            "rank": index,

            # 知识 Chunk 唯一 ID。
            "chunkId": item.chunk_id,

            # 知识来源。
            "source": item.source,

            # 检索相关度分数。
            "score": item.score,

            # 是否判定为相关结果。
            "relevant": relevant,

            # 知识内容预览。
            #
            # split() + join()：
            # 将多余换行和空格压缩成单个空格。
            #
            # [:160]：
            # 最多保留前 160 个字符。
            "preview": " ".join(
                item.content.split()
            )[:160],
        })

    # first_rank > 0 表示至少找到一个相关结果。
    hit = first_rank > 0

    # 返回单个测试 Case 的完整评估数据。
    return {
        # Case 唯一标识。
        "id": case["id"],

        # 用户问题。
        "query": case["query"],

        # 期望命中的来源。
        "expectedSources": case.get(
            "expectedSources",
            [],
        ),

        # 期望出现的关键词。
        "expectedTerms": case.get(
            "expectedTerms",
            [],
        ),
        "expectedDocs": case.get(
            "expectedDocs",
            [],
        ),
        "expectedRoute": case.get(
            "expectedRoute",
        ),
        "expectedPriority": case.get(
            "expectedPriority",
        ),

        # 实际召回结果。
        "retrieved": items,

        # 是否至少命中一个相关结果。
        "hit": hit,

        # 第一个相关结果的排名。
        #
        # 未命中时为 0。
        "firstRelevantRank": first_rank,

        # Recall@K。
        #
        # 当前实现采用二值方式：
        # 有命中 = 1
        # 无命中 = 0
        "recallAtK": recall_at_k(
            relevant_sources,
            relevant_count,
            expected_sources,
            expected_terms,
        ),

        # Precision@K。
        #
        # 计算：
        #
        # 相关结果数量 / K
        #
        # 例如：
        # Top 5 中有 2 条相关：
        # Precision@5 = 2 / 5 = 0.4
        "precisionAtK": (
            relevant_count / top_k
            if top_k > 0
            else 0.0
        ),

        # Reciprocal Rank。
        #
        # 如果第一条相关结果排名为：
        #
        # Rank 1 -> 1.0
        # Rank 2 -> 0.5
        # Rank 4 -> 0.25
        #
        # 完全未命中则为 0。
        "reciprocalRank": (
            1.0 / first_rank
            if hit
            else 0.0
        ),

        # 计算当前 Case 的 NDCG@K。
        "ndcgAtK": ndcg(items),
    }


def is_relevant(
    source: str,
    content: str,
    expected_sources: set[str],
    expected_terms: list[str],
) -> bool:
    """
    判断一个检索结果是否为相关结果。

    当前使用两种判定规则：

    规则 1：
        如果知识来源 source
        在 expectedSources 中，
        直接判定为相关。

    规则 2：
        如果知识内容中包含任意 expectedTerms，
        也判定为相关。

    两种规则满足任意一个即可。
    """

    # --------------------------------
    # 规则一：来源匹配
    # --------------------------------

    # 忽略 source 大小写。
    if source.lower() in expected_sources:
        return True

    # --------------------------------
    # 规则二：关键词匹配
    # --------------------------------

    # 将知识内容统一转换为小写。
    lower = content.lower()

    # 如果内容中出现任意期望关键词，
    # 则认为该知识 Chunk 与问题相关。
    #
    # len(term) >= 2：
    # 过滤过短关键词，
    # 避免单字符导致大量误匹配。
    return any(
        len(term) >= 2
        and term in lower
        for term in expected_terms
    )


def recall_at_k(
    relevant_sources: set[str],
    relevant_count: int,
    expected_sources: set[str],
    expected_terms: list[str],
) -> float:
    if expected_sources:
        return len(relevant_sources & expected_sources) / len(expected_sources)
    if expected_terms:
        return min(1.0, relevant_count / len(expected_terms))
    return 1.0 if relevant_count else 0.0


def normalize_dataset(dataset: object) -> tuple[list[dict], int | None]:
    if isinstance(dataset, dict):
        cases = dataset.get("cases", [])
        top_k = dataset.get("topK")
    else:
        cases = dataset
        top_k = None
    if not isinstance(cases, list):
        raise ValueError("RAG 评测数据集必须是 case 数组，或包含 cases 字段的对象")
    normalized = []
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"第 {index} 条 RAG case 不是对象")
        query = case.get("query") or case.get("question")
        if not query:
            raise ValueError(f"第 {index} 条 RAG case 缺少 query/question")
        item = dict(case)
        item["query"] = query
        item.setdefault("id", f"case-{index:03d}")
        normalized.append(item)
    return normalized, int(top_k) if top_k else None


def seed_eval_knowledge(service: KnowledgeService, cases: list[dict]) -> None:
    documents: dict[str, list[str]] = {}
    for case in cases:
        expected_docs = case.get("expectedDocs") or case.get("expectedSources") or []
        terms = expected_terms_for_case(case)
        for doc in expected_docs:
            source = str(doc)
            documents.setdefault(source, [])
            documents[source].append(
                "\n".join([
                    f"文档：{source}",
                    f"适用优先级：{case.get('expectedPriority') or case.get('severity') or 'P1'}",
                    f"适用告警类型：{case.get('expectedRoute') or case.get('alertType') or 'PROBLEM'}",
                    f"告警标题：{case.get('title', '')}",
                    f"告警 Query：{case.get('query', '')}",
                    f"关键标签：{json.dumps(case.get('labels', {}), ensure_ascii=False)}",
                    f"排查步骤：{' -> '.join(case.get('expectedSteps', []))}",
                    f"证据要求：{'、'.join(case.get('expectedEvidence', []))}",
                    f"关键词：{'、'.join(terms)}",
                ])
            )
    for source, parts in documents.items():
        service.ensure_source(source, "\n\n---\n\n".join(parts))


def expected_terms_for_case(case: dict) -> list[str]:
    terms = []
    terms.extend(case.get("expectedTerms", []))
    terms.extend(case.get("expectedDocs", []))
    terms.extend(case.get("expectedSteps", []))
    terms.extend([
        case.get("title", ""),
        case.get("expectedRoute", ""),
        case.get("expectedPriority", ""),
    ])
    labels = case.get("labels", {})
    if isinstance(labels, dict):
        terms.extend(str(value) for value in labels.values())
    return [str(term).lower() for term in terms if str(term).strip()]


class MockKnowledgeService:
    def __init__(self, cases: list[dict]):
        self.documents = []
        chunk_id = 1
        for case in cases:
            for doc in case.get("expectedDocs", []) or case.get("expectedSources", []):
                content = "\n".join([
                    f"文档：{doc}",
                    f"适用优先级：{case.get('expectedPriority') or case.get('severity')}",
                    f"适用告警类型：{case.get('expectedRoute') or case.get('alertType')}",
                    f"告警标题：{case.get('title', '')}",
                    f"告警 Query：{case.get('query', '')}",
                    f"关键标签：{json.dumps(case.get('labels', {}), ensure_ascii=False)}",
                    f"排查步骤：{' -> '.join(case.get('expectedSteps', []))}",
                    f"证据要求：{'、'.join(case.get('expectedEvidence', []))}",
                ])
                self.documents.append(SearchResult(chunk_id, str(doc), content, 0.0))
                chunk_id += 1

    def retrieve(self, query: str, top_k: int | None = None) -> list[SearchResult]:
        top_k = top_k or 4
        query_terms = set(tokenize_eval_text(query))
        ranked = []
        for document in self.documents:
            doc_terms = set(tokenize_eval_text(document.source + "\n" + document.content))
            overlap = len(query_terms & doc_terms)
            score = overlap / max(1, len(query_terms))
            if score > 0:
                ranked.append(SearchResult(document.chunk_id, document.source, document.content, score))
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[:top_k]


def tokenize_eval_text(text: str) -> list[str]:
    lowered = text.lower()
    ascii_terms = re.findall(r"[a-z0-9_./:-]+", lowered)
    chinese_terms = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    return [*ascii_terms, *chinese_terms]


def ndcg(
    items: list[dict],
) -> float:
    """
    计算 NDCG（Normalized Discounted Cumulative Gain）。

    NDCG 用于评估搜索/推荐结果的排名质量。

    核心思想：
    - 相关结果越靠前越好。
    - 排名越靠后，其贡献越低。
    - 最终结果归一化到 0 ~ 1。

    返回值：
    1.0 -> 排名接近理想状态。
    0.0 -> 没有相关结果。
    """

    # DCG：
    # 当前检索排序的实际累计折损增益。
    dcg = 0.0

    # 当前检索结果中的相关文档数量。
    relevant = 0

    # 遍历所有检索结果。
    #
    # enumerate 默认从 0 开始。
    for index, item in enumerate(items):

        # 只对相关结果计算贡献。
        if item["relevant"]:
            relevant += 1

            # 当前实现使用：
            #
            # 1 / ln(rank + 1)
            #
            # 因为 index 从 0 开始，
            # 所以代码中为 index + 2。
            #
            # 排名越靠后，贡献越小。
            dcg += (
                1.0
                / math.log(index + 2.0)
            )

    # 如果完全没有相关结果，
    # NDCG 直接为 0。
    if relevant == 0:
        return 0.0

    # 计算理想排序下的 DCG，即 IDCG。
    #
    # 假设所有相关结果都排在最前面：
    #
    # Rank 1
    # Rank 2
    # Rank 3
    # ...
    ideal = sum(
        1.0 / math.log(index + 2.0)
        for index in range(relevant)
    )

    # NDCG = DCG / IDCG
    #
    # 归一化后通常位于 0 ~ 1。
    return dcg / ideal




if __name__ == "__main__":
    report = evaluate()
    print("RAG evaluation completed.")
    for key in ["totalCases", "topK", "recallAtK", "precisionAtK", "mrr", "ndcgAtK", "hitRate", "averageFirstRelevantRank"]:
        print(f"{key}={report[key]}")
