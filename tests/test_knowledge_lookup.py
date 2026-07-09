from __future__ import annotations

from fault_diagnosis.domain.diagnosis.steps.knowledge_lookup import (
    build_knowledge_artifact,
    extract_fault_code_entries,
    extract_fault_codes_from_text,
)
from fault_diagnosis.platform.tools.kb_tools import query_fault_code_from_local_pdfs


def test_fault_code_query_uses_exact_local_pdf_lookup() -> None:
    result = query_fault_code_from_local_pdfs("查询故障代码F01002的触发原因，以及如何维修")

    assert "来源文件：S120_故障手册.pdf" in result
    assert "检索方式：故障码精确匹配" in result
    assert "F01002" in result
    assert "原因：" in result
    assert "出现了一个内部软件错误" in result
    assert "处理：" in result
    assert "重新为所有组件上电" in result


def test_fault_code_entry_parser_extracts_structured_manual_fields() -> None:
    result = query_fault_code_from_local_pdfs("A07089")
    entries = extract_fault_code_entries(result, requested_codes=["A07089"])

    assert len(entries) == 1
    entry = entries[0]
    assert entry.code == "A07089"
    assert entry.meaning == "转换单位后不能激活功能块"
    assert entry.cause == "尝试激活功能块。转换单位后不允许此操作。"
    assert entry.remedy == "将单位恢复到出厂设置。"
    assert entry.category == "参数设置 / 配置 / 调试过程出错 (18)"
    assert entry.drive_object == "所有目标"
    assert entry.component == "无"
    assert entry.propagation == "LOCAL"
    assert entry.reaction == "无"
    assert entry.acknowledgement == "无"
    assert entry.references == [
        "p0100 ( 标准 IEC/NEMA)",
        "p0349 ( 电机等效电路图数据单位制 )",
        "p0505 ( 单位制选择 )",
    ]
    assert entry.source_file == "S120_故障手册.pdf"
    assert entry.page == "232"
    assert entry.match_type == "exact_match"


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


def test_knowledge_artifact_contains_structured_fault_code_entries() -> None:
    raw_output = query_fault_code_from_local_pdfs("A07089")

    artifact = build_knowledge_artifact(
        "A07089",
        raw_output,
        fallback_error_message="知识检索未命中",
    )

    assert artifact.fault_codes == ["A07089"]
    assert artifact.fault_code_entries[0].code == "A07089"


def test_extract_fault_codes_from_sql_output_normalizes_suffixes() -> None:
    codes = extract_fault_codes_from_text("fault_code='F1030-0/0/0', alarm_code='0'; next F01002")

    assert codes == ["F1030", "F01002"]


def test_extract_fault_codes_ignores_g120_asset_model() -> None:
    codes = extract_fault_codes_from_text("device_name='G120电机1', alarm_code='A07089'")

    assert codes == ["A07089"]
