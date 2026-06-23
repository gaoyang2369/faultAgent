"""Asset registry and alias helpers for resource-scoped SQL access."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..common.paths import RUN_STATE_DIR


class AssetDataSource(BaseModel):
    table: str
    device_name: str | None = None
    inverter_name: str | None = None


class AssetRecord(BaseModel):
    asset_id: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    data_sources: list[AssetDataSource] = Field(default_factory=list)
    system: str = ""
    location: str = ""


DEFAULT_ASSET_REGISTRY: tuple[AssetRecord, ...] = (
    AssetRecord(
        asset_id="g120_motor_1",
        display_name="G120电机1",
        aliases=["G120电机1", "J1号机", "1号机", "DCMA一号电机", "real_data_01"],
        data_sources=[AssetDataSource(table="real_data_01", device_name="G120电机1")],
        system="DCMA_LINE_1",
        location="一号车间",
    ),
    AssetRecord(
        asset_id="g120_motor_2",
        display_name="G120电机2",
        aliases=["G120电机2", "J2号机", "2号机", "DCMA二号电机", "real_data_02"],
        data_sources=[AssetDataSource(table="real_data_02", device_name="G120电机2")],
        system="DCMA_LINE_1",
        location="一号车间",
    ),
    AssetRecord(
        asset_id="g120_motor_3",
        display_name="G120电机3",
        aliases=["G120电机3", "J3号机", "3号机", "DCMA三号电机", "real_data_03"],
        data_sources=[AssetDataSource(table="real_data_03", device_name="G120电机3")],
        system="DCMA_LINE_1",
        location="一号车间",
    ),
)


def _registry_path() -> Path:
    configured = os.getenv("ASSET_REGISTRY_PATH", "").strip()
    return Path(configured) if configured else Path(RUN_STATE_DIR) / "asset_registry.json"


def _scope_key(value: str) -> str:
    return "".join(str(value or "").casefold().split())


def _fallback_aliases(value: str) -> set[str]:
    aliases = {_scope_key(part) for part in str(value or "").replace("／", "/").split("/")}
    for alias in list(aliases):
        for suffix in ("号机", "设备"):
            if alias.endswith(suffix) and len(alias) > len(suffix):
                aliases.add(alias[: -len(suffix)])
    return {alias for alias in aliases if alias}


def _record_aliases(record: AssetRecord) -> set[str]:
    values = [
        record.asset_id,
        record.display_name,
        *record.aliases,
        *(source.device_name or "" for source in record.data_sources),
        *(source.inverter_name or "" for source in record.data_sources),
    ]
    aliases: set[str] = set()
    for value in values:
        aliases.update(_fallback_aliases(value))
    return aliases


def _parse_registry_payload(payload: Any) -> list[AssetRecord]:
    if isinstance(payload, dict):
        payload = payload.get("assets", [])
    if not isinstance(payload, list):
        return []
    records: list[AssetRecord] = []
    for item in payload:
        try:
            records.append(AssetRecord.model_validate(item))
        except Exception:
            continue
    return records


@lru_cache(maxsize=1)
def load_asset_registry() -> tuple[AssetRecord, ...]:
    path = _registry_path()
    if path.exists():
        try:
            records = _parse_registry_payload(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            records = []
        if records:
            return tuple(records)
    return DEFAULT_ASSET_REGISTRY


def resolve_asset(value: str | None) -> AssetRecord | None:
    aliases = _fallback_aliases(value or "")
    if not aliases:
        return None
    return next(
        (record for record in load_asset_registry() if aliases.intersection(_record_aliases(record))),
        None,
    )


def asset_aliases(value: str | None) -> set[str]:
    record = resolve_asset(value)
    if record is not None:
        return _record_aliases(record)
    return _fallback_aliases(value or "")


def asset_is_in_scope(asset: str, assigned_assets: list[str]) -> bool:
    requested_aliases = asset_aliases(asset)
    if not requested_aliases:
        return True
    return any(requested_aliases.intersection(asset_aliases(assigned)) for assigned in assigned_assets)


def select_asset_table(asset: str | None, *, allowed_tables: set[str]) -> str | None:
    record = resolve_asset(asset)
    if record is None:
        return None
    return next(
        (source.table for source in record.data_sources if source.table in allowed_tables),
        None,
    )


def data_source_terms_for_table(table_name: str, assets: list[str]) -> dict[str, list[str]]:
    """Return DB filter values for assets on one table.

    For real_data shards, the source device/inverter names are authoritative. For
    auxiliary tables, include stable ids and aliases because those schemas may
    store either device_id or a display name.
    """

    terms: dict[str, list[str]] = {"device_name": [], "inverter_name": [], "device_id": []}
    for asset in assets:
        record = resolve_asset(asset)
        if record is None:
            terms["device_name"].append(asset)
            terms["inverter_name"].append(asset)
            terms["device_id"].append(asset)
            continue

        if table_name.startswith("real_data_"):
            for source in record.data_sources:
                if source.table != table_name:
                    continue
                if source.device_name:
                    terms["device_name"].append(source.device_name)
                if source.inverter_name:
                    terms["inverter_name"].append(source.inverter_name)
        else:
            terms["device_id"].append(record.asset_id)
            terms["device_name"].extend([record.display_name, *record.aliases])

    return {
        key: list(dict.fromkeys(value.strip() for value in values if value and value.strip()))
        for key, values in terms.items()
    }


def real_data_filter_terms(asset: str | None, table_name: str) -> dict[str, list[str]]:
    return data_source_terms_for_table(table_name, [asset] if asset else [])
