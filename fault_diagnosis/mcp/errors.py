"""MCP 协议层错误模型与异常包装。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from .schemas import McpErrorPayload


class McpErrorCode(str, Enum):
    """首批 MCP 协议错误码。"""

    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    DATA_NOT_FOUND = "DATA_NOT_FOUND"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class McpErrorResponse(BaseModel):
    """统一错误响应。"""

    model_config = ConfigDict(use_enum_values=True)

    error: McpErrorPayload


class McpProtocolError(Exception):
    """MCP 协议层可识别异常。"""

    def __init__(
        self,
        *,
        code: McpErrorCode,
        message: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        self.trace_id = trace_id
        self.run_id = run_id

    def to_payload(self) -> McpErrorPayload:
        """转换为标准错误负载。"""

        return McpErrorPayload(
            code=self.code.value,
            message=self.message,
            retryable=self.retryable,
            details=self.details,
            trace_id=self.trace_id,
            run_id=self.run_id,
        )

    def to_response(self) -> McpErrorResponse:
        """转换为标准错误响应。"""

        return McpErrorResponse(error=self.to_payload())

    @classmethod
    def from_validation_error(
        cls,
        error: ValidationError,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
    ) -> "McpProtocolError":
        """将 Pydantic 校验异常转换为协议异常。"""

        details = {
            "validation_errors": [
                {
                    "loc": ".".join(str(item) for item in issue.get("loc", [])),
                    "message": issue.get("msg", ""),
                    "type": issue.get("type", ""),
                }
                for issue in error.errors()
            ]
        }
        return cls(
            code=McpErrorCode.INVALID_ARGUMENT,
            message="请求参数校验失败",
            retryable=False,
            details=details,
            trace_id=trace_id,
            run_id=run_id,
        )


def build_error_response(
    *,
    code: McpErrorCode,
    message: str,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
) -> McpErrorResponse:
    """构造统一错误响应。"""

    return McpProtocolError(
        code=code,
        message=message,
        retryable=retryable,
        details=details,
        trace_id=trace_id,
        run_id=run_id,
    ).to_response()


def coerce_protocol_error(
    error: Exception,
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
) -> McpProtocolError:
    """将任意异常规范化为协议异常。"""

    if isinstance(error, McpProtocolError):
        return error
    if isinstance(error, ValidationError):
        return McpProtocolError.from_validation_error(
            error,
            trace_id=trace_id,
            run_id=run_id,
        )
    return McpProtocolError(
        code=McpErrorCode.INTERNAL_ERROR,
        message=str(error) or "内部执行失败",
        retryable=False,
        details={"exception_type": error.__class__.__name__},
        trace_id=trace_id,
        run_id=run_id,
    )
