from __future__ import annotations

import os  # 环境变量
import re  # 正则
import sys  # 解释器路径
from contextlib import asynccontextmanager  # 异步上下文管理器
from typing import Any, AsyncIterator  # 类型标注

from app.core.config import Settings  # 全局配置


class McpToolError(RuntimeError):
    # MCP 工具调用统一异常
    pass


class EvoHarnessMcpToolClient:
    # MCP 工具客户端：通过 stdio 拉起本地 mcp_tools.server，封装告警工具调用
    def __init__(self, settings: Settings):
        self.settings = settings  # 全局配置

    async def handle_alert(self, alert_id: int, severity: str | None) -> list[str]:
        # 处理一条告警：写台账；高优先级（P0/P1）补充通知工具，返回所有工具结果
        try:
            async with self._session() as session:  # 建立 MCP 会话
                results = [  # 结果列表
                    await self._call_tool(session, "evo_alert_ledger_write", {"alert_id": alert_id}),  # 写台账
                ]
                if severity and severity.upper() in {"P0", "P1"}:  # 高优先级才通知
                    incident_id = self._extract_incident_id(results[-1])  # 从台账结果解析事件（存在才补充）
                    if incident_id is not None:  # 有事件则通知
                        results.append(await self._call_tool(session, "evo_alert_notify", {"alert_id": alert_id, "incident_id": incident_id}))
                return results  # 返回全部结果
        except McpToolError:  # 业务异常原样上抛
            raise
        except Exception as exc:  # 其他异常包装为 MCP 错误
            raise McpToolError(f"MCP 工具调用异常：{type(exc).__name__}: {exc}") from exc

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
        # 异步上下文：惰性导入 mcp 依赖并启动本地 stdio 服务器
        try:  # 导入 MCP 客户端依赖
            from mcp import ClientSession, StdioServerParameters  # 会话与服务器参数
            from mcp.client.stdio import stdio_client  # stdio 客户端
        except ImportError as exc:  # 缺依赖则报错
            raise McpToolError("缺少 mcp 依赖，无法通过 MCP 调用 EvoHarnessAlert 工具") from exc

        project_root = self.settings.project_root  # 项目根目录
        env = os.environ.copy()  # 拷贝环境变量
        python_path = env.get("PYTHONPATH")  # 原 PYTHONPATH
        env["PYTHONPATH"] = str(project_root) if not python_path else f"{project_root}{os.pathsep}{python_path}"  # 注入项目根

        server = StdioServerParameters(  # 服务器参数
            command=sys.executable,  # 用当前解释器
            args=["-m", "app.mcp_tools.server"],  # 启动本地工具服务
            env=env,  # 环境
            cwd=str(project_root),  # 工作目录
        )
        async with stdio_client(server) as (read_stream, write_stream):  # 建立 stdio 管道
            async with ClientSession(read_stream, write_stream) as session:  # 建客户端会话
                await session.initialize()  # 握手初始化
                yield session  # 交给调用方

    async def _call_tool(self, session: Any, name: str, arguments: dict[str, Any]) -> str:
        # 调用单个工具，失败则抛 McpToolError
        result = await session.call_tool(name, arguments=arguments)  # 发起调用
        message = self._result_message(result)  # 提取文本
        if getattr(result, "isError", False):  # 服务端标记错误则抛
            raise McpToolError(f"{name} 调用失败：{message}")
        return message  # 返回结果文本

    def _result_message(self, result: Any) -> str:
        # 从 MCP 结果对象提取字符串消息
        parts = []  # 文本片段
        for item in getattr(result, "content", []) or []:  # 遍历内容块
            text = getattr(item, "text", None)  # 取 text
            parts.append(text if text is not None else str(item))  # 文本或兜底字符串
        if parts:  # 有文本则拼接
            return "\n".join(parts)
        structured = getattr(result, "structuredContent", None)  # 结构化内容
        return str(structured if structured is not None else result)  # 兜底序列化

    @staticmethod
    def _extract_incident_id(message: str) -> int | None:
        # 从台账结果文本中解析 incidentId（存在才返回）
        match = re.search(r"incidentId=(\d+)", message)  # 匹配事件 ID
        return int(match.group(1)) if match else None  # 命中返回整数，否则 None