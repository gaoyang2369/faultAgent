"""一次/轮的异步语义编排；Wave 1 只输出安全观测和确定性 parse。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from pydantic import ValidationError

from fault_diagnosis.agent.semantics.contracts import SemanticCallTrace, SemanticResolution
from fault_diagnosis.agent.semantics.context_interpreter import project_context_semantic_input
from fault_diagnosis.agent.semantics.context_validator import ContextProposalValidator
from fault_diagnosis.agent.semantics.intent_canonicalizer import IntentCanonicalizer
from fault_diagnosis.agent.semantics.intent_interpreter import parse_semantic_turn_proposal
from fault_diagnosis.agent.semantics.model_request import SemanticModelRequest
from fault_diagnosis.agent.semantics.model_gateway import (
    AsyncModelGateway,
    SemanticModelCancelled,
    build_semantic_model_gateway,
)
from fault_diagnosis.domain.canonical_turn import CAPABILITY_SPECS
from fault_diagnosis.platform import settings


class SemanticResolutionService:
    """生产语义入口，模型异常只回退到已有确定性 parse。"""

    def __init__(
        self,
        *,
        parser,
        gateway_factory: Callable[[asyncio.Semaphore], AsyncModelGateway] | None = None,
        mode: str | None = None,
        concurrency: int | None = None,
    ) -> None:
        self._parser = parser
        self._gateway_factory = gateway_factory or (lambda semaphore: build_semantic_model_gateway(semaphore=semaphore))
        self._mode = mode or settings.LLM_SEMANTIC_MODE
        self._semaphore = asyncio.Semaphore(concurrency or settings.LLM_SEMANTIC_CONCURRENCY)
        self._gateway: AsyncModelGateway | None = None
        self._canonicalizer = IntentCanonicalizer()
        self._context_validator = ContextProposalValidator()

    async def resolve(
        self,
        raw_message: str,
        *,
        cancel_event: asyncio.Event | None = None,
        context_candidates=(),
        pending_summary: dict[str, Any] | None = None,
    ) -> SemanticResolution:
        parsed = self._parser.parse(raw_message)
        deterministic_capabilities = [item.action.capability for item in parsed.clauses if item.action]
        mode = self._mode if self._mode in {"off", "shadow", "primary"} else "off"
        if mode == "off":
            return SemanticResolution(parsed=parsed, trace=SemanticCallTrace(
                mode="off", deterministic_capabilities=deterministic_capabilities,
            ))
        trace = SemanticCallTrace(
            attempted=True,
            mode=mode,
            status="model_error",
            deterministic_capabilities=deterministic_capabilities,
        )
        started = time.perf_counter()
        try:
            gateway = self._gateway or self._gateway_factory(self._semaphore)
            self._gateway = gateway
            trace.model_name = gateway.model_name
            result = await gateway.invoke_clause_model(
                SemanticModelRequest(
                    text=parsed.raw_text,
                    deterministic_entities=tuple(item.model_dump(mode="json") for item in parsed.entities),
                    deterministic_clauses=tuple(_clause_summary(item) for item in parsed.clauses),
                    allowed_capabilities=tuple(sorted(CAPABILITY_SPECS)),
                    allowed_source_kinds=("prior_result", "current_message"),
                    response_schema="semantic_turn_proposal.v1",
                    schema_version="semantic_turn_request.v1",
                    context_candidates=project_context_semantic_input(list(context_candidates)),
                    pending_summary=pending_summary,
                ),
                cancel_event=cancel_event,
            )
            trace.latency_ms = result.latency_ms
            trace.input_tokens = result.input_tokens
            trace.output_tokens = result.output_tokens
            trace.concurrency_limited = result.concurrency_limited
        except SemanticModelCancelled:
            return _fallback(parsed, trace, "model_cancelled", started)
        except TimeoutError:
            return _fallback(parsed, trace, "model_timeout", started)
        except RuntimeError as exc:
            return _fallback(
                parsed, trace, "model_not_configured" if "not_configured" in str(exc) else "model_error", started
            )
        except (TypeError, ValueError):
            return _fallback(parsed, trace, "schema_invalid", started)
        except Exception:
            return _fallback(parsed, trace, "model_error", started)
        if mode in {"primary", "shadow"}:
            try:
                proposal = parse_semantic_turn_proposal(result.payload)
                canonical, decisions = self._canonicalizer.canonicalize(parsed, proposal)
                context_proposal, context_decisions, context_clarification_reason = self._context_validator.validate(
                    proposal.context, list(context_candidates)
                )
                decisions.extend(context_decisions)
            except ValidationError:
                return _fallback(parsed, trace, "schema_invalid", started)
            except (TypeError, ValueError):
                return _fallback(parsed, trace, "validation_failed", started)
            trace.status = "completed"
            trace.accepted = [item.field for item in decisions if item.decision == "ACCEPT"]
            trace.rejected = [item.field for item in decisions if item.decision == "REJECT"]
            trace.clarify = [item.field for item in decisions if item.decision == "CLARIFY"]
            trace.fallback = False
            trace.proposal_capabilities = [item.capability for item in proposal.clauses if item.capability]
            # Shadow 只观测同一次语义调用，绝不替换确定性 parse 或上下文绑定。
            return SemanticResolution(
                parsed=canonical if mode == "primary" else parsed,
                trace=trace,
                field_decisions=decisions,
                context_proposal=context_proposal if mode == "primary" else None,
                context_clarification_reason=context_clarification_reason if mode == "primary" else None,
            )


def _clause_summary(clause) -> dict:  # noqa: ANN001
    return {
        "text": clause.text,
        "capability": clause.action.capability if clause.action else None,
        "requested": clause.modality.requested,
        "negated": clause.modality.negated,
        "source_kind": clause.source.source_kind if clause.source else None,
    }


def _fallback(parsed, trace: SemanticCallTrace, status: str, started: float) -> SemanticResolution:  # noqa: ANN001
    trace.status = status
    trace.fallback = True
    trace.fallback_reason = status
    trace.latency_ms = (time.perf_counter() - started) * 1000
    return SemanticResolution(parsed=parsed, trace=trace)
