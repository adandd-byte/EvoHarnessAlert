from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    agent_framework: str = "event_driven_multi_agent"
    agent_runtime_max_steps: int = 8
    agent_runtime_max_claims_per_round: int = 4
    agent_runtime_max_claims_per_agent: int = 3
    agent_final_accept_min_confidence: float = 0.6
    ai_provider: str = "mock"
    ai_temperature: float = 0.2
    ai_max_tokens: int = 512
    ai_trust_env: bool = True
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "evoharness-alert-qwen2.5-7b:latest"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_wire_api: str = "chat_completions"
    openai_responses_stream: bool = False
    openai_extra_headers: dict[str, str] = {}
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    # ---- 微调模型资产（models/ 目录下的 GGUF + Modelfile，供 Ollama 加载）----
    finetuned_model_dir: str = "models/evoharness-alert-qwen2.5-7b"
    finetuned_model_file: str = "evoharness-alert-qwen2.5-7b-q4_k_m.gguf"
    finetuned_model_name: str = "evoharness-alert-qwen2.5-7b:latest"
    database_url: str = "mysql+pymysql://evoalert:evoalert@127.0.0.1:13306/evoharness_alert?charset=utf8mb4"
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_memory_ttl_seconds: int = 86400
    redis_memory_max_messages: int = 40
    redis_socket_timeout_seconds: float = 2.0
    knowledge_top_k: int = 4
    knowledge_candidate_k: int = 16
    knowledge_chunk_size: int = 512
    knowledge_chunk_overlap: int = 64
    knowledge_hybrid_vector_weight: float = 0.65
    knowledge_hybrid_bm25_weight: float = 0.35
    knowledge_rerank_enabled: bool = True
    knowledge_vector_enabled: bool = False
    knowledge_vector_required: bool = False
    chroma_persist_dir: str = "data/chroma"
    chroma_collection_name: str = "evoharness_alert_knowledge"
    chroma_snapshot_dir: str = "data/chroma-snapshots"
    chroma_snapshot_keep: int = 5
    embedding_timeout_seconds: float = 30.0
    rag_eval_dataset: str = "app/rag_eval/alert_eval_cases_zh.json"
    rag_eval_output: str = "target/rag-eval-report.json"
    rag_eval_enabled: bool = False
    rag_eval_exit_after_run: bool = False
    rag_eval_mock_knowledge: bool = False
    excel_path: str = "data/evoharness-alert-ledger.xlsx"
    alert_email_delivery_mode: str = "log"
    alert_email_rate_limit_per_minute: int = 30
    alert_email_from: str = ""
    alert_email_to: str = ""
    alert_email_subject_prefix: str = "[EvoHarnessAlert P0/P1告警]"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_timeout_seconds: float = 10.0
    tool_queue_enabled: bool = True
    tool_queue_poll_interval_seconds: float = 1.0
    tool_queue_batch_size: int = 10
    tool_queue_max_attempts: int = 3
    tool_queue_retry_delay_seconds: float = 15.0
    tool_queue_excel_workers: int = 1
    tool_queue_email_workers: int = 2

    # ---- 大上下文模型（Kimi / Moonshot 等 OpenAI 兼容服务） ----
    # Kimi 类模型上下文窗口很大，可把记忆容量与 RAG topK 放大，减少截断。
    provider_context_window_tokens: int = 2_000_000  # Kimi-K2 的 200 万 token 上下文窗口上限（2M）；仅作为是否放大检索的记忆/知识开关的阈值参考，不代表每次都塞满
    memory_use_full_context: bool = False  # True 时不再强制压缩近期消息，直接放入大窗口
    memory_max_messages_full_context: int = 4000  # 大窗口模型下的 Redis 近期消息上限
    knowledge_top_k_full_context: int = 24  # 大窗口模型下允许召回更多候选片段

    # ---- 代码分析沙箱 ----
    # backend 可选 process（本地轻量进程沙箱，内置）或 docker（走 docker CLI 起容器）。
    sandbox_backend: str = "process"
    sandbox_image: str = "python:3.11-slim"  # 仅在 docker 后端使用
    sandbox_network_enabled: bool = False
    sandbox_timeout_seconds: float = 120.0
    sandbox_memory_mb: int = 1024
    sandbox_cpu_quota: float = 1.0
    sandbox_workspace_dir: str = "data/sandbox"
    sandbox_git_branch: str = "main"

    # ---- 单轮告警研判耗时估算 ----
    estimation_default_llm_ms: int = 3000  # 单次大模型调用（含思考）耗时常量
    estimation_default_rag_ms: int = 500  # 一次知识库检索（embedding+bm25+rerank）耗时常量
    estimation_default_tool_ms: int = 8000  # 一次沙箱代码分析（含启动）耗时常量
    estimation_max_parallel: int = 5  # 一次告警最多并行的微服务分析数

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]


@lru_cache
def get_settings() -> Settings:
    return Settings()
