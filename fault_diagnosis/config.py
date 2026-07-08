"""Compatibility alias for ``fault_diagnosis.platform.settings``."""

from __future__ import annotations

import sys

from .platform import settings as _settings

sys.modules[__name__] = _settings
