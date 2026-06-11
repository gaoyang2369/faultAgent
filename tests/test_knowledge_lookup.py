from __future__ import annotations

from fault_diagnosis.diagnosis.steps.knowledge_lookup import build_knowledge_artifact, extract_fault_codes_from_text
from fault_diagnosis.tools.kb_tools import query_fault_code_from_local_pdfs


def test_fault_code_query_uses_exact_local_pdf_lookup() -> None:
    result = query_fault_code_from_local_pdfs("查询故障代码F01002的触发原因，以及如何维修")

    assert "来源文件：S120_故障手册.pdf" in result
    assert "检索方式：故障码精确匹配" in result
    assert "F01002" in result
    assert "原因：" in result
    assert "出现了一个内部软件错误" in result
    assert "处理：" in result
    assert "重新为所有组件上电" in result


def test_fault_code_query_checks_multiple_codes_from_local_pdfs() -> None:
    result = query_fault_code_from_local_pdfs("同时诊断 F01002 和 F01003 的原因与处理")

    assert "故障码：F01002" in result
    assert "故障码：F01003" in result
    assert result.count("检索方式：故障码精确匹配") >= 2


def test_timeout_knowledge_output_is_not_successful() -> None:
    artifact = build_knowledge_artifact(
        "F01002",
        "超时：知识库检索超过 15s 未返回，请稍后重试或缩小查询范围。",
        fallback_error_message="知识检索未命中",
    )

    assert artifact.success is False
    assert artifact.error == "超时：知识库检索超过 15s 未返回，请稍后重试或缩小查询范围。"


def test_extract_fault_codes_from_sql_output_normalizes_suffixes() -> None:
    codes = extract_fault_codes_from_text("fault_code='F1030-0/0/0', alarm_code='0'; next F01002")

    assert codes == ["F1030", "F01002"]
