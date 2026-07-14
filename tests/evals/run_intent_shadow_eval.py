#!/usr/bin/env python3
"""Score deterministic and optional real-model current-message intent parses."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from typing import Any

import yaml
from pydantic import ValidationError

from fault_diagnosis.agent.canonical_turn import CurrentUtteranceParser
from fault_diagnosis.agent.canonical_turn.clause_parser import DeterministicClauseParser
from fault_diagnosis.agent.canonical_turn.llm_structured_clause_model import build_intent_clause_model
from fault_diagnosis.agent.canonical_turn.model_clause_parser import ClauseModelRequest, ModelClauseParser
from fault_diagnosis.domain.canonical_turn import ALL_INTENT_CAPABILITIES
from tests.evals.intent_shadow_comparator import infer_shadow_metadata


CASES_PATH = Path(__file__).with_name("intent_shadow_cases.yaml")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("deterministic", "model", "compare"), default="compare")
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--concurrency", type=int, default=4)
    return parser.parse_args()


def _prediction(parsed, clauses, metadata) -> list[dict[str, Any]]:  # noqa: ANN001
    entities = {item.entity_id: item.value for item in parsed.entities}
    rows: list[dict[str, Any]] = []
    for clause, semantic in zip(clauses, metadata):
        refs = set(clause.action.entity_refs if clause.action else [])
        refs.update(clause.source.entity_refs if clause.source else [])
        for values in clause.slot.values():
            refs.update(values)
        rows.append({
            "capability": clause.action.capability if clause.action else None,
            "requested": semantic.requested,
            "negated": semantic.negated,
            "conditional": semantic.conditional,
            "sequence_index": semantic.sequence_index,
            "depends_on": semantic.depends_on,
            "source_kind": clause.source.source_kind if clause.source else "current_message",
            "entity_refs": sorted(entities[ref] for ref in refs if ref in entities),
            "boundary": [clause.start, clause.end],
        })
    return rows


def _deterministic(case: dict[str, Any]) -> dict[str, Any]:
    parsed = CurrentUtteranceParser().parse(case["user_message"])
    return {
        "status": "completed", "returned": True, "schema_valid": True, "validation_passed": True,
        "latency_ms": 0.0, "parsed": parsed,
        "clauses": _prediction(parsed, parsed.clauses, infer_shadow_metadata(parsed.clauses)),
    }


def _model(case: dict[str, Any], configured) -> dict[str, Any]:  # noqa: ANN001
    parsed = CurrentUtteranceParser().parse(case["user_message"])
    if configured is None:
        return {"status": "model_not_configured", "returned": False, "schema_valid": False,
                "validation_passed": False, "latency_ms": 0.0, "parsed": parsed, "clauses": []}
    model, _ = configured
    started = time.perf_counter()
    try:
        payload = model.parse(ClauseModelRequest(
            text=parsed.raw_text,
            deterministic_entities=tuple(item.model_dump(mode="json") for item in parsed.entities),
        ))
    except TimeoutError:
        status, returned, schema_valid = "model_timeout", False, False
    except (ValueError, TypeError):
        status, returned, schema_valid = "schema_invalid", False, False
    except Exception:
        status, returned, schema_valid = "model_error", False, False
    else:
        returned, schema_valid = True, True
        try:
            validation = ModelClauseParser().validate_for_shadow(
                parsed.raw_text, parsed.entities, payload,
                detect_action=DeterministicClauseParser().detect_action,
            )
        except ValidationError:
            status, schema_valid = "schema_invalid", False
        except Exception:
            status = "validation_failed"
        else:
            return {
                "status": "completed", "returned": True, "schema_valid": True, "validation_passed": True,
                "latency_ms": (time.perf_counter() - started) * 1000, "parsed": parsed,
                "clauses": _prediction(
                    parsed,
                    validation.clauses,
                    [clause.shadow_metadata for clause in validation.clauses],
                ),
            }
    return {
        "status": status, "returned": returned, "schema_valid": schema_valid,
        "validation_passed": False, "latency_ms": (time.perf_counter() - started) * 1000,
        "parsed": parsed, "clauses": [],
    }


def _score(cases: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    dimensions = ("negated", "conditional", "sequence_index", "depends_on", "source_kind")
    dimension_hits = Counter()
    dimension_totals = Counter()
    tp = fp = fn = entity_tp = entity_fp = entity_fn = exact = false_activations = 0
    for case, result in zip(cases, results):
        gold = case["expected"]["clauses"]
        predicted = result["clauses"]
        completed = result["status"] == "completed"
        gold_caps = Counter(item.get("capability") for item in gold if item.get("capability"))
        pred_caps = Counter(item.get("capability") for item in predicted if item.get("capability"))
        overlap = gold_caps & pred_caps
        tp += sum(overlap.values()); fp += sum((pred_caps - gold_caps).values()); fn += sum((gold_caps - pred_caps).values())
        case_exact = completed and len(gold) == len(predicted)
        for index, expected in enumerate(gold):
            actual = predicted[index] if index < len(predicted) else {}
            for key in ("capability", "requested", *dimensions):
                expected_value = expected.get(key, _default(key))
                dimension_totals[key] += 1
                if completed and actual.get(key, _default(key)) == expected_value:
                    dimension_hits[key] += 1
                else:
                    case_exact = False
            if "entity_refs" in expected:
                expected_refs = Counter(expected["entity_refs"])
                actual_refs = Counter(actual.get("entity_refs", []))
                refs_overlap = expected_refs & actual_refs
                entity_tp += sum(refs_overlap.values())
                entity_fp += sum((actual_refs - expected_refs).values())
                entity_fn += sum((expected_refs - actual_refs).values())
                case_exact &= expected_refs == actual_refs
        exact += int(case_exact)
        if "irrelevant" in case.get("tags", []) and any(item.get("requested") and item.get("capability") for item in predicted):
            false_activations += 1
    precision = _ratio(tp, tp + fp); recall = _ratio(tp, tp + fn)
    entity_precision = _ratio(entity_tp, entity_tp + entity_fp); entity_recall = _ratio(entity_tp, entity_tp + entity_fn)
    latencies = sorted(item["latency_ms"] for item in results if item["status"] == "completed")
    irrelevant_count = sum("irrelevant" in case.get("tags", []) for case in cases)
    return {
        "total_cases": len(cases),
        "returned_rate": _ratio(sum(item["returned"] for item in results), len(cases)),
        "schema_valid_rate": _ratio(sum(item["schema_valid"] for item in results), len(cases)),
        "validation_pass_rate": _ratio(sum(item["validation_passed"] for item in results), len(cases)),
        "exact_clause_accuracy": _ratio(exact, len(cases)),
        "capability_precision": precision, "capability_recall": recall,
        "capability_f1": _f1(precision, recall),
        "entity_reference_f1": _f1(entity_precision, entity_recall),
        "negation_accuracy": _ratio(dimension_hits["negated"], dimension_totals["negated"]),
        "condition_accuracy": _ratio(dimension_hits["conditional"], dimension_totals["conditional"]),
        "sequence_accuracy": _ratio(dimension_hits["sequence_index"], dimension_totals["sequence_index"]),
        "dependency_accuracy": _ratio(dimension_hits["depends_on"], dimension_totals["depends_on"]),
        "prior_result_relation_accuracy": _ratio(dimension_hits["source_kind"], dimension_totals["source_kind"]),
        "false_positive_activation_rate": _ratio(false_activations, irrelevant_count),
        "p50_latency_ms": _percentile(latencies, 0.50), "p95_latency_ms": _percentile(latencies, 0.95),
        "statuses": dict(Counter(item["status"] for item in results)),
    }


def _classify(cases, deterministic, model) -> tuple[dict[str, int], list[dict[str, str]]]:  # noqa: ANN001
    counts = Counter({name: 0 for name in (
        "model_correct_rule_wrong", "rule_correct_model_wrong", "both_correct_different_representation",
        "both_wrong", "gold_or_schema_ambiguous",
    )})
    examples = []
    for case, det, mdl in zip(cases, deterministic, model):
        if mdl["status"] != "completed":
            counts["gold_or_schema_ambiguous"] += 1
            continue
        if det["clauses"] == mdl["clauses"]:
            continue
        gold = case["expected"]["clauses"]
        det_ok = _semantic_equal(gold, det["clauses"])
        model_ok = _semantic_equal(gold, mdl["clauses"])
        if model_ok and not det_ok: category = "model_correct_rule_wrong"
        elif det_ok and not model_ok: category = "rule_correct_model_wrong"
        elif det_ok and model_ok: category = "both_correct_different_representation"
        else: category = "both_wrong"
        counts[category] += 1
        if len(examples) < 12:
            examples.append({"case_id": case["case_id"], "category": category, "user_message": case["user_message"]})
    return dict(counts), examples


def _semantic_equal(gold, predicted) -> bool:  # noqa: ANN001
    if len(gold) != len(predicted): return False
    return all(
        all(actual.get(key, _default(key)) == expected.get(key, _default(key)) for key in (
            "capability", "requested", "negated", "conditional", "sequence_index", "depends_on", "source_kind"
        ))
        for expected, actual in zip(gold, predicted)
    )


def _default(key: str) -> Any:
    return {"requested": True, "negated": False, "conditional": False, "sequence_index": None,
            "depends_on": [], "source_kind": "current_message", "capability": None}.get(key)


def _ratio(value: int | float, total: int | float) -> float:
    return round(value / total, 4) if total else 0.0


def _f1(precision: float, recall: float) -> float:
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values: return 0.0
    index = min(len(values) - 1, max(0, round((len(values) - 1) * fraction)))
    return round(values[index], 2)


def main() -> int:
    args = _args()
    cases = yaml.safe_load(args.cases.read_text(encoding="utf-8"))
    unknown_capabilities = sorted({
        clause["capability"]
        for case in cases
        for clause in case["expected"]["clauses"]
        if clause.get("capability") not in ALL_INTENT_CAPABILITIES
    })
    if unknown_capabilities:
        raise ValueError(f"Gold Dataset contains unknown capabilities: {unknown_capabilities}")
    deterministic = [_deterministic(case) for case in cases]
    configured = None
    configuration_error = ""
    if args.mode in {"model", "compare"}:
        try: configured = build_intent_clause_model()
        except Exception as exc: configuration_error = f"{type(exc).__name__}: {exc}"
    model = []
    if args.mode in {"model", "compare"}:
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            model = list(pool.map(lambda case: _model(case, configured), cases))
    report: dict[str, Any] = {
        "schema_version": "intent_shadow_eval.v1", "mode": args.mode,
        "dataset": str(args.cases), "case_count": len(cases),
        "model_eval_concurrency": max(1, args.concurrency),
        "deterministic_vs_gold": _score(cases, deterministic),
    }
    if model:
        report["model_vs_gold"] = _score(cases, model)
        report["model_configuration_error"] = configuration_error
        report["difference_classification"], report["typical_examples"] = _classify(cases, deterministic, model)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
