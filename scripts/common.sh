# Shared defaults for the scripts in this directory. Override any of them in the environment.
# Authentication comes from the active AWS profile (AWS_PROFILE) for ECR and from
# KUBE_CONTEXT for anything that touches the cluster.
: "${AWS_REGION:=us-east-1}"
: "${ECR_REGISTRY:=$(aws sts get-caller-identity --query Account --output text).dkr.ecr.${AWS_REGION}.amazonaws.com}"
: "${NAMESPACE:=paddleocr}"

: "${PIPELINE_IMAGE:=paddleocr-hps-pipeline}"
: "${PIPELINE_TAG:=paddlex3.6-gpu-sdk0.1.0-bundled}"
: "${GATEWAY_IMAGE:=paddleocr-hps-gateway}"
: "${GATEWAY_TAG:=paddlex3.6-sdk0.1.1}"
: "${VLLM_IMAGE:=paddleocr-genai-vllm-server}"
: "${VLLM_TAG:=paddleocr3.6-nvidia-gpu-offline}"
: "${VLLM_SOURCE_IMAGE:=ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-genai-vllm-server:${VLLM_TAG}}"
: "${HPS_BASE_IMAGE:=paddlex-hps}"
: "${HPS_BASE_TAG:=paddlex3.6-gpu}"
: "${HPS_BASE_SOURCE_IMAGE:=ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlex/hps:${HPS_BASE_TAG}}"
: "${APP_IMAGE:=paddleocr-app}"
: "${APP_TAG:=$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)}"

require_kube_context() { : "${KUBE_CONTEXT:?set KUBE_CONTEXT to the kubectl context of the target cluster}"; }
kc() { kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" "$@"; }

ensure_ecr_repo() {
  aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$1" >/dev/null 2>&1 \
    || aws ecr create-repository --region "$AWS_REGION" --repository-name "$1" >/dev/null
}

ecr_login_docker() {
  aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"
}

ensure_ecr_push_secret() {
  kubectl --context "$KUBE_CONTEXT" create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl --context "$KUBE_CONTEXT" apply -f - >/dev/null
  kc delete secret ecr-push --ignore-not-found >/dev/null
  kc create secret docker-registry ecr-push \
    --docker-server="$ECR_REGISTRY" --docker-username=AWS \
    --docker-password="$(aws ecr get-login-password --region "$AWS_REGION")" >/dev/null
  echo "ecr-push secret refreshed in $NAMESPACE (token is valid for 12 hours)"
}
