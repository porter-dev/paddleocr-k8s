#!/usr/bin/env bash
# Builds the two PaddleOCR-VL high-performance-serving images that Baidu does not publish
# (the Triton pipeline and the FastAPI gateway) and mirrors the two Baidu images they depend
# on (the vLLM server and the PaddleX HPS base the pipeline is built on), so every image
# the cluster pulls, and every image a rebuild starts from, lives in ECR. The build context
# is assembled here from upstream sources:
#   - Baidu's HPS SDK tarball (Triton model repository, launcher, client wheel; no weights)
#   - PaddleOCR's deploy/paddleocr_vl_docker/hps gateway and pipeline Dockerfile at a pinned commit
# The gateway Dockerfile is rewritten without its BuildKit bind mount so Kaniko can build it.
#
# Usage: set AWS_PROFILE, then
#   ECR_REGISTRY=<account>.dkr.ecr.<region>.amazonaws.com KUBE_CONTEXT=<ctx> scripts/build-paddleocr-images.sh
# BUILDER=kaniko (default) runs three pods in the cluster so nothing is pulled locally; the
# base images are 8 to 15 GB. BUILDER=docker builds on this machine instead.
# NODE_GROUP_ID pins the build pods to a Porter node group. GATEWAY_ONLY=true rebuilds just the gateway.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/common.sh
: "${BUILDER:=kaniko}"
: "${PADDLEOCR_REF:=2661c7c0ef5c613e8f93c6e93b2e052399f0f854}"
: "${SDK_VERSION:=v3.6}"
SDK_DIR=paddlex_hps_PaddleOCR-VL-1.6_sdk
SDK_URL="https://paddle-model-ecology.bj.bcebos.com/paddlex/PaddleX3.0/deploy/paddlex_hps/public/sdks/${SDK_VERSION}/${SDK_DIR}.tar.gz"
UPSTREAM="https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/${PADDLEOCR_REF}/deploy/paddleocr_vl_docker/hps"

ctx=$(mktemp -d "${TMPDIR:-/tmp}/hpsctx.XXXXXX")
trap 'rm -rf "$ctx"' EXIT
echo "assembling build context in $ctx"
curl -fsSL "$SDK_URL" | tar xz -C "$ctx"
echo "SDK $(cat "$ctx/$SDK_DIR/version.txt")"
mkdir -p "$ctx/gateway"
curl -fsSL "$UPSTREAM/gateway/app.py" -o "$ctx/gateway/app.py"
curl -fsSL "$UPSTREAM/gateway/requirements.txt" -o "$ctx/gateway/requirements.txt"
curl -fsSL "$UPSTREAM/pipeline.Dockerfile" -o "$ctx/pipeline.Dockerfile"
# Upstream's pipeline image fetches the layout detector (PP-DocLayoutV3, ~126 MB) from a
# model hub on every pod start. Fetch it at build time instead so a pod starts with every
# weight local and nothing is pulled from outside the registry at runtime.
cat >> "$ctx/pipeline.Dockerfile" <<DOCKERFILE

RUN python -c "from paddlex.inference.utils.official_models import official_models; print(official_models['PP-DocLayoutV3'])" \
    && du -sh /root/.paddlex/official_models/PP-DocLayoutV3
DOCKERFILE
cat > "$ctx/gateway.Dockerfile" <<DOCKERFILE
FROM python:3.10-slim
RUN apt-get update \\
    && apt-get install -y --no-install-recommends curl libgl1 libglib2.0-0 \\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY ${SDK_DIR}/client /tmp/sdk
COPY gateway .
RUN python -m pip install --no-cache-dir -r requirements.txt \\
    && python -m pip install --no-cache-dir -r /tmp/sdk/requirements.txt \\
    && python -m pip install --no-cache-dir /tmp/sdk/paddlex_hps_client-*.whl \\
    && rm -rf /tmp/sdk
