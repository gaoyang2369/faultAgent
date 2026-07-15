"""对模型上下文约束做候选可见性与确定性校验。"""

from __future__ import annotations

from fault_diagnosis.agent.semantics.contracts import ContextSemanticProposal, SemanticFieldDecision
from fault_diagnosis.domain.canonical_turn import ContextCandidate
from fault_diagnosis.domain.security.assets import resolve_asset


class ContextProposalValidator:
    """只接受可由 ACL 已授权候选集解释的筛选约束。"""

    def validate(
        self,
        proposal: ContextSemanticProposal | None,
        candidates: list[ContextCandidate],
    ) -> tuple[ContextSemanticProposal | None, list[SemanticFieldDecision], str | None]:
        if proposal is None:
            return None, [], None
        decisions: list[SemanticFieldDecision] = []
        visible_assets = {asset for item in candidates for asset in item.asset_refs}
        include = self._assets(proposal.include_asset_refs, visible_assets, "include_asset_refs", decisions)
        exclude = self._assets(proposal.exclude_asset_refs, visible_assets, "exclude_asset_refs", decisions)
        if set(include).intersection(exclude):
            decisions.append(SemanticFieldDecision(
                field="context.asset_refs", decision="CLARIFY", value=None,
                reason_code="conflicting_context_asset_constraints",
            ))
        if proposal.temporal_relation == "ordinal" and (proposal.ordinal is None or proposal.ordinal > len(candidates)):
            decisions.append(SemanticFieldDecision(
                field="context.ordinal", decision="CLARIFY", value=None,
                reason_code="context_ordinal_out_of_range",
            ))
        if proposal.temporal_relation != "ordinal" and proposal.ordinal is not None:
            decisions.append(SemanticFieldDecision(
                field="context.ordinal", decision="REJECT", value=None,
                reason_code="ordinal_requires_ordinal_relation",
            ))
        if proposal.relation == "worse_device_from_previous_comparison" and not include:
            decisions.append(SemanticFieldDecision(
                field="context.relation", decision="CLARIFY", value=None,
                reason_code="comparison_role_requires_asset_constraint",
            ))
        if any(item.decision == "CLARIFY" for item in decisions):
            return None, decisions, next(item.reason_code for item in decisions if item.decision == "CLARIFY")
        normalized = proposal.model_copy(update={
            "include_asset_refs": include,
            "exclude_asset_refs": exclude,
            "ordinal": proposal.ordinal if proposal.temporal_relation == "ordinal" else None,
        })
        decisions.append(SemanticFieldDecision(
            field="context", decision="ACCEPT", value={
                "temporal_relation": normalized.temporal_relation,
                "include_asset_refs": normalized.include_asset_refs,
                "exclude_asset_refs": normalized.exclude_asset_refs,
                "freshness_intent": normalized.freshness_intent,
            }, reason_code="acl_projected_context_constraints_verified",
        ))
        return normalized, decisions, None

    @staticmethod
    def _assets(
        values: list[str],
        visible_assets: set[str],
        field: str,
        decisions: list[SemanticFieldDecision],
    ) -> list[str]:
        normalized: list[str] = []
        for value in values:
            record = resolve_asset(value)
            asset = record.display_name if record else ""
            if not asset or asset not in visible_assets:
                decisions.append(SemanticFieldDecision(
                    field=f"context.{field}", decision="CLARIFY", value=None,
                    reason_code="unknown_or_unavailable_context_asset",
                ))
                continue
            normalized.append(asset)
        return list(dict.fromkeys(normalized))
