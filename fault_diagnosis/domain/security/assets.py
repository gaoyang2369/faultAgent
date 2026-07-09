"""Asset registry and alias helpers for resource-scoped SQL access."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


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
        data_sources=[AssetDataSource(table="real_data_01")],
        system="DCMA_LINE_1",
        location="一号车间",
    ),
    AssetRecord(
        asset_id="g120_motor_2",
        display_name="G120电机2",
        aliases=["G120电机2", "J2号机", "2号机", "DCMA二号电机", "real_data_02"],
        data_sources=[AssetDataSource(table="real_data_02")],
        system="DCMA_LINE_1",
        location="一号车间",
    ),
    AssetRecord(
        asset_id="g120_motor_3",
        display_name="G120电机3",
        aliases=["G120电机3", "J3号机", "3号机", "DCMA三号电机", "real_data_03"],
        data_sources=[AssetDataSource(table="real_data_03")],
        system="DCMA_LINE_1",
        location="一号车间",
    ),
)


def _registry_path() -> Path:
    configured = os.getenv("ASSET_REGISTRY_PATH", "").strip()
    if configured:
        return Path(configured)
    repo_config = Path("config") / "asset_registry.json"
    if repo_config.exists():
        return repo_config
    return Path("trash/run") / "asset_registry.json"


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


def asset_has_data_source_for_table(asset: str | None, table_name: str) -> bool:
    record = resolve_asset(asset)
    if record is None:
        return False
    return any(source.table == table_name for source in record.data_sources)


def assets_have_data_source_for_table(table_name: str, assets: list[str]) -> bool:
    cleaned = [asset for asset in (str(value or "").strip() for value in assets) if asset]
    return bool(cleaned) and any(asset_has_data_source_for_table(asset, table_name) for asset in cleaned)


def assets_include_table_scoped_source(table_name: str, assets: list[str]) -> bool:
    cleaned = [asset for asset in (str(value or "").strip() for value in assets) if asset]
    for asset in cleaned:
        record = resolve_asset(asset)
        if record is None:
            continue
        if any(
            source.table == table_name and not source.device_name and not source.inverter_name
            for source in record.data_sources
        ):
            return True
    return False


def assets_are_table_scoped_for_table(table_name: str, assets: list[str]) -> bool:
    """Return true when the table itself is the configured asset boundary.

    A real_data data source without device_name/inverter_name means the shard is
    already scoped to that asset, so adding a row-level device predicate would
    be both redundant and, for real installations, often wrong.
    """

    cleaned = [asset for asset in (str(value or "").strip() for value in assets) if asset]
    if not cleaned:
        return False
    matched = False
    for asset in cleaned:
        record = resolve_asset(asset)
        if record is None:
            return False
        sources = [source for source in record.data_sources if source.table == table_name]
        if not sources:
            continue
        matched = True
        if any(source.device_name or source.inverter_name for source in sources):
            return False
    return matched


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
