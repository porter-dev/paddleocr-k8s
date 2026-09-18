#!/usr/bin/env bash
# Installs or upgrades the PaddleOCR-VL serving stack (charts/paddleocr-vl-hps) using the
# cluster-specific values in .values.yaml (gitignored: registry, node group id, instance
# types, sizing). One pod per GPU: vLLM sidecar + Triton pipeline + gateway.
#
# Required: KUBE_CONTEXT. Optional: VALUES_FILE (default .values.yaml), RELEASE (default
# paddleocr-hps), NAMESPACE (default paddleocr), extra helm arguments after the script name.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${KUBE_CONTEXT:?set KUBE_CONTEXT to the kubectl context of the target cluster}"
: "${NAMESPACE:=paddleocr}"
: "${VALUES_FILE:=.values.yaml}"
: "${RELEASE:=paddleocr-hps}"
[ -f "$VALUES_FILE" ] || { echo "$VALUES_FILE not found; copy .values.example.yaml and fill in your cluster"; exit 2; }

helm upgrade --install "$RELEASE" charts/paddleocr-vl-hps \
  --kube-context "$KUBE_CONTEXT" --namespace "$NAMESPACE" --create-namespace \
  -f "$VALUES_FILE" "$@"
echo "waiting for the pod (image pulls plus vLLM startup take about 10 minutes on a fresh node)"
kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" rollout status "deploy/$RELEASE" --timeout=1200s
echo "in-cluster URL: http://$RELEASE.$NAMESPACE.svc.cluster.local:8080"
