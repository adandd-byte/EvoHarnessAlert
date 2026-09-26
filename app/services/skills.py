from __future__ import annotations

from dataclasses import dataclass  # 数据类
from pathlib import Path  # 路径操作
from typing import Any  # 任意类型


@dataclass(frozen=True)
class AlertSkill:
    # 一个告警技能的元数据画像
    name: str            # 技能名称
    description: str     # 技能描述
    path: Path           # SKILL.md 文件路径
    workflow: list[str]  # 工作流步骤列表
    alert_types: list[str]  # 支持的告警类型
    status: str          # 健康状态（READY/WARN/FAIL）
    issues: list[str]    # 校验发现的问题


class AlertSkillRegistry:
    # 技术技能注册表：扫描 skills/ 目录并校验每个 SKILL.md 的完整性
    def __init__(self, root: Path | None = None):
        self.root = root or Path(__file__).resolve().parents[2] / "skills"  # 默认技能根目录

    def list_skills(self) -> list[AlertSkill]:
        # 列出所有技能（按目录名排序）
        return [self._load(path) for path in sorted(self.root.glob("*/SKILL.md"))]

    def status_items(self) -> list[dict[str, Any]]:
        # 输出所有技能的状态摘要（供接口展示）
        return [
            {
                "name": item.name,          # 技能名
                "status": item.status,      # 状态
                "description": item.description,  # 描述
                "workflow": item.workflow,  # 工作流
                "alertTypes": item.alert_types,  # 告警类型
                "issues": item.issues,      # 问题列表
                "path": str(item.path),     # 文件路径
            }
            for item in self.list_skills()
        ]

    def _load(self, path: Path) -> AlertSkill:
        issues: list[str] = []  # 问题收集
        text = path.read_text(encoding="utf-8")  # 读取 SKILL.md
        metadata = _front_matter(text)  # 解析 front matter
        if metadata is None:  # 无 front matter 则置空并记录
            metadata = {}
            issues.append("缺少 front matter")

        name = str(metadata.get("name") or path.parent.name).strip()  # 技能名
        description = str(metadata.get("description") or "运维告警处理技能").strip()  # 描述
        workflow = _as_str_list(metadata.get("workflow"))  # 工作流
        alert_types = [item.upper() for item in _as_str_list(metadata.get("alert_types") or metadata.get("alertTypes"))]  # 告警类型统一转大写

        if not metadata.get("name"):  # 缺 name
            issues.append("缺少 name")
        if name != path.parent.name:  # 目录名与技能名不一致
            issues.append("目录名和 Skill name 不一致")
        if not metadata.get("description"):  # 缺 description
            issues.append("缺少 description")
        if not workflow:  # 缺 workflow
            issues.append("缺少 workflow")
        if not alert_types:  # 缺 alert_types
            issues.append("缺少 alert_types")
        if "不得泄露" not in text and "脱敏" not in text and "敏感" not in text:  # 缺安全边界说明
            issues.append("缺少安全边界说明")

        status = "READY"  # 默认就绪
        if any(issue in issues for issue in ["缺少 front matter", "缺少 name", "缺少 workflow"]):  # 关键项缺失判 FAIL
            status = "FAIL"
        elif issues:  # 有非关键问题判 WARN
            status = "WARN"

        return AlertSkill(  # 组装技能对象
            name=name,
            description=description,
            path=path,
            workflow=workflow,
            alert_types=alert_types,
            status=status,
            issues=issues,
        )


def _front_matter(text: str) -> dict[str, Any] | None:
    # 解析 SKILL.md 开头的 --- 分隔的 front matter，失败返回 None
    lines = text.splitlines()  # 按行切分
    if not lines or lines[0].strip() != "---":  # 首行必须是 ---
        return None
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")  # 找结束 ---
    except StopIteration:
        return None
    return _parse_simple_yaml(lines[1:end])  # 解析中间内容


def _parse_simple_yaml(lines: list[str]) -> dict[str, Any]:
    # 极简 YAML 解析：支持顶层标量值和 - 列表项
    data: dict[str, Any] = {}
    current_key: str | None = None  # 当前键
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):  # 跳过空行与注释
            continue
        if raw.startswith("  - ") and current_key:  # 列表项追加到当前键
            data.setdefault(current_key, []).append(raw.split("-", 1)[1].strip())
            continue
        if ":" in raw:  # 键值对
            key, value = raw.split(":", 1)
            current_key = key.strip()
            value = value.strip()
            data[current_key] = [] if value == "" else value.strip("\"'")
    return data


def _as_str_list(value: Any) -> list[str]:
    # 把字符串或列表统一转成字符串列表
    if isinstance(value, list):  # 列表原样过滤为字符串
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():  # 逗号分隔字符串切分
        return [item.strip() for item in value.split(",") if item.strip()]
    return []  # 其他返回空


class AlertSkillLibrary:
    # 技能库工具入口
    @staticmethod
    def status_items() -> list[dict[str, Any]]:
        return AlertSkillRegistry().status_items()  # 直接透传注册表状态