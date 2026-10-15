"""Enforces the layer import rules documented in docs/ARCHITECTURE.md.

domain          -> (nothing internal)
application     -> domain
infrastructure  -> domain, application
api             -> domain, application, infrastructure
ui              -> domain, application (presentation only)
"""

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "fedtone"
LAYERS = ("domain", "application", "infrastructure", "api", "ui")

ALLOWED: dict[str, set[str]] = {
    "domain": set(),
    "application": {"domain"},
    "infrastructure": {"domain", "application"},
    "api": {"domain", "application", "infrastructure"},
    "ui": {"domain", "application"},
}


def _imported_layers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    layers = set()
    for module in modules:
        parts = module.split(".")
        if parts[0] == "fedtone" and len(parts) > 1 and parts[1] in LAYERS:
            layers.add(parts[1])
    return layers


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_exists(layer: str) -> None:
    assert (PACKAGE_ROOT / layer / "__init__.py").is_file()


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_imports_respect_boundaries(layer: str) -> None:
    violations = []
    for path in (PACKAGE_ROOT / layer).rglob("*.py"):
        forbidden = _imported_layers(path) - ALLOWED[layer] - {layer}
        if forbidden:
            violations.append(f"{path.relative_to(PACKAGE_ROOT)} imports {sorted(forbidden)}")
    assert not violations, "\n".join(violations)


def test_boundary_checker_detects_violation(tmp_path: Path) -> None:
    sample = tmp_path / "bad.py"
    sample.write_text("from fedtone.infrastructure import db\nimport fedtone.api.app\n")
    assert _imported_layers(sample) == {"infrastructure", "api"}
