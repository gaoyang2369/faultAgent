"""系统提示词模板。"""

from .dcma_system_prompt import DCMA_SYSTEM_PROMPT


def get_identity_system_prompt(user_identity: str) -> str:
    """根据用户身份生成系统提示词"""
    if user_identity == "游客":
        return "当前用户是**游客用户**，可能对工业设备故障诊断领域不够熟悉。"
    else:
        return "当前用户是**管理员**，具有专业背景。"


systemprompt = DCMA_SYSTEM_PROMPT
