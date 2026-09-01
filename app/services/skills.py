from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AlertSkill:
    name: str
    description: str
    path: Path
    workflow: list[str]
    alert_types: list[str]
    status: str
    issues: list[str]


class AlertSkillRegistry:
    def __init__(self, root: Path | None = None):
        self.root = root or Path(__file__).resolve().parents[2] / "skills"

    def list_skills(self) -> list[AlertSkill]:
        return [self._load(path) for path in sorted(self.root.glob("*/SKILL.md"))]

    def status_items(self) -> list[dict[str, Any]]:
        return [
            {
                "name": item.name,
                "status": item.status,
                "description": item.description,
                "workflow": item.workflow,
                "alertTypes": item.alert_types,
                "issues": item.issues,
                "path": str(item.path),
            }
            for item in self.list_skills()
        ]

    def _load(self, path: Path) -> AlertSkill:
        issues: list[str] = []
        text = path.read_text(encoding="utf-8")
        metadata = _front_matter(text)
        if metadata is None:
            metadata = {}
            issues.append("缺少 front matter")

        name = str(metadata.get("name") or path.parent.name).strip()
        description = str(metadata.get("description") or "运维告警处理技能").strip()
        workflow = _as_str_list(metadata.get("workflow"))
        alert_types = [item.upper() for item in _as_str_list(metadata.get("alert_types") or metadata.get("alertTypes"))]

        if not metadata.get("name"):
            issues.append("缺少 name")
        if name != path.parent.name:
            issues.append("目录名和 Skill name 不一致")
        if not metadata.get("description"):
            issues.append("缺少 description")
        if not workflow:
            issues.append("缺少 workflow")
        if not alert_types:
            issues.append("缺少 alert_types")
        if "不得泄露" not in text and "脱敏" not in text and "敏感" not in text:
            issues.append("缺少安全边界说明")

        status = "READY"
        if any(issue in issues for issue in ["缺少 front matter", "缺少 name", "缺少 workflow"]):
            status = "FAIL"
        elif issues:
            status = "WARN"

        return AlertSkill(
            name=name,
            description=description,
            path=path,
            workflow=workflow,
            alert_types=alert_types,
            status=status,
            issues=issues,
        )


def _front_matter(text: str) -> dict[str, Any] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        return None
    return _parse_simple_yaml(lines[1:end])


def _parse_simple_yaml(lines: list[str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_key: str | None = None
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("  - ") and current_key:
            data.setdefault(current_key, []).append(raw.split("-", 1)[1].strip())
            continue
        if ":" in raw:
            key, value = raw.split(":", 1)
            current_key = key.strip()
            value = value.strip()
            data[current_key] = [] if value == "" else value.strip("\"'")
    return data


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


class AlertSkillLibrary:
    @staticmethod
    def status_items() -> list[dict[str, Any]]:
        return AlertSkillRegistry().status_items()
