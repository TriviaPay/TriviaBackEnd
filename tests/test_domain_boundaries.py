import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOMAIN_CONFIGS = {
    "app_versions": {
        "paths": [ROOT / "routers" / "app_versions"],
        "allowed_prefixes": ["routers.app_versions", "routers.dependencies"],
    },
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
    """
    Enforces "no cross-domain imports" across all Python modules within each domain,
    including `api.py` routers, and also `service.py`/`repository.py`/`schemas.py`.
    """
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


def test_non_auth_domains_do_not_import_user_model():
    """
    Data ownership rule: `User` is owned by Auth/Profile.

    Non-auth domains should not import `User` from `models`; use `core.users` (Auth service API)
    for lookups/locking and pass around `account_id` where possible.
    """
    non_auth_paths = [
        ROOT / "routers" / "trivia",
        ROOT / "routers" / "store",
        ROOT / "routers" / "messaging",
        ROOT / "routers" / "notifications",
    ]
    violations = []
    for path in _iter_python_files(non_auth_paths):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "models":
                for alias in node.names:
                    if alias.name == "User":
                        violations.append(str(path))

    if violations:
        joined = "\n".join(sorted(set(violations)))
        raise AssertionError(
            "Non-auth domains import `User` directly. Use `core.users` instead:\n"
            + joined
        )
