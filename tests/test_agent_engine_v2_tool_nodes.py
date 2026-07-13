from __future__ import annotations

from typing import Any

from fault_diagnosis.agent import ExecutionPlan, WorkflowRuntimeExecutor
from fault_diagnosis.domain.security.permissions import build_auth_context
from fault_diagnosis.domain.security.rag_acl import filter_kb_documents
from fault_diagnosis.domain.security.runtime_context import get_current_auth_context
from fault_diagnosis.platform.tools import report_tools
from fault_diagnosis.agent.runtime.tool_runtime import ToolRuntime


def _plan(nodes: list[dict[str, Any]], *, edges: list[dict[str, Any]] | None = None, approvals: list[dict[str, Any]] | None = None) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="plan.phase6.validated",
        plan_version="v2.phase6.validated",
        nodes=nodes,
        edges=edges or [],
        allowed_tools=["sql.read", "kb.search", "report.write_draft", "workorder.propose_draft"],
        required_evidence=["latest_runtime_status"],
        approval_requirements=approvals or [],
    )


def _runtime(fake: Any | None = None) -> WorkflowRuntimeExecutor:
    return WorkflowRuntimeExecutor(real_tools=True, tool_runtime=fake or FakeToolRuntime())


def _sample_sql_rows() -> list[tuple[Any, ...]]:
    return [
        (
            1,
            "2026-07-08 10:00:00",
            "G120电机1",
            "INV-J1",
            "2026-07-08",
            "10:00:00",
            "异常",
            "A07089",
            "",
            "0",
            "0",
            560.0,
            1000.0,
            700.0,
            12.0,
            10.0,
            8.0,
            32.0,
            72.0,
            66.0,
            11.0,
            1.0,
            1.0,
            100.0,
            68.0,
            82.0,
            81.0,
            50.0,
            12.0,
            2.0,
            "2026-07-08 10:00:00",
        )
    ]


class FakeToolRuntime:
    def __init__(self) -> None:
        self.sql_calls: list[tuple[str, Any]] = []
        self.kb_docs = [
            {"page_content": "公开手册：A07089 需要检查速度反馈。", "metadata": {"visibility": "public"}},
            {"page_content": "内部手册：检查变频器负载率。", "metadata": {"visibility": "internal"}},
            {"page_content": "受限手册：管理员专用处置。", "metadata": {"visibility": "restricted"}},
        ]

    def invoke_sql_tool(self, tool_name: str, payload: Any) -> Any:
        self.sql_calls.append((tool_name, payload))
        if tool_name == "sql_db_query_checker":
            return payload
        return _sample_sql_rows()

    def query_knowledge_base(self, query: str) -> str:
        auth = get_current_auth_context()
        visible = filter_kb_documents(self.kb_docs, auth=auth)
        return "\n\n".join(str(doc["page_content"]) for doc in visible)

    def save_report(self, *, report_filename: str, chart_payload: str | None = None, operation_report_payload: str = "") -> str:
        payload = {
            "report_filename": report_filename,
            "chart_payload": chart_payload,
            "operation_report_payload": operation_report_payload,
        }
        if hasattr(report_tools.save_report, "invoke"):
            return report_tools.save_report.invoke(payload)
        return report_tools.save_report(**payload)


def test_real_sql_node_applies_acl_and_executes_rewritten_read_query() -> None:
    fake = FakeToolRuntime()
    result = _runtime(fake).execute(
        _plan(
            [
                {
                    "node_id": "sql_1",
                    "node_type": "sql",
                    "inputs": {
                        "sql_query": "SELECT * FROM real_data_01",
                        "device_refs": ["J1号机"],
                        "use_checker": True,
                    },
                }
            ]
        ),
        auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
    )

    assert result.status == "completed"
    assert result.node_results[0].status == "completed"
    assert [name for name, _ in fake.sql_calls] == ["sql_db_query_checker", "sql_db_query"]
    executed_sql = fake.sql_calls[-1][1]
    assert executed_sql.startswith("SELECT")
    assert "real_data_01" in executed_sql
    assert "device_name IN ('G120电机1')" not in executed_sql
    assert "LIMIT 50" in executed_sql
    assert result.evidence_ledger.evidence_items


def test_real_sql_node_blocks_write_unauthorized_table_and_device_without_tool_call() -> None:
    for auth, query, inputs, expected_code in [
        (
            build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
            "DELETE FROM real_data_01",
            {"device_refs": ["J1号机"]},
            "sql_not_readonly",
        ),
        (
            build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["device_alarm"]),
            "SELECT * FROM real_data_01",
            {"device_refs": ["J1号机"]},
            "table_out_of_scope",
        ),
        (
            build_auth_context(role="engineer", asset_scope=["J2号机"], table_scope=["real_data_01"]),
            "SELECT * FROM real_data_01",
            {"device_refs": ["J1号机"]},
            "asset_out_of_scope",
        ),
    ]:
        fake = FakeToolRuntime()
        result = _runtime(fake).execute(
            _plan([{"node_id": "sql_1", "node_type": "sql", "inputs": {"sql_query": query, **inputs}}]),
            auth_context=auth,
        )

        assert result.status == "blocked"
        assert result.node_results[0].status == "blocked"
        assert result.node_results[0].error["code"] == expected_code
        assert fake.sql_calls == []
        assert result.evidence_ledger.evidence_items == []


