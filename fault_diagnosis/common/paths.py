"""项目路径常量。"""

from __future__ import annotations

import os


PACKAGE_ROOT = os.path.dirname(os.path.dirname(__file__))
PROJECT_ROOT = os.path.dirname(PACKAGE_ROOT)
PROJECT_ENV_FILE = os.path.join(PROJECT_ROOT, ".env")
TRASH_DIR = os.path.join(PROJECT_ROOT, "trash")
RUN_STATE_DIR = os.path.join(TRASH_DIR, "run")
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "agent_fronted")
FRONTEND_PUBLIC_DIR = os.path.join(FRONTEND_DIR, "public")
IMAGES_DIR = os.path.join(FRONTEND_PUBLIC_DIR, "images")
REPORTS_DIR = os.path.join(RUN_STATE_DIR, "reports")
PDFS_DIR = os.path.join(PROJECT_ROOT, "pdfs")
