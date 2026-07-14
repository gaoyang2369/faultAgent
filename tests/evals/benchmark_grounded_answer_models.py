"""Layered real-provider benchmark for fixed Grounded Answer models.

This evaluation module is never imported by production code. It uses the production
prompt, packet builder, validator and one-call synthesizer, with controlled runtime
fixtures from the Phase 1.5 acceptance suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

from fault_diagnosis.agent.output import GroundedAnswerSynthesizer, GroundedAnswerValidator, build_answer_source_packet
from fault_diagnosis.agent.output.grounded_answer import ANSWER_SYNTHESIS_SYSTEM_PROMPT
from fault_diagnosis.platform.model_catalog import resolve_answer_model_name
from fault_diagnosis.server.bootstrap.app_models import build_answer_model
from tests.evals.run_grounded_answer_real_model_eval import Case, _cases


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[max(0, min(len(ordered) - 1, int(len(ordered) * percentile) - 1))], 1)


def _response_text(response: Any) -> str:
    content = getattr(response, "content", "")
    return content if isinstance(content, str) else ""


def _response_metrics(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage_metadata", {}) or {}
    details = usage.get("output_token_details") or {}
    metadata = getattr(response, "response_metadata", {}) or {}
    return {
        "provider_trace_id": str(getattr(response, "id", "") or metadata.get("id") or "")[:200],
        "prompt_tokens": int(usage.get("input_tokens") or 0),
        "completion_tokens": int(usage.get("output_tokens") or 0),
        "reasoning_tokens": int(details.get("reasoning") or details.get("reasoning_tokens") or 0),
        "finish_reason": str(metadata.get("finish_reason") or ""),
        "response_length": len(_response_text(response)),
    }


def _packet(case: Case, *, include_fallback: bool) -> Any:
    return build_answer_source_packet(
        user_message=case.message,
        deterministic_answer=case.deterministic_answer if include_fallback else "",
        deliverables=case.deliverables,
        evidence_bundle=case.bundle,
        runtime_metadata={"overall_status": case.overall_status, "status": case.runtime_status},
        auth_safe_context={},
    )


def _scaled_packet_json(case: Case, target_chars: int) -> str:
    packet = _packet(case, include_fallback=False)
    payload = packet.model_dump(mode="json")
    if target_chars > 2000:
        payload["evidence"] = [
            {
                "evidence_id": f"benchmark_ev_{index}",
                "source_type": "benchmark",
                "summary": "设备状态证据摘要。" * 20,
                "freshness": {"quality": "current"},
            }
            for index in range(max(1, target_chars // 220))
        ]
    rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    while len(rendered) > target_chars and payload.get("evidence"):
        payload["evidence"].pop()
        rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return rendered


async def _raw_call(
    *,
    model_name: str,
    json_mode: bool,
    timeout_seconds: float,
    messages: list[dict[str, str]],
) -> tuple[Any | None, dict[str, Any]]:
    model = build_answer_model(model_name, json_mode, timeout_seconds, 512)
    started = time.monotonic()
    try:
        async with asyncio.timeout(timeout_seconds):
            response = await model.ainvoke(messages)
    except TimeoutError:
        return None, {"provider_returned": False, "timeout": True, "error": "model_timeout", "latency_ms": round((time.monotonic() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001 - benchmark records safe error classes only.
        return None, {"provider_returned": False, "timeout": False, "error": type(exc).__name__, "latency_ms": round((time.monotonic() - started) * 1000, 1)}
    metrics = _response_metrics(response)
    metrics.update(
        provider_returned=True,
        timeout=False,
        error="",
        latency_ms=round((time.monotonic() - started) * 1000, 1),
        time_to_response_headers_ms=0.0,
        time_to_first_token_ms=0.0,
    )
    return response, metrics


async def _basic_probe(model_name: str, *, json_mode: bool, timeout: float, index: int) -> dict[str, Any]:
    response, metrics = await _raw_call(
        model_name=model_name,
        json_mode=json_mode,
        timeout_seconds=timeout,
        messages=[{"role": "user", "content": '只返回 JSON：{"ok":true}'}],
    )
    schema_valid = False
    if response is not None:
        try:
            schema_valid = json.loads(_response_text(response)) == {"ok": True}
        except json.JSONDecodeError:
            pass
    return {"model": model_name, "layer": "A", "case": f"basic_{index}", "json_mode": json_mode, "schema_valid": schema_valid, **metrics}


async def _packet_probe(
    model_name: str,
    *,
    timeout: float,
    case: Case,
    index: int,
    include_fallback: bool,
    target_chars: int | None = None,
    json_mode: bool = True,
) -> dict[str, Any]:
    packet = _packet(case, include_fallback=include_fallback)
    packet_json = _scaled_packet_json(case, target_chars) if target_chars else packet.model_dump_json(exclude_none=True)
    response, metrics = await _raw_call(
        model_name=model_name,
        json_mode=json_mode,
        timeout_seconds=timeout,
        messages=[
            {"role": "system", "content": ANSWER_SYNTHESIS_SYSTEM_PROMPT},
            {"role": "user", "content": f"Answer Source Packet:\n{packet_json}"},
        ],
    )
    validation_packet = type(packet).model_validate_json(packet_json)
    validation = GroundedAnswerValidator().validate(_response_text(response), source_packet=validation_packet) if response is not None else None
    return {
        "model": model_name,
        "layer": "B" if target_chars is None else "C",
        "case": f"{case.name}_{index}",
        "json_mode": json_mode,
        "include_fallback": include_fallback,
        "packet_chars": len(packet_json),
        "schema_valid": bool(validation and validation.schema_valid),
        "answer_validated": bool(validation and validation.valid),
        **metrics,
    }


async def _full_case(model_name: str, *, timeout: float, case: Case, repeat: int, include_fallback: bool, json_mode: bool) -> dict[str, Any]:
    model = build_answer_model(model_name, json_mode, timeout, 512)
    synth = GroundedAnswerSynthesizer(
        model=model,
        model_name=model_name,
        model_source="injected",
        enabled=True,
        timeout_seconds=timeout,
        include_fallback_in_packet=include_fallback,
    )
    result = await synth.synthesize(
        user_message=case.message,
        deterministic_answer=case.deterministic_answer,
        deliverables=case.deliverables,
        evidence_bundle=case.bundle,
        runtime_metadata={"overall_status": case.overall_status, "status": case.runtime_status},
        auth_safe_context={},
    )
    return {
        "model": model_name,
        "layer": "D",
        "case": case.name,
        "repeat": repeat,
        "json_mode": json_mode,
        "include_fallback": include_fallback,
        "deterministic_answer": case.deterministic_answer,
        "model_answer": result.answer if result.synthesis_status == "generated" else "",
        **result.audit_summary(),
    }


async def _bounded(tasks: list[Any], parallel: int) -> list[dict[str, Any]]:
    gate = asyncio.Semaphore(max(1, parallel))

    async def run(task: Any) -> dict[str, Any]:
        async with gate:
            return await task

    return await asyncio.gather(*(run(task) for task in tasks))


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    returned = sum(bool(item.get("provider_returned")) for item in records)
    schema = sum(bool(item.get("schema_valid")) for item in records)
    validated = sum(bool(item.get("answer_validated")) for item in records)
    fallback = sum(bool(item.get("fallback_used")) for item in records)
    timeouts = sum(bool(item.get("timeout")) or item.get("synthesis_status") == "model_timeout" for item in records)
    latencies = [float(item.get("model_total_latency_ms") or item.get("latency_ms") or 0) for item in records]
    prompt_tokens = [int(item.get("prompt_token_count") or item.get("prompt_tokens") or 0) for item in records if item.get("provider_returned")]
    completion_tokens = [int(item.get("completion_token_count") or item.get("completion_tokens") or 0) for item in records if item.get("provider_returned")]
    reasoning_tokens = [int(item.get("reasoning_token_count") or item.get("reasoning_tokens") or 0) for item in records if item.get("provider_returned")]
    return {
        "total": total,
        "provider_return_rate": round(returned / total, 4) if total else 0,
        "schema_success_rate_returned": round(schema / returned, 4) if returned else 0,
        "validator_pass_rate_schema": round(validated / schema, 4) if schema else 0,
        "generated_rate": round(validated / total, 4) if total else 0,
        "fallback_rate": round(fallback / total, 4) if total else 0,
        "timeout_rate": round(timeouts / total, 4) if total else 0,
        "p50_ms": _percentile(latencies, 0.5),
        "p95_ms": _percentile(latencies, 0.95),
        "average_prompt_tokens": round(statistics.fmean(prompt_tokens), 1) if prompt_tokens else 0,
        "average_completion_tokens": round(statistics.fmean(completion_tokens), 1) if completion_tokens else 0,
        "average_reasoning_tokens": round(statistics.fmean(reasoning_tokens), 1) if reasoning_tokens else 0,
    }


async def _main(args: argparse.Namespace) -> int:
    models = [resolve_answer_model_name(value) for value in args.models]
    cases = _cases()
    tasks: list[Any] = []
    for model in models:
        if args.stage == "screening":
            tasks.extend(_basic_probe(model, json_mode=True, timeout=args.timeout, index=index) for index in range(5))
            tasks.extend(
                _packet_probe(
                    model,
                    timeout=args.timeout,
                    case=cases[1],
                    index=index,
                    include_fallback=args.include_fallback,
                )
                for index in range(5)
            )
        elif args.stage == "probe":
            tasks.extend(_basic_probe(model, json_mode=mode, timeout=args.timeout, index=index) for mode in (False, True) for index in range(3))
            tasks.extend(
                _packet_probe(
                    model,
                    timeout=args.timeout,
                    case=cases[1],
                    index=index,
                    include_fallback=False,
                    json_mode=mode,
                )
                for index, mode in enumerate((False, True), 1)
            )
            tasks.extend(_packet_probe(model, timeout=args.timeout, case=cases[1], index=index, include_fallback=False, target_chars=size) for index, size in enumerate((1500, 4000, 11000), 1))
            tasks.extend(_packet_probe(model, timeout=args.timeout, case=cases[14], index=index, include_fallback=include) for index, include in enumerate((True, False), 1))
        elif args.stage == "ab":
            tasks.extend(
                _packet_probe(
                    model,
                    timeout=args.timeout,
                    case=cases[1],
                    index=index,
                    include_fallback=False,
                    json_mode=mode,
                )
                for index, mode in enumerate((False, True), 1)
            )
            tasks.extend(
                _packet_probe(
                    model,
                    timeout=args.timeout,
                    case=cases[1],
                    index=index,
                    include_fallback=include,
                    json_mode=True,
                )
                for index, include in enumerate((True, False), 1)
            )
        elif args.stage == "diagnostic":
            for repeat in range(1, args.repeats + 1):
                tasks.extend(
                    _full_case(
                        model,
                        timeout=args.diagnostic_timeout,
                        case=case,
                        repeat=repeat,
                        include_fallback=args.include_fallback,
                        json_mode=True,
                    )
                    for case in [cases[index] for index in args.diagnostic_case_indices]
                )
        elif args.stage in {"full", "stability"}:
            for repeat in range(1, args.repeats + 1):
                tasks.extend(_full_case(model, timeout=args.timeout, case=case, repeat=repeat, include_fallback=args.include_fallback, json_mode=args.json_mode) for case in cases)
    records = await _bounded(tasks, args.parallel)
    by_model = {model: _summary([item for item in records if item["model"] == model]) for model in models}
    payload = {"stage": args.stage, "parallel": args.parallel, "models": by_model, "records": records}
    output = Path(args.output or f"trash/run/grounded-answer-{args.stage}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"stage": args.stage, "models": by_model}, ensure_ascii=False))
    print(f"results={output}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--stage", choices=("screening", "probe", "ab", "diagnostic", "full", "stability"), required=True)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--diagnostic-timeout", type=float, default=60.0)
    parser.add_argument("--diagnostic-case-indices", nargs="+", type=int, default=[0, 2, 6, 14, 17])
    parser.add_argument("--json-mode", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-fallback", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output", default="")
    raise SystemExit(asyncio.run(_main(parser.parse_args())))
