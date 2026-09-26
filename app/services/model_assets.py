"""微调模型资产状态：检查 models/ 目录下的 GGUF 与 Modelfile 是否就绪。

参考 mindbridge-py 的 app/services/model_assets.py 移植。

本项目用 Qwen2.5-7B 做了告警领域微调，产物是：
    models/evoharness-alert-qwen2.5-7b/
      ├── evoharness-alert-qwen2.5-7b-q4_k_m.gguf   # 量化后的模型权重
      └── Modelfile                                  # Ollama 模型描述（FROM ./xxx.gguf）

通过 `ollama create evoharness-alert-qwen2.5-7b -f Modelfile` 注册后，
AI_PROVIDER=ollama 即可加载本地微调模型。本模块给前端/运维一个
"模型资产是否就绪"的自检接口，避免"启动了才发现模型没下载"的尴尬。
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings


def finetuned_model_status(settings: Settings) -> dict:
    """返回微调模型资产的自检结果（文件是否存在、大小、Modelfile 是否就绪）。"""
    root = settings.project_root
    model_dir = resolve_model_dir(settings)
    gguf_path = model_dir / settings.finetuned_model_file
    modelfile_path = model_dir / "Modelfile"
    return {
        "name": settings.finetuned_model_name,
        # 目录用相对路径展示，避免把部署机的绝对路径泄露给前端
        "directory": str(model_dir.relative_to(root)) if model_dir.is_relative_to(root) else str(model_dir),
        "ggufFile": settings.finetuned_model_file,
        "ggufExists": gguf_path.exists(),
        "ggufSizeBytes": gguf_path.stat().st_size if gguf_path.exists() else 0,
        "ggufSizeHuman": _human_size(gguf_path.stat().st_size) if gguf_path.exists() else "0B",
        "modelfileExists": modelfile_path.exists(),
        "ollamaCreateCommand": "ollama create evoharness-alert-qwen2.5-7b -f Modelfile",
    }


def resolve_model_dir(settings: Settings) -> Path:
    """解析模型目录：支持绝对路径，也支持相对项目根目录的路径。"""
    path = Path(settings.finetuned_model_dir)
    return path if path.is_absolute() else settings.project_root / path


def _human_size(size_bytes: int) -> str:
    """把字节数转成人类可读的大小（4.7GB 这种），方便直接展示。"""
    value = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"
