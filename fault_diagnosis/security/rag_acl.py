"""Post-retrieval document filtering before knowledge enters model context."""

from __future__ import annotations

from typing import Any

from .contracts import AuthContext
from .permissions import KB_VISIBILITY_BY_ROLE
from .policy_engine import asset_is_in_scope


def _metadata(document: Any) -> dict[str, Any]:
    if isinstance(document, dict):
        nested = document.get("metadata")
        return {**document, **nested} if isinstance(nested, dict) else document
    value = getattr(document, "metadata", {})
    return value if isinstance(value, dict) else {}


def document_is_visible(document: Any, auth: AuthContext) -> bool:
    metadata = _metadata(document)
    source_type = str(metadata.get("source_type") or "knowledge_base")
    default_visibility = "internal" if source_type == "uploaded_pdf" else "public"
    visibility = str(metadata.get("visibility") or default_visibility).strip().lower()
    allowed_visibility = set(auth.kb_scopes or KB_VISIBILITY_BY_ROLE[auth.role])
    if visibility not in allowed_visibility:
        return False

    raw_roles = metadata.get("allowed_roles", []) or []
    if isinstance(raw_roles, str):
        raw_roles = [raw_roles]
    allowed_roles = [str(value).strip().lower() for value in raw_roles]
    if allowed_roles and auth.role not in allowed_roles:
        return False
    if auth.is_admin():
        return True
    raw_assets = metadata.get("allowed_asset_ids", []) or []
    if isinstance(raw_assets, str):
        raw_assets = [raw_assets]
    allowed_assets = [str(value).strip() for value in raw_assets]
    if allowed_assets and not any(asset_is_in_scope(asset, auth.asset_scope) for asset in allowed_assets):
        return False
    raw_systems = metadata.get("allowed_systems", []) or []
    if isinstance(raw_systems, str):
        raw_systems = [raw_systems]
    allowed_systems = {str(value).strip().casefold() for value in raw_systems}
    user_systems = {str(value).strip().casefold() for value in auth.system_scope}
    if allowed_systems and not allowed_systems.intersection(user_systems):
        return False
    return True


def filter_kb_documents(docs: list[Any], *, auth: AuthContext, decision: Any = None) -> list[Any]:
    del decision
    return [document for document in docs if document_is_visible(document, auth)]