ENV HPS_TRITON_URL=localhost:8001
ENV HPS_MAX_CONCURRENT_INFERENCE_REQUESTS=16
ENV HPS_MAX_CONCURRENT_NON_INFERENCE_REQUESTS=64
ENV HPS_INFERENCE_TIMEOUT=600
ENV HPS_LOG_LEVEL=INFO
ENV HPS_UVICORN_WORKERS=4
EXPOSE 8080
# exec so uvicorn is PID 1 and receives SIGTERM; upstream's shell-form CMD leaves a shell as
# PID 1 that swallows the signal, and the pod then lives out its whole grace period.
CMD ["sh", "-c", "exec uvicorn --host 0.0.0.0 --port 8080 --workers \${HPS_UVICORN_WORKERS} app:app"]
DOCKERFILE

for repo in "$PIPELINE_IMAGE" "$GATEWAY_IMAGE" "$VLLM_IMAGE" "$HPS_BASE_IMAGE"; do ensure_ecr_repo "$repo"; done
PIPELINE_REF="$ECR_REGISTRY/$PIPELINE_IMAGE:$PIPELINE_TAG"
GATEWAY_REF="$ECR_REGISTRY/$GATEWAY_IMAGE:$GATEWAY_TAG"
VLLM_REF="$ECR_REGISTRY/$VLLM_IMAGE:$VLLM_TAG"
HPS_BASE_REF="$ECR_REGISTRY/$HPS_BASE_IMAGE:$HPS_BASE_TAG"

mirror_locally() { # source destination
  if command -v crane >/dev/null; then
    crane copy "$1" "$2"
  else
    docker pull "$1" && docker tag "$1" "$2" && docker push "$2"
  fi
}

build_with_docker() {
  ecr_login_docker
  command -v crane >/dev/null && aws ecr get-login-password --region "$AWS_REGION" | crane auth login --username AWS --password-stdin "$ECR_REGISTRY"
  mirror_locally "$HPS_BASE_SOURCE_IMAGE" "$HPS_BASE_REF"
  mirror_locally "$VLLM_SOURCE_IMAGE" "$VLLM_REF"
  docker buildx build --platform linux/amd64 -f "$ctx/pipeline.Dockerfile" --build-arg "HPS_SDK_DIR=$SDK_DIR" --build-arg "BASE_IMAGE=$HPS_BASE_REF" -t "$PIPELINE_REF" --push "$ctx"
  docker buildx build --platform linux/amd64 -f "$ctx/gateway.Dockerfile" -t "$GATEWAY_REF" --push "$ctx"
}

