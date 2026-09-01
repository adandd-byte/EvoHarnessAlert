import json
import math
from datetime import datetime
from pathlib import Path

from app.core.bootstrap import create_schema, seed_data
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.knowledge import KnowledgeService

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

    # 确保数据库表结构已经创建。
    create_schema()

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

        # 获取 RAG 评估数据集文件路径。
        dataset_path = Path(
            settings.rag_eval_dataset
        )

        # 读取 JSON 格式的评估数据集。
        #
        # 数据格式通常类似：
        # [
        #     {
        #         "id": "case-001",
        #         "question": "...",
        #         "expectedSources": [...],
        #         "expectedTerms": [...]
        #     }
        # ]
        cases = json.loads(
            dataset_path.read_text(
                encoding="utf-8"
            )
        )

        # 对数据集中的每一个测试 Case 进行检索评估。
        #
        # knowledge_top_k 表示每个问题最多召回多少条知识 Chunk。
        results = [
            evaluate_case(
                service,
                case,
                settings.knowledge_top_k,
            )
            for case in cases
        ]

        # 防止评估集为空时出现除零异常。
        #
        # 如果 results 为空，
        # total 至少设置为 1。
        total = max(
            1,
            len(results),
        )

        # 找出至少命中一个相关结果的测试 Case。
        hits = [
            item
            for item in results
            if item["hit"]
        ]

        # 汇总整个评估集的指标。
        report = {
            # 本次评估报告生成时间。
            "createdAt": (
                datetime.utcnow().isoformat()
            ),

            # 使用的评估数据集。
            "dataset": settings.rag_eval_dataset,

            # 知识检索 Top K。
            "topK": settings.knowledge_top_k,

            # 测试问题总数。
            "totalCases": len(results),

            # --------------------------------
            # Recall@K
            # --------------------------------
            #
            # 当前实现中每个 Case 的 recallAtK
            # 实际上是一个二值指标：
            #
            # 找到至少一个相关结果 = 1
            # 没有找到相关结果 = 0
            #
            # 所以这里求平均后，
            # 实际效果与 Hit Rate 非常接近。
            "recallAtK": sum(
                item["recallAtK"]
                for item in results
            ) / total,

            # --------------------------------
            # Precision@K
            # --------------------------------
            #
            # 表示 Top K 检索结果中，
            # 有多少比例被认为是相关结果。
            "precisionAtK": sum(
                item["precisionAtK"]
                for item in results
            ) / total,

            # --------------------------------
            # MRR
            # Mean Reciprocal Rank
            # --------------------------------
            #
            # 衡量“第一个相关结果”出现的位置。
            #
            # 第 1 名相关：1 / 1 = 1.0
            # 第 2 名相关：1 / 2 = 0.5
            # 第 3 名相关：1 / 3 ≈ 0.333
            #
            # 越接近 1，说明相关知识越靠前。
            "mrr": sum(
                item["reciprocalRank"]
                for item in results
            ) / total,

            # --------------------------------
            # NDCG@K
            # --------------------------------
            #
            # 衡量相关结果在整个排名中的位置质量。
            # 相关结果越靠前，NDCG 越高。
            "ndcgAtK": sum(
                item["ndcgAtK"]
                for item in results
            ) / total,

            # --------------------------------
            # Hit Rate
            # --------------------------------
            #
            # 在所有问题中，
            # 至少检索到一个相关结果的问题比例。
            "hitRate": len(hits) / total,

            # --------------------------------
            # Average First Relevant Rank
            # --------------------------------
            #
            # 对所有成功命中的测试 Case，
            # 统计第一个相关知识结果的平均排名。
            #
            # 数值越小越好：
            # 1 表示相关内容通常排在第一位。
            "averageFirstRelevantRank": (
                sum(
                    item["firstRelevantRank"]
                    for item in hits
                )
                / max(1, len(hits))
            ),

            # 保存每一个测试 Case 的详细结果，
            # 方便后续人工分析具体失败原因。
            "results": results,
        }

        # 获取评估报告输出路径。
        output = Path(
            settings.rag_eval_output
        )

        # 如果输出目录不存在，则递归创建。
        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # 将完整评估报告以格式化 JSON 写入文件。
        #
        # ensure_ascii=False：
        # 保留中文字符，不转换为 Unicode 转义。
        #
        # indent=2：
        # 使用 2 个空格进行缩进，方便阅读。
        output.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # 返回完整评估报告。
        return report

    finally:
        # 无论评估成功还是发生异常，
        # 都确保数据库 Session 被关闭，
        # 防止连接泄漏。
        db.close()


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
        case["question"],
        top_k,
    )

    # 获取期望命中的知识来源。
    #
    # 全部转换成小写，
    # 避免因为大小写不同导致匹配失败。
    expected_sources = {
        source.lower()
        for source in case.get(
            "expectedSources",
            [],
        )
    }

    # 获取期望出现的关键词。
    #
    # 同样统一转换成小写。
    expected_terms = [
        term.lower()
        for term in case.get(
            "expectedTerms",
            [],
        )
    ]

    # 保存 Top-K 检索结果的详细评估信息。
    items = []

    # 第一个相关结果的排名。
    #
    # 0 表示目前还没有找到相关结果。
    first_rank = 0

    # Top-K 中相关结果数量。
    relevant_count = 0

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
        "question": case["question"],

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
        "recallAtK": (
            1.0
            if hit
            else 0.0
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

