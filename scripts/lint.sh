#!/usr/bin/env bash
set -euo pipefail

python3 -m black --check .
python3 -m isort --check-only .
python3 -m flake8 .
python3 -m mypy --explicit-package-bases --exclude '(^|/)auth\\.py$' app/services
