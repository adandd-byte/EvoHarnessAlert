"""技能文件缺失、元数据不一致和健康状态的功能测试。"""
import pytest

from app.services.skills import AlertSkillRegistry


VALID = """---
name: problem
description: 问题类告警取证
alert_types:
  - problem
workflow:
  - 确认影响
  - 查询日志
---
日志内容必须脱敏。
"""


@pytest.mark.parametrize("content,status,issue", [
    (VALID, "READY", None),
    ("普通文字", "FAIL", "缺少 front matter"),
    (VALID.replace("name: problem\n", ""), "FAIL", "缺少 name"),
    (VALID.replace("workflow:\n  - 确认影响\n  - 查询日志\n", ""), "FAIL", "缺少 workflow"),
    (VALID.replace("name: problem", "name: other"), "WARN", "目录名和 Skill name 不一致"),
    (VALID.replace("description: 问题类告警取证\n", ""), "WARN", "缺少 description"),
    (VALID.replace("日志内容必须脱敏。", ""), "WARN", "缺少安全边界说明"),
])
def test_registry_reports_actionable_status(tmp_path, content, status, issue):
    folder = tmp_path / "problem"
    folder.mkdir()
    (folder / "SKILL.md").write_text(content, encoding="utf-8")
    item, = AlertSkillRegistry(tmp_path).status_items()
    assert item["status"] == status
    if issue:
        assert issue in item["issues"]
    else:
        assert item["alertTypes"] == ["PROBLEM"]
        assert item["workflow"] == ["确认影响", "查询日志"]
        assert item["issues"] == []


def test_empty_skill_directory_has_no_ready_skills(tmp_path):
    assert AlertSkillRegistry(tmp_path).status_items() == []
