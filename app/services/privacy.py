import re
from typing import Any


class PrivacySanitizer:
    patterns = [
        re.compile(r"1[3-9]\d{9}"),
        re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
        re.compile(r"\b\d{17}[\dXx]\b"),
        re.compile(r"(?i)\b(?:bearer\s+)?[a-z0-9._%+-]*token[a-z0-9._%+-]*\s*[:=]\s*[^\s,;]+"),
        re.compile(r"(?i)\b(?:password|passwd|pwd|secret|access_key|accesskey|api_key|apikey)\s*[:=]\s*[^\s,;]+"),
        re.compile(r"(?i)\bAuthorization\s*[:=]\s*[^\s,;]+"),
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ]

    def sanitize(self, text: str) -> str:
        sanitized = text or ""
        for pattern in self.patterns:
            sanitized = pattern.sub("[已脱敏]", sanitized)
        return sanitized

    def sanitize_data(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.sanitize(value)
        if isinstance(value, dict):
            return {self.sanitize(str(key)): self.sanitize_data(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.sanitize_data(item) for item in value]
        return value
