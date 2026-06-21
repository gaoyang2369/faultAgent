"""Identity and authorization primitives for the diagnosis service."""

from .contracts import AuthContext, AuthorizationDecision, ResourceScope, SqlAclResult
from .permissions import build_auth_context

__all__ = [
    "AuthContext",
    "AuthorizationDecision",
    "ResourceScope",
    "SqlAclResult",
    "build_auth_context",
]
