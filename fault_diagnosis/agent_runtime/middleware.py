"""中间件组装模块"""

from langchain.agents.middleware import TodoListMiddleware, SummarizationMiddleware

from ..prompts.dynamic_prompt import identity_aware_prompt
from ..config import MAX_TOKENS_BEFORE_SUMMARY, MESSAGES_TO_KEEP


def build_middleware(summary_model=None):
    """组装中间件列表，返回给 create_agent 使用"""
    middlewares = [
        TodoListMiddleware(),
        identity_aware_prompt,
    ]
    if summary_model is not None:
        middlewares.append(
            SummarizationMiddleware(
                model=summary_model,
                max_tokens_before_summary=MAX_TOKENS_BEFORE_SUMMARY,
                messages_to_keep=MESSAGES_TO_KEEP,
            )
        )
    return middlewares
