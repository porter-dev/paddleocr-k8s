#!/usr/bin/env bash
# Builds the web app image (Svelte frontend compiled into the Python server) for linux/amd64
# and pushes it to ECR. Deploy it as a Porter app (porter.yaml) or any Deployment that sets
# PADDLEOCR_URL and S3_BUCKET.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/common.sh

ensure_ecr_repo "$APP_IMAGE"
ecr_login_docker
docker buildx build --platform linux/amd64 -t "$ECR_REGISTRY/$APP_IMAGE:$APP_TAG" -t "$ECR_REGISTRY/$APP_IMAGE:latest" --push .
echo "pushed $ECR_REGISTRY/$APP_IMAGE:$APP_TAG"
