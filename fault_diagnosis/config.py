"""故障诊断 Agent 系统的集中配置模块。

所有硬编码值集中于此。非敏感配置使用与原始硬编码一致的默认值，
敏感配置（数据库密码、API 密钥）仅从 .env 文件加载。
"""
import hashlib
import os
import secrets

from dotenv import dotenv_values, load_dotenv

from .common.paths import PROJECT_ENV_FILE, PROJECT_ROOT, RUN_STATE_DIR


_SESSION_SECRET_FROM_PROCESS = os.environ.get("SESSION_SECRET", "").strip()
load_dotenv(dotenv_path=PROJECT_ENV_FILE, override=False)
try:
    _PROJECT_DOTENV_VALUES = dotenv_values(PROJECT_ENV_FILE)
except OSError:
    _PROJECT_DOTENV_VALUES = {}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _resolve_project_path(path: str | None, default_value: str) -> str:
    target = path or default_value
    if os.path.isabs(target):
        return target
    return os.path.join(PROJECT_ROOT, target)


def _resolve_optional_project_path(path: str | None) -> str:
    target = (path or "").strip()
    if not target:
        return ""
    if os.path.isabs(target):
        return target
    return os.path.join(PROJECT_ROOT, target)


def _project_dotenv_value(name: str) -> str:
    raw = _PROJECT_DOTENV_VALUES.get(name, "")
    if raw is None:
        return ""
    return str(raw).strip()


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    value = os.getenv(name, default).strip().lower() or default
    return value if value in choices else default


def _secret_fingerprint(secret: str) -> str:
    if not secret:
        return ""
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _load_or_create_local_dev_session_secret(secret_file: str) -> tuple[str, str]:
    try:
        if os.path.exists(secret_file):
            with open(secret_file, "r", encoding="utf-8") as handle:
                existing_secret = handle.read().strip()
            if existing_secret:
                return existing_secret, "local_dev_file"

        os.makedirs(os.path.dirname(secret_file), exist_ok=True)
        generated_secret = secrets.token_urlsafe(48)
        with open(secret_file, "w", encoding="utf-8") as handle:
            handle.write(generated_secret)
        return generated_secret, "local_dev_file"
    except OSError:
        return "", "missing"


def _resolve_session_secret(is_production: bool, secret_file: str) -> tuple[str, bool, str]:
    dotenv_secret = os.getenv("SESSION_SECRET", "").strip()
    if _SESSION_SECRET_FROM_PROCESS:
        return _SESSION_SECRET_FROM_PROCESS, True, "environment"
    if dotenv_secret:
        return dotenv_secret, True, "dotenv"
    if is_production:
        return "", False, "missing"
    local_secret, source = _load_or_create_local_dev_session_secret(secret_file)
    return local_secret, False, source


# === Knowledge Base (Ollama + FAISS) ===
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://10.108.13.254:11434")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:8b")
FAISS_PATH = os.getenv("FAISS_PATH", "faiss_db")

# === Knowledge Base Build Parameters ===
KB_CHUNK_SIZE = int(os.getenv("KB_CHUNK_SIZE", "3000"))
KB_CHUNK_OVERLAP = int(os.getenv("KB_CHUNK_OVERLAP", "1000"))
KB_BATCH_SIZE = int(os.getenv("KB_BATCH_SIZE", "50"))
KB_QUERY_TIMEOUT_SECONDS = int(os.getenv("KB_QUERY_TIMEOUT_SECONDS", "15"))
KB_EMBED_TIMEOUT_SECONDS = int(os.getenv("KB_EMBED_TIMEOUT_SECONDS", "60"))
KB_BUILD_MAX_DOCUMENTS = int(os.getenv("KB_BUILD_MAX_DOCUMENTS", "0"))
KB_INCREMENTAL_BUILD = _env_bool("KB_INCREMENTAL_BUILD", False)
KB_EMBED_CACHE_PATH = os.getenv(
    "KB_EMBED_CACHE_PATH",
    os.path.join(FAISS_PATH, "embedding_cache.sqlite3"),
)

# === Agent Behavior ===
MAX_TOKENS_BEFORE_SUMMARY = int(os.getenv("MAX_TOKENS_BEFORE_SUMMARY", "64000"))
MESSAGES_TO_KEEP = int(os.getenv("MESSAGES_TO_KEEP", "20"))
RECURSION_LIMIT = int(os.getenv("RECURSION_LIMIT", "50"))
STREAM_HEARTBEAT_SECONDS = float(os.getenv("STREAM_HEARTBEAT_SECONDS", "15"))
MODEL_STREAM_FIRST_EVENT_TIMEOUT_SECONDS = float(os.getenv("MODEL_STREAM_FIRST_EVENT_TIMEOUT_SECONDS", "20"))
HEALTHCHECK_TIMEOUT_SECONDS = float(os.getenv("HEALTHCHECK_TIMEOUT_SECONDS", "5"))
ENABLE_WORKFLOW_V1 = _env_bool("ENABLE_WORKFLOW_V1", True)

# === Database ===
DCMA_DB_NAME = os.getenv("DCMA_DB_NAME", "dcma")
MYSQL_USER = (
    os.getenv("MYSQL_USER", "").strip()
    or _project_dotenv_value("USER")
    or "root"
)

# === External APIs ===
FAULT_API_URL = os.getenv("FAULT_API_URL", "http://10.108.13.250:8001/predict_reason")
TTS_SYNTHESIZE_URL = os.getenv("TTS_SYNTHESIZE_URL", "").strip()
TTS_SYNTHESIZE_TIMEOUT_SECONDS = float(os.getenv("TTS_SYNTHESIZE_TIMEOUT_SECONDS", "15"))
TTS_SYNTHESIZE_MAX_CHARS = max(1, int(os.getenv("TTS_SYNTHESIZE_MAX_CHARS", "500")))

