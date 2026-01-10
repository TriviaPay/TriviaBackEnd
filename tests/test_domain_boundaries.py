import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOMAIN_CONFIGS = {
    "auth": {
        "paths": [ROOT / "routers" / "auth"],
        "allowed_prefixes": ["routers.auth", "routers.dependencies"],
    },
    "trivia": {
        "paths": [ROOT / "routers" / "trivia"],
        "allowed_prefixes": ["routers.trivia", "routers.dependencies"],
    },
    "store": {
        "paths": [ROOT / "routers" / "store"],
        "allowed_prefixes": ["routers.store", "routers.dependencies"],
    },
    "messaging": {
        "paths": [ROOT / "routers" / "messaging"],
        "allowed_prefixes": ["routers.messaging", "routers.dependencies"],
    },
    "notifications": {
        "paths": [ROOT / "routers" / "notifications"],
        "allowed_prefixes": ["routers.notifications", "routers.dependencies"],
    },
    "payments": {
        "paths": [ROOT / "app" / "routers" / "payments"],
        "allowed_prefixes": ["app.routers.payments"],
    },
}


def _iter_python_files(paths):
    for base in paths:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if path.is_file():
                yield path


def _iter_imported_modules(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module


def _is_cross_domain_import(module_name, allowed_prefixes):
    if not (
        module_name.startswith("routers.") or module_name.startswith("app.routers.")
    ):
        return False
    for prefix in allowed_prefixes:
        if module_name == prefix or module_name.startswith(prefix + "."):
            return False
    return True


def test_no_cross_domain_imports():
    violations = []
    for domain, config in DOMAIN_CONFIGS.items():
        for path in _iter_python_files(config["paths"]):
            tree = ast.parse(path.read_text(), filename=str(path))
            for module_name in _iter_imported_modules(tree):
                if _is_cross_domain_import(module_name, config["allowed_prefixes"]):
                    violations.append(f"{path}: {module_name} ({domain})")

    if violations:
        joined = "\n".join(sorted(violations))
        raise AssertionError(f"Cross-domain imports detected:\n{joined}")
