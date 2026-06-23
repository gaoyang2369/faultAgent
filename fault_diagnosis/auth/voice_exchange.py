"""Voice signed assertion to browser cookie identity exchange helpers."""

from __future__ import annotations

from ..repositories.user_repository import FileUserRepository
from ..security.contracts import AuthContext
from ..security.permissions import build_auth_context
from ..security.voice_auth import VoiceNonceCache, verify_voice_signed_values


def resolve_voice_exchange_auth_context(
    *,
    user: str,
    role: str,
    timestamp: str | int,
    nonce: str,
    signature: str,
    user_repository: FileUserRepository | None = None,
    nonce_cache: VoiceNonceCache | None = None,
) -> AuthContext | None:
    """Validate a voice assertion and map it to a server-trusted user record."""

    voice_identity = verify_voice_signed_values(
        user=user,
        role=role,
        timestamp=timestamp,
        nonce=nonce,
        signature=signature,
        nonce_cache=nonce_cache,
    )
    if voice_identity is None:
        return None

    repository = user_repository or FileUserRepository()
    record = repository.find_by_voice_name(voice_identity.voice_name)
    if record is None or record.role != voice_identity.role:
        return None

    return build_auth_context(
        user_id=record.user_id,
        display_name=record.display_name or voice_identity.voice_name,
        role=record.role,
        asset_scope=record.asset_scope,
        table_scope=record.allowed_tables,
        system_scope=record.system_scope,
        location_scope=record.location_scope,
        kb_scopes=record.kb_scopes,
        auth_method="voice_exchange",
    )
