"""持久化 repository 入口。"""

from .admin_file_repository import FileAdminKnowledgeFileRepository, get_admin_file_repository
from .governance_repository import FileGovernanceRepository, sanitize_governance_thread_hint
from .history_index import (
    FileHistoryIndexRepository,
    MemoryHistoryIndexRepository,
    configure_history_index_repository,
    get_history_index_repository,
    record_history_thread,
    remove_history_thread,
)

__all__ = [
    "FileAdminKnowledgeFileRepository",
    "FileGovernanceRepository",
    "FileHistoryIndexRepository",
    "MemoryHistoryIndexRepository",
    "configure_history_index_repository",
    "get_admin_file_repository",
    "sanitize_governance_thread_hint",
    "get_history_index_repository",
    "record_history_thread",
    "remove_history_thread",
]

