#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

python -m pytest --cov --cov-report=term-missing
cfn-lint infra/bootstrap.yaml infra/platform.yaml
docker compose config --quiet

