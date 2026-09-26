import re  # 正则表达式
from typing import Any  # 任意类型


class PrivacySanitizer:
    # 隐私脱敏器：把告警文本里的敏感信息替换为占位符
    patterns = [  # 待脱敏的敏感模式集合
        re.compile(r"1[3-9]\d{9}"),  # 手机号
        re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),  # 邮箱
        re.compile(r"\b\d{17}[\dXx]\b"),  # 身份证号
        re.compile(r"(?i)\b(?:bearer\s+)?[a-z0-9._%+-]*token[a-z0-9._%+-]*\s*[:=]\s*[^\s,;]+"),  # token
        re.compile(r"(?i)\b(?:password|passwd|pwd|secret|access_key|accesskey|api_key|apikey)\s*[:=]\s*[^\s,;]+"),  # 凭据键值
        re.compile(r"(?i)\bAuthorization\s*[:=]\s*[^\s,;]+"),  # Authorization 头
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),  # IPv4 地址
    ]

    def sanitize(self, text: str) -> str:
        sanitized = text or ""  # 空文本兼容
        for pattern in self.patterns:  # 依次替换每种敏感模式
            sanitized = pattern.sub("[已脱敏]", sanitized)
        return sanitized

    def sanitize_data(self, value: Any) -> Any:
        # 递归脱敏：支持字符串、字典、列表任意结构
        if isinstance(value, str):
            return self.sanitize(value)  # 字符串直接脱敏
        if isinstance(value, dict):
            return {self.sanitize(str(key)): self.sanitize_data(item) for key, item in value.items()}  # 字典递归脱敏键值
        if isinstance(value, list):
            return [self.sanitize_data(item) for item in value]  # 列表递归脱敏
        return value  # 其他类型原样返回