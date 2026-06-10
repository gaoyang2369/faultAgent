"""动态提示词中间件"""

from dataclasses import dataclass

from langchain.agents.middleware import dynamic_prompt, ModelRequest

from .system_prompt import systemprompt, get_identity_system_prompt

EVIDENCE_GUARDRAIL_APPENDIX = """

## 证据护栏

当你生成诊断、原因解释或报告草稿时，必须遵守以下规则：

1. 每个具体结论都要基于本轮已经查询到的数据、知识库片段或工具输出。
2. 证据不足、间接相关或覆盖不完整时，要把结论降级为“可能原因 / 待确认假设”，不能说成已确认根因。
3. 不要把缺少 SQL 数据或缺少现场证据的判断包装成确定结论。
4. 进入报告式表达前，优先按“结论 -> 证据 -> 不确定性 -> 下一步动作”的顺序组织内容。
5. 关键证据缺失时，要明确说明缺什么，并指出下一步应该执行哪个查询、检索或工具调用。
"""


@dataclass
class Context:
    """上下文数据类，用于动态提示词"""
    user_identity: str  # 用户身份：游客/管理员


@dynamic_prompt
def identity_aware_prompt(request: ModelRequest) -> str:
    """
    根据用户身份和部署环境动态调整系统提示词
    """
    user_identity = request.runtime.context.user_identity

    role = get_identity_system_prompt(user_identity)
    base = role + systemprompt

    return f"{base}\n\n{EVIDENCE_GUARDRAIL_APPENDIX}"
