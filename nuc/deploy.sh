#!/usr/bin/env bash
#
# Deploy the CFU YOLO detector to the Nuclio instance that backs CVAT.
#
# Prereqs:
#   - CVAT running with the serverless profile:
#       docker compose -f docker-compose.yml -f components/serverless/docker-compose.serverless.yml up -d
#   - `nuctl` CLI installed (same version as CVAT's nuclio container).
#
# Usage:
#   ./deploy.sh            # deploy to the "cvat" project on the nuclio platform
#
set -eu

FUNC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="cvat"
PLATFORM="local"

echo "Deploying function from: ${FUNC_DIR}"

nuctl create project "${PROJECT}" --platform "${PLATFORM}" 2>/dev/null || true

nuctl deploy --project-name "${PROJECT}" \
  --path "${FUNC_DIR}" \
  --file "${FUNC_DIR}/function.yaml" \
  --platform "${PLATFORM}"

echo
echo "Deployed. Verify with:  nuctl get function --platform ${PLATFORM}"
echo "Then in CVAT: open a task → Actions → Automatic annotation → 'YOLO CFU Colony Detector'."
