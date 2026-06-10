# DCMA 保护 / 机械臂剥离总结

## 已完成

1. 将机械臂专属能力迁入 `robot_arm/`，包括：
   - `sql_inter`
   - `extract_data`
   - `fig_inter`
   - `fault_explanation_tool`
   - 机械臂子 Agent 与 SHAP 提示词
2. `tools/__init__.py` 改为默认只注册 DCMA 与共享工具。
3. `prompts/system_prompt.py` 改为 DCMA 主提示词 + 可选机械臂追加片段。
4. 保留 `tools/data_tools.py`、`tools/subagent/*`、`tools/sql_tools.py` 的最小兼容层，避免历史导入立刻失效。
5. 修复 `LOCAL_DEV_MODE` SSE 参数缺失、`HTTPException` 被吞、HTML 报告脚本注入基础风险、机械臂子 Agent 的同步阻塞调用。
6. 前端默认模板与品牌文案收口到 DCMA。

## 验证结果

- 后端测试：`C:\miniconda3\envs\faultagent312\python.exe -m pytest -q`
  - 结果：`80 passed`
- 前端构建：`D:\nvm\v20.19.3\npm.cmd run build`
  - 结果：构建成功
  - 备注：产物仍提示 chunk size warning，但不是本次回归失败
- 应用导入：`C:\miniconda3\envs\faultagent312\python.exe -c "from app import app; print('app import ok')"`
  - 结果：成功

## 剩余说明

- 兼容层仍保留在 `tools/` 下，这是为了保护历史导入路径，不是主系统继续依赖机械臂模块。
- `ENABLE_ROBOT_ARM=false` 为默认值；只有显式打开时，主系统才会重新接入机械臂模块。
