"""Check package-layer import boundaries after the fault_diagnosis reorganization."""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "fault_diagnosis"
LAYER_PREFIXES = {
    "server": "fault_diagnosis.server",
    "agent": "fault_diagnosis.agent",
    "domain": "fault_diagnosis.domain",
    "platform": "fault_diagnosis.platform",
    "shared": "fault_diagnosis.shared",
}
FORBIDDEN_IMPORTS = {
    "agent": ("fault_diagnosis.server",),
    "domain": ("fault_diagnosis.server", "fault_diagnosis.agent"),
    "platform": ("fault_diagnosis.server", "fault_diagnosis.agent"),
    "shared": ("fault_diagnosis.server", "fault_diagnosis.agent", "fault_diagnosis.domain", "fault_diagnosis.platform"),
}


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    source_layer: str
    imported: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line,
            "source_layer": self.source_layer,
            "imported": self.imported,
            "reason": self.reason,
        }


def run_check(root: Path = ROOT) -> dict[str, object]:
    forbidden: list[Hit] = []
    domain_platform: list[Hit] = []
    for path in _iter_python_files(root):
        source_layer = _source_layer(path, root)
        if not source_layer:
            continue
        for line, imported in _iter_imports(path):
            if source_layer == "domain" and imported.startswith("fault_diagnosis.platform"):
                domain_platform.append(
                    Hit(
                        path=path.relative_to(root).as_posix(),
                        line=line,
                        source_layer=source_layer,
                        imported=imported,
                        reason="domain_platform_dependency",
                    )
                )
            for prefix in FORBIDDEN_IMPORTS.get(source_layer, ()):
                if imported == prefix or imported.startswith(prefix + "."):
                    forbidden.append(
                        Hit(
                            path=path.relative_to(root).as_posix(),
                            line=line,
                            source_layer=source_layer,
                            imported=imported,
                            reason=f"{source_layer}_must_not_import_{prefix.removeprefix('fault_diagnosis.')}",
                        )
                    )
    return {
        "schema_version": "package_boundary_check.v1",
        "summary": {
            "forbidden_hits": len(forbidden),
            "domain_platform_dependency_hits": len(domain_platform),
        },
        "forbidden_hits": [hit.to_dict() for hit in forbidden],
        "domain_platform_dependency_hits": [hit.to_dict() for hit in domain_platform],
    }


def main() -> int:
    payload = run_check(ROOT)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    summary = payload["summary"]
    return 1 if summary["forbidden_hits"] or summary["domain_platform_dependency_hits"] else 0


def _iter_python_files(root: Path) -> Iterable[Path]:
    if not PACKAGE_ROOT.exists():
        return []
    return (
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _source_layer(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root).parts
    except ValueError:
        return ""
    if len(rel) < 2 or rel[0] != "fault_diagnosis":
        return ""
    return rel[1] if rel[1] in LAYER_PREFIXES else ""


def _iter_imports(path: Path) -> Iterable[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    imports: list[tuple[int, str]] = []
    package = _module_name(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_from_import(package, node.module or "", node.level)
            imports.append((node.lineno, module))
    return imports


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_from_import(package: str, module: str, level: int) -> str:
    if level <= 0:
        return module
    parts = package.split(".")
    base = parts[: max(len(parts) - level, 0)]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


if __name__ == "__main__":
    sys.exit(main())