def test_real_rag_node_uses_existing_visibility_filtering() -> None:
    fake = FakeToolRuntime()
    plan = _plan([{"node_id": "rag_1", "node_type": "rag", "inputs": {"query": "A07089"}}])

    guest = _runtime(fake).execute(plan, auth_context=build_auth_context(role="guest"))
    engineer = _runtime(fake).execute(plan, auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"]))
    admin = _runtime(fake).execute(plan, auth_context=build_auth_context(role="admin"))

    assert "公开手册" in guest.node_results[0].output["artifact"]["raw_output"]
    assert "内部手册" not in guest.node_results[0].output["artifact"]["raw_output"]
    assert "内部手册" in engineer.node_results[0].output["artifact"]["raw_output"]
    assert "受限手册" not in engineer.node_results[0].output["artifact"]["raw_output"]
    assert "受限手册" in admin.node_results[0].output["artifact"]["raw_output"]


def test_real_rag_node_timeout_is_not_usable_evidence() -> None:
    class TimeoutToolRuntime(FakeToolRuntime):
        def query_knowledge_base(self, query: str) -> str:
            return "超时：知识库检索超过 15s 未返回，请稍后重试或缩小查询范围。"

    result = _runtime(TimeoutToolRuntime()).execute(
        _plan(
            [
                {
                    "node_id": "rag_1",
                    "node_type": "rag",
                    "inputs": {
                        "query": "F01002 故障原因 触发条件 处理措施 检查步骤 复位方法 详细说明",
                        "fault_code_refs": ["F01002"],
                        "semantic_intent": "expand_previous_answer",
                        "requested_output_mode": "detailed",
                        "top_k": 5,
                    },
                }
            ]
        ),
        auth_context=build_auth_context(role="guest"),
    )

    node = result.node_results[0]
    artifact = node.output["artifact"]
    assert node.status == "completed"
    assert node.evidence_refs == []
    assert artifact["success"] is False
    assert artifact["hit_count"] == 0
    assert artifact["snippets"] == []
    assert artifact["error_code"] == "kb_timeout"
    assert result.evidence_ledger.evidence_items == []
    assert result.evidence_ledger.final_claim_ids == []
    assert "ev_kb_001" not in result.evidence_ledger.quality_checks.get("evidence_ids", [])
    assert result.output_frame.final_answer == "知识库检索超时，未获得可靠证据，请稍后重试或缩小查询范围。"


def test_tool_runtime_invokes_structured_kb_tool(monkeypatch) -> None:
    class StructuredKbTool:
        def __init__(self) -> None:
            self.payload = None

        def invoke(self, payload):  # noqa: ANN001
            self.payload = payload
            return f"hit:{payload['query']}"

    tool = StructuredKbTool()

    import fault_diagnosis.platform.tools.kb_tools as kb_tools

    monkeypatch.setattr(kb_tools, "query_knowledge_base", tool)

    result = ToolRuntime().query_knowledge_base("A07089")

    assert result == "hit:A07089"
    assert tool.payload == {"query": "A07089"}


def test_kg_node_is_skipped_and_does_not_add_evidence() -> None:
    result = _runtime().execute(_plan([{"node_id": "kg_1", "node_type": "kg"}]))

    assert result.status == "completed"
    assert result.node_results[0].status == "skipped"
    assert result.node_results[0].output["skipped_reason"] == "not_configured"
    assert result.evidence_ledger.evidence_items == []


def test_analysis_node_consumes_sql_and_rag_artifacts_and_commits_evidence_and_claims() -> None:
    result = _runtime().execute(
        _plan(
            [
                {"node_id": "sql_1", "node_type": "sql", "inputs": {"sql_query": "SELECT * FROM real_data_01", "device_refs": ["J1号机"]}},
                {"node_id": "rag_1", "node_type": "rag", "inputs": {"query": "A07089"}},
                {"node_id": "analysis_1", "node_type": "analysis", "inputs": {"device_refs": ["J1号机"]}},
            ],
            edges=[{"from": "sql_1", "to": "rag_1"}, {"from": "rag_1", "to": "analysis_1"}],
        ),
        auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
    )

    assert result.status == "completed"
    analysis = result.node_results[-1].output
    assert analysis["analysis_artifact"]["conclusion"]
    assert analysis["assessment"]["analyzer_id"] == "dcma_runtime"
    assert result.evidence_ledger.evidence_items
    assert result.evidence_ledger.claims


def test_report_node_writes_private_report_and_returns_reports_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(report_tools, "REPORTS_DIR", str(tmp_path))

    result = _runtime().execute(
        _plan(
            [
                {
                    "node_id": "report_1",
                    "node_type": "report",
                    "inputs": {
                        "report_filename": "phase6_report",
                        "operation_report_payload": '{"title":"V2 report","sections":[]}',
                    },
                }
            ]
        ),
        auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
    )

    assert result.status == "completed"
    artifact = result.node_results[0].output["artifact"]
    assert artifact["report_url"] == "/reports/phase6_report.html"
    assert (tmp_path / "phase6_report.html").exists()
    assert (tmp_path / "phase6_report.html.access.json").exists()


def test_workorder_node_generates_suggestion_or_draft_but_never_dispatches() -> None:
    result = _runtime().execute(
        _plan(
            [
                {"node_id": "sql_1", "node_type": "sql", "inputs": {"sql_query": "SELECT * FROM real_data_01", "device_refs": ["J1号机"]}},
                {"node_id": "analysis_1", "node_type": "analysis", "inputs": {"device_refs": ["J1号机"]}},
                {"node_id": "workorder_1", "node_type": "workorder", "inputs": {"device_refs": ["J1号机"]}},
            ],
            edges=[{"from": "sql_1", "to": "analysis_1"}, {"from": "analysis_1", "to": "workorder_1"}],
        ),
        trace_id="trace.workorder",
        thread_id="thread.workorder",
        auth_context=build_auth_context(role="engineer", asset_scope=["J1号机"], table_scope=["real_data_01"]),
    )

    assert result.status == "completed"
    output = result.node_results[-1].output
    assert output["suggestion"]["lifecycle_status"] in {"recommended_draft", "not_recommended"}
    if "draft" in output:
        assert output["draft"]["status"] in {"draft", "pending_verification"}
        assert output["draft"]["dispatch_policy"] == "dispatch_requires_external_approval"
    assert "dispatched" not in str(output).lower()


def test_workorder_dispatch_and_device_action_are_blocked() -> None:
    for inputs in ({"action_type": "dispatch_workorder"}, {"action_type": "device_action"}):
        result = _runtime().execute(_plan([{"node_id": "workorder_1", "node_type": "workorder", "inputs": inputs}]))

        assert result.status == "blocked"
        assert result.node_results[0].status == "blocked"
        assert result.node_results[0].output["allowed_next_step"] == "deny"


def test_approval_node_returns_pending_for_draft_only_and_blocks_dangerous_action() -> None:
    workorder_result = _runtime().execute(
        _plan(
            [{"node_id": "approval_1", "node_type": "approval"}],
            approvals=[{"type": "workorder_draft", "required": True}],
        )
    )
    device_result = _runtime().execute(
        _plan(
            [{"node_id": "approval_1", "node_type": "approval"}],
            approvals=[{"type": "device_control.write", "required": True}],
        )
    )

    assert workorder_result.status == "completed"
    assert workorder_result.node_results[0].output["status"] == "pending_manual_confirmation"
    assert workorder_result.node_results[0].output["dispatch_performed"] is False
    assert workorder_result.node_results[0].output["interrupts"][0]["allowed_next_step"] == "draft_only"
    assert device_result.status == "blocked"
    assert device_result.node_results[0].output["interrupts"][0]["allowed_next_step"] == "deny"


def test_approval_requirement_not_lost_when_node_input_is_empty_list() -> None:
    result = _runtime().execute(
        _plan(
            [{"node_id": "approval_1", "node_type": "approval", "inputs": {"approval_requirements": []}}],
            approvals=[
                {
                    "requirement_id": "approval_workorder_draft",
                    "type": "workorder_draft",
                    "required": True,
                    "allowed_next_step": "draft_only",
                }
            ],
        )
    )

    assert result.status == "completed"
    output = result.node_results[0].output
    assert output["status"] == "pending_manual_confirmation"
    assert output["required"] is True
    assert output["approval_requirements"][0]["requirement_id"] == "approval_workorder_draft"
    assert output["approval_requirements"][0]["required"] is True
    assert output["interrupts"][0]["allowed_next_step"] == "draft_only"


def test_failed_blocked_and_skipped_tool_nodes_do_not_pollute_evidence_ledger() -> None:
    missing_sql = _runtime().execute(_plan([{"node_id": "sql_1", "node_type": "sql"}]))
    blocked_workorder = _runtime().execute(_plan([{"node_id": "workorder_1", "node_type": "workorder", "inputs": {"action_type": "dispatch_workorder"}}]))
    skipped_kg = _runtime().execute(_plan([{"node_id": "kg_1", "node_type": "kg"}]))

    assert missing_sql.status == "failed"
    assert blocked_workorder.status == "blocked"
    assert skipped_kg.status == "completed"
    assert missing_sql.evidence_ledger.evidence_items == []
    assert blocked_workorder.evidence_ledger.evidence_items == []
    assert skipped_kg.evidence_ledger.evidence_items == []
