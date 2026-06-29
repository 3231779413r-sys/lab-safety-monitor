#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE="${TEST_DOCKER_IMAGE:-lab-safety-monitor-monitor-worker:latest}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker command not found" >&2
  exit 1
fi

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  echo "Docker image not found: ${IMAGE}" >&2
  echo "Build it first with: docker compose build monitor-worker" >&2
  exit 1
fi

exec docker run --rm \
  --network host \
  -v "${PROJECT_ROOT}:/workspace" \
  -w /workspace \
  "${IMAGE}" \
  python test/run_ppe_person_test.py "$@"