# === Optional Domain Modules ===
ENABLE_ROBOT_ARM = _env_bool("ENABLE_ROBOT_ARM", False)

# === Runtime Environment ===
APP_ENV = os.getenv("APP_ENV", os.getenv("ENV", "development")).strip().lower() or "development"
IS_PRODUCTION = APP_ENV in {"prod", "production"}

# === Web / Session ===
DEFAULT_FRONTEND_ORIGINS = (
    ""
    if IS_PRODUCTION
    else "http://localhost:9005,http://127.0.0.1:9005,http://localhost:8000,http://127.0.0.1:8000"
)
FRONTEND_ORIGINS = _parse_origins(os.getenv("FRONTEND_ORIGINS", DEFAULT_FRONTEND_ORIGINS))
SESSION_SECRET_FILE = _resolve_project_path(
    os.getenv("SESSION_SECRET_FILE", "").strip(),
    os.path.join(RUN_STATE_DIR, "session_secret.txt"),
)
SESSION_SECRET, HAS_EXPLICIT_SESSION_SECRET, SESSION_SECRET_SOURCE = _resolve_session_secret(
    IS_PRODUCTION,
    SESSION_SECRET_FILE,
)
HAS_STABLE_SESSION_SECRET = bool(SESSION_SECRET)
SESSION_SECRET_FINGERPRINT = _secret_fingerprint(SESSION_SECRET)
SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", IS_PRODUCTION)
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax").strip().lower() or "lax"
if SESSION_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    SESSION_COOKIE_SAMESITE = "lax"
if SESSION_COOKIE_SAMESITE == "none":
    SESSION_COOKIE_SECURE = True
SESSION_COOKIE_DOMAIN = os.getenv("SESSION_COOKIE_DOMAIN", "").strip() or None
SESSION_COOKIE_PATH = os.getenv("SESSION_COOKIE_PATH", "/").strip() or "/"

# === Local Development ===
LOCAL_DEV_MODE = _env_bool("LOCAL_DEV_MODE", False)
IS_LOCAL_RUNTIME = LOCAL_DEV_MODE or APP_ENV in {"dev", "development", "local", "test"}

# === Admin Auth / PDF Upload ===
DEFAULT_ADMIN_USERNAME = "DCMA"
DEFAULT_ADMIN_PASSWORD = "707707"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", DEFAULT_ADMIN_USERNAME).strip() or DEFAULT_ADMIN_USERNAME
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)
ADMIN_PASSWORD_IS_DEFAULT = (
    ADMIN_USERNAME == DEFAULT_ADMIN_USERNAME
    and ADMIN_PASSWORD == DEFAULT_ADMIN_PASSWORD
)
ALLOW_DEFAULT_ADMIN_PASSWORD = _env_bool("ALLOW_DEFAULT_ADMIN_PASSWORD", True)
ADMIN_AUTH_MAX_AGE = int(os.getenv("ADMIN_AUTH_MAX_AGE", str(60 * 60 * 8)))
ADMIN_UPLOAD_DIR = _resolve_project_path(
    os.getenv("ADMIN_UPLOAD_DIR", "").strip(),
    os.path.join(RUN_STATE_DIR, "admin_uploads"),
)
ADMIN_PDF_MAX_FILE_SIZE = int(os.getenv("ADMIN_PDF_MAX_FILE_SIZE", str(50 * 1024 * 1024)))

# === PDF / OCR Lightweight Pipeline ===
PDF_TEXT_EXTRACT_BACKEND = _env_choice("PDF_TEXT_EXTRACT_BACKEND", "auto", {"auto", "pypdf_text"})
MEDICINE_OCR_BACKEND = _env_choice("MEDICINE_OCR_BACKEND", "auto", {"auto", "pypdf_text", "medicine_ocr_local"})
MEDICINE_OCR_ENABLE_HEAVY_MODEL = _env_bool("MEDICINE_OCR_ENABLE_HEAVY_MODEL", False)
MEDICINE_OCR_MODEL_DIR = _resolve_optional_project_path(os.getenv("MEDICINE_OCR_MODEL_DIR", ""))
MEDICINE_OCR_DEVICE = os.getenv("MEDICINE_OCR_DEVICE", "auto").strip().lower() or "auto"
MEDICINE_OCR_TIMEOUT_SECONDS = int(os.getenv("MEDICINE_OCR_TIMEOUT_SECONDS", "300"))
MEDICINE_OCR_MAX_PAGES = max(1, int(os.getenv("MEDICINE_OCR_MAX_PAGES", "1")))
MEDICINE_OCR_RENDER_DPI = max(72, int(os.getenv("MEDICINE_OCR_RENDER_DPI", "120")))
PDF_TEXT_MIN_CHARS = max(1, int(os.getenv("PDF_TEXT_MIN_CHARS", "100")))
PDF_TEXT_PREVIEW_CHARS = max(200, int(os.getenv("PDF_TEXT_PREVIEW_CHARS", "4000")))
UPLOADED_PDF_KB_ENABLE_VECTOR_INDEX = _env_bool("UPLOADED_PDF_KB_ENABLE_VECTOR_INDEX", False)
UPLOADED_PDF_KB_VECTOR_TIMEOUT_SECONDS = max(1, int(os.getenv("UPLOADED_PDF_KB_VECTOR_TIMEOUT_SECONDS", "8")))