build_with_kaniko() {
  require_kube_context
  ensure_ecr_push_secret
  local tarball; tarball=$(mktemp "${TMPDIR:-/tmp}/hpsctx.XXXXXX.tgz")
  tar czf "$tarball" -C "$ctx" .
  kc delete configmap hps-build-context --ignore-not-found >/dev/null
  kc create configmap hps-build-context --from-file=context.tgz="$tarball" >/dev/null
  rm -f "$tarball"

  local placement=""
  if [ -n "${NODE_GROUP_ID:-}" ]; then
    placement=$(cat <<YAML
  nodeSelector: { porter.run/node-group-id: "$NODE_GROUP_ID" }
  tolerations:
  - { key: porter.run/node-group-id, operator: Equal, value: "$NODE_GROUP_ID", effect: NoSchedule }
  - { key: nvidia.com/gpu, operator: Exists, effect: NoSchedule }
YAML
)
  fi

  kaniko_pod() { # name dockerfile destination
cat <<YAML
apiVersion: v1
kind: Pod
metadata: { name: $1, namespace: $NAMESPACE, labels: { app: hps-image-build } }
spec:
  restartPolicy: Never
$placement
  volumes:
  - { name: ctx, configMap: { name: hps-build-context } }
  - { name: workspace, emptyDir: {} }
  - { name: docker, secret: { secretName: ecr-push, items: [ { key: .dockerconfigjson, path: config.json } ] } }
  initContainers:
  - name: unpack
    image: busybox:1.36
    command: [sh, -c, "tar xzf /ctx/context.tgz -C /workspace"]
    volumeMounts: [ { name: ctx, mountPath: /ctx }, { name: workspace, mountPath: /workspace } ]
  containers:
  - name: kaniko
    image: gcr.io/kaniko-project/executor:v1.23.2
    args: ["--dockerfile=/workspace/$2", "--context=dir:///workspace", "--destination=$3", "--build-arg=HPS_SDK_DIR=$SDK_DIR", "--build-arg=BASE_IMAGE=$HPS_BASE_REF", "--cache=false", "--snapshot-mode=redo", "--compressed-caching=false"]
    resources: { requests: { cpu: "1", memory: 4Gi }, limits: { memory: 12Gi } }
    volumeMounts: [ { name: workspace, mountPath: /workspace }, { name: docker, mountPath: /kaniko/.docker } ]
YAML
  }

  mirror_pod() { # name source destination
cat <<YAML
apiVersion: v1
kind: Pod
metadata: { name: $1, namespace: $NAMESPACE, labels: { app: hps-image-build } }
spec:
  restartPolicy: Never
$placement
  volumes:
  - { name: docker, secret: { secretName: ecr-push, items: [ { key: .dockerconfigjson, path: config.json } ] } }
  containers:
  - name: crane
    image: gcr.io/go-containerregistry/crane:latest
    args: ["copy", "$2", "$3"]
    env: [ { name: DOCKER_CONFIG, value: /docker } ]
    volumeMounts: [ { name: docker, mountPath: /docker } ]
YAML
  }

  wait_for_pods() {
    for pod in "$@"; do
      while true; do
        case "$(kc get pod "$pod" -o jsonpath='{.status.phase}')" in
          Succeeded) echo "$pod: done $(date +%H:%M:%S)"; break ;;
          Failed) echo "$pod: FAILED"; kc logs "$pod" --tail=40; exit 1 ;;
          *) sleep 20 ;;
        esac
      done
    done
  }

  kc delete pod -l app=hps-image-build --ignore-not-found >/dev/null
  if [ "${GATEWAY_ONLY:-false}" = true ]; then
    kaniko_pod build-hps-gateway gateway.Dockerfile "$GATEWAY_REF" | kubectl --context "$KUBE_CONTEXT" apply -f -
    wait_for_pods build-hps-gateway
    return
  fi
  mirror_pod mirror-hps-base "$HPS_BASE_SOURCE_IMAGE" "$HPS_BASE_REF" | kubectl --context "$KUBE_CONTEXT" apply -f -
  mirror_pod mirror-vllm "$VLLM_SOURCE_IMAGE" "$VLLM_REF" | kubectl --context "$KUBE_CONTEXT" apply -f -
  kaniko_pod build-hps-gateway gateway.Dockerfile "$GATEWAY_REF" | kubectl --context "$KUBE_CONTEXT" apply -f -
  echo "mirroring the PaddleX HPS base image first; the pipeline build starts from the ECR copy"
  wait_for_pods mirror-hps-base
  kaniko_pod build-hps-pipeline pipeline.Dockerfile "$PIPELINE_REF" | kubectl --context "$KUBE_CONTEXT" apply -f -
  echo "waiting for the remaining pods (the pipeline image takes 10-20 minutes)"
  wait_for_pods build-hps-gateway mirror-vllm build-hps-pipeline
}

case "$BUILDER" in
  docker) build_with_docker ;;
  kaniko) build_with_kaniko ;;
  *) echo "BUILDER must be kaniko or docker"; exit 2 ;;
esac

cat <<MSG

Images pushed:
  $PIPELINE_REF
  $GATEWAY_REF
  $VLLM_REF
  $HPS_BASE_REF  (build base only, not pulled by the chart)
charts/paddleocr-vl-hps/values.yaml expects these names and tags; set registry: $ECR_REGISTRY in your .values.yaml.
MSG
