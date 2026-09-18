#!/usr/bin/env bash
# Builds a stock vLLM server image carrying our patched PaddleOCR-VL model file and
# pushes it to ECR. The patch is the encoder CUDA graph support from
# vllm-project/vllm#44394, backported onto the release tag in VLLM_BASE_TAG.
#
# Only one Python file differs from the upstream image, so the build is a thin layer on
# top of it. Kaniko runs in-cluster because the image is linux/amd64 and local Docker on
# Apple silicon is arm64. Every layer, base included, lands in ECR, so nothing is pulled
# from Docker Hub at runtime.
#
# Required: KUBE_CONTEXT, AWS_PROFILE. Optional: REGION, ECR_REPO, VLLM_BASE_TAG,
# IMAGE_TAG, VLLM_SRC, NAMESPACE.
set -euo pipefail
: "${KUBE_CONTEXT:?set KUBE_CONTEXT to the kubectl context of the build cluster}"
: "${AWS_PROFILE:?set AWS_PROFILE}"
: "${REGION:=us-west-2}"
: "${ECR_REPO:=vllm-openai}"
: "${VLLM_BASE_TAG:=v0.29.0}"
: "${IMAGE_TAG:=${VLLM_BASE_TAG}-paddleocr-encoder-cudagraph}"
: "${VLLM_SRC:?set VLLM_SRC to a vLLM checkout containing your patched paddleocr_vl.py}"
: "${NAMESPACE:=default}"
: "${POD:=vllm-image-build}"

PATCHED="$VLLM_SRC/vllm/model_executor/models/paddleocr_vl.py"
[ -f "$PATCHED" ] || { echo "patched model file not found at $PATCHED"; exit 2; }

kc() { kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" "$@"; }

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
DEST="$REGISTRY/$ECR_REPO:$IMAGE_TAG"
aws ecr describe-repositories --region "$REGION" --repository-names "$ECR_REPO" >/dev/null 2>&1 ||
  aws ecr create-repository --region "$REGION" --repository-name "$ECR_REPO" >/dev/null
echo "destination: $DEST"

workdir=$(mktemp -d "${BUILD_DIR:-${TMPDIR:-/tmp}}/vllm-build.XXXXXX")
trap 'rm -rf "$workdir"' EXIT

# The install path is resolved at build time rather than hardcoded, and importing the
# patched module afterwards proves the backport loads against this release's runtime.
cat > "$workdir/Dockerfile" <<DOCKERFILE
FROM vllm/vllm-openai:$VLLM_BASE_TAG
COPY paddleocr_vl.py /opt/paddleocr-vl-patch/paddleocr_vl.py
RUN set -eu; \\
    dest="\$(VLLM_LOGGING_LEVEL=ERROR python3 -c 'import vllm.model_executor.models as m, pathlib; print(pathlib.Path(m.__file__).parent)' 2>/dev/null | tail -n1)"; \\
    [ -d "\$dest" ] || { echo "could not resolve the vllm models package dir, got: \$dest"; exit 1; }; \\
    echo "installing into \$dest"; \\
    cp /opt/paddleocr-vl-patch/paddleocr_vl.py "\$dest/paddleocr_vl.py"; \\
    rm -rf /opt/paddleocr-vl-patch "\$dest/__pycache__/paddleocr_vl."*; \\
    VLLM_LOGGING_LEVEL=ERROR python3 -c "import vllm.model_executor.models.paddleocr_vl as p; assert hasattr(p.PaddleOCRVLForConditionalGeneration, 'encoder_cudagraph_forward'), 'encoder cudagraph entrypoint missing'; print('PATCH_VERIFIED', p.__file__)"
DOCKERFILE
cp "$PATCHED" "$workdir/paddleocr_vl.py"

echo "publishing build context and ECR credentials"
kc delete configmap "$POD-ctx" --ignore-not-found >/dev/null
kc delete secret "$POD-auth" --ignore-not-found >/dev/null
kc delete pod "$POD" --ignore-not-found --wait=true >/dev/null
kc create configmap "$POD-ctx" --from-file="$workdir/Dockerfile" --from-file="$workdir/paddleocr_vl.py" >/dev/null
AUTH=$(aws ecr get-login-password --region "$REGION" | tr -d '\n' | { read -r pw; printf 'AWS:%s' "$pw" | base64 | tr -d '\n'; })
printf '{"auths":{"%s":{"auth":"%s"}}}' "$REGISTRY" "$AUTH" > "$workdir/config.json"
kc create secret generic "$POD-auth" --from-file="$workdir/config.json" >/dev/null

cat <<POD | kc apply -f - >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: $POD
spec:
  restartPolicy: Never
  nodeSelector:
    kubernetes.io/arch: amd64
  initContainers:
  # ConfigMap keys are symlinks into ..data/, and kaniko's COPY preserves the link, so the
  # image would get a dangling symlink instead of the file. Dereference into an emptyDir.
  - name: materialize-context
    image: busybox:1.36
    command: [sh, -c, 'cp -rL /cm/. /workspace/ && ls -l /workspace']
    volumeMounts:
    - name: cm
      mountPath: /cm
    - name: ctx
      mountPath: /workspace
  containers:
  - name: kaniko
    image: gcr.io/kaniko-project/executor:v1.23.2
    args:
    - --context=dir:///workspace
    - --dockerfile=/workspace/Dockerfile
    - --destination=$DEST
    - --single-snapshot
    - --cache=false
    - --compressed-caching=false
    - --verbosity=info
    volumeMounts:
    - name: ctx
      mountPath: /workspace
    - name: auth
      mountPath: /kaniko/.docker
    resources:
      requests:
        cpu: "2"
        memory: 6Gi
        ephemeral-storage: 34Gi
      limits:
        memory: 12Gi
        ephemeral-storage: 40Gi
  volumes:
  - name: cm
    configMap:
      name: $POD-ctx
  - name: ctx
    emptyDir: {}
  - name: auth
    secret:
      secretName: $POD-auth
      items:
      - key: config.json
        path: config.json
POD

echo "waiting for the build pod (Karpenter may need to provision an amd64 node)"
for i in $(seq 1 80); do
  phase=$(kc get pod "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || true)
  [ "$phase" = "Running" ] || [ "$phase" = "Succeeded" ] || [ "$phase" = "Failed" ] && break
  sleep 15
done
kc logs -f "$POD" 2>&1 || true
# `logs -f` can return before the pod leaves Running (the long final snapshot drops the
# stream), so wait for a terminal phase instead of sampling once.
for _ in $(seq 1 120); do
  phase=$(kc get pod "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || true)
  case "$phase" in Succeeded|Failed) break ;; esac
  sleep 10
done
echo "build pod phase: $phase"
[ "$phase" = "Succeeded" ] || { echo "BUILD FAILED"; exit 1; }

kc delete pod "$POD" --ignore-not-found >/dev/null
kc delete configmap "$POD-ctx" --ignore-not-found >/dev/null
kc delete secret "$POD-auth" --ignore-not-found >/dev/null
echo "pushed $DEST"
aws ecr describe-images --region "$REGION" --repository-name "$ECR_REPO" \
  --image-ids imageTag="$IMAGE_TAG" \
  --query 'imageDetails[0].{pushed:imagePushedAt,sizeGB:imageSizeInBytes,digest:imageDigest}' --output table
