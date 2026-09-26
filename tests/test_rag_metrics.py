"""用手算结果检查指标公式；不使用由答案合成的 Mock 知识库。"""
import math
from unittest.mock import Mock

import pytest

from app.rag_eval.runner import evaluate_case, evaluate_cases, normalize_dataset
from app.services.knowledge import SearchResult


def test_rag_metrics_match_hand_calculation():
    service = Mock()
    service.retrieve.return_value = [
        SearchResult(1, "无关文档", "其他内容", 0.9),
        SearchResult(2, "订单手册", "排障步骤", 0.8),
        SearchResult(3, "库存手册", "核验步骤", 0.7),
    ]
    case = {"id": "case-1", "query": "下单超时", "expectedSources": ["订单手册", "库存手册", "支付手册"]}
    result = evaluate_case(service, case, top_k=4)
    assert result["recallAtK"] == pytest.approx(2 / 3)
    assert result["precisionAtK"] == 0.5
    assert result["reciprocalRank"] == 0.5
    assert result["hit"] is True
    expected_ndcg = (1 / math.log2(3) + 1 / math.log2(4)) / (1 + 1 / math.log2(3))
    assert result["ndcgAtK"] == pytest.approx(expected_ndcg)
    service.retrieve.assert_called_once_with("下单超时", 4)


def test_no_retrieval_has_zero_metrics():
    service = Mock()
    service.retrieve.return_value = []
    result = evaluate_case(service, {"id": "empty", "query": "故障", "expectedSources": ["手册"]}, 4)
    for key in ("recallAtK", "precisionAtK", "reciprocalRank", "ndcgAtK"):
        assert result[key] == 0
    assert result["hit"] is False


def test_duplicate_source_does_not_inflate_document_recall():
    service = Mock()
    service.retrieve.return_value = [SearchResult(i, "订单手册", "步骤", 1) for i in range(3)]
    result = evaluate_case(service, {"id": "duplicate", "query": "故障", "expectedSources": ["订单手册", "库存手册"]}, 4)
    assert result["recallAtK"] == 0.5


def test_empty_evaluation_does_not_pass(tmp_path):
    report = evaluate_cases(Mock(), [], 4, "empty", str(tmp_path / "report.json"), "unit_fixture")
    assert report["passed"] is False
    assert report["totalCases"] == 0
    assert report["hitRate"] == 0


@pytest.mark.parametrize("dataset", [None, {"cases": "bad"}, ["bad"], [{"id": "missing-query"}]])
def test_invalid_dataset_is_rejected(dataset):
    with pytest.raises(ValueError):
        normalize_dataset(dataset)


def test_legacy_question_is_normalized_without_mutating_input():
    source = {"question": "怎么排查下单失败？"}
    cases, top_k = normalize_dataset({"cases": [source], "topK": 4})
    assert cases[0]["query"] == source["question"]
    assert cases[0]["id"] == "case-001"
    assert top_k == 4
    assert "query" not in source
