#!/usr/bin/env bash
# Deploy VizAgent to the team's Kubernetes namespace at http://<team-host>/app.
# Run on the workshop VM from the repo root:  bash deploy/deploy.sh
# Same approach as vast-builders-challenge/.cursor/skills/deployment/deploy-app-no-registry:
# code ships as a ConfigMap, no Docker build/push. Re-run after every code change.
set -euo pipefail

APP_NAME=vizagent
APP_DIR="$(cd "$(dirname "$0")/../app" && pwd)"
APP_PORT=8080

# Team values come from the single /config/<team>.config on the VM.
mapfile -t TEAM_CONFIGS < <(find /config -maxdepth 1 -type f -name '*.config' | sort)
(( ${#TEAM_CONFIGS[@]} == 1 )) || { echo "expected exactly one /config/*.config"; exit 1; }
set -a && source "${TEAM_CONFIGS[0]}" && set +a

NS="$USERNAME"
APP_HOST="${INGRESS_URL#http://}"
APP_HOST="${APP_HOST#https://}"
APP_HOST="${APP_HOST%%/*}"

for var in WANDB_API_KEY WANDB_TEAM WANDB_PROJECT; do
  [[ -n "${!var:-}" ]] || echo "warning: $var is not set; LLM judging/tracing will be off"
done

echo "Deploying $APP_NAME to namespace $NS at http://$APP_HOST/app"

# 1. Code (flat directory only; ConfigMap limit ~1 MiB)
kubectl -n "$NS" create configmap "${APP_NAME}-code" \
  --from-file="$APP_DIR" \
  --dry-run=client -o yaml | kubectl apply -f -

# 2. Secrets: VSS login + W&B inference
kubectl -n "$NS" create secret generic "${APP_NAME}-secrets" \
  --from-literal=VSS_URL="$INGRESS_URL" \
  --from-literal=VSS_USERNAME="$USERNAME" \
  --from-literal=VSS_PASSWORD="$PASSWORD" \
  --from-literal=WANDB_API_KEY="${WANDB_API_KEY:-}" \
  --from-literal=WANDB_TEAM="${WANDB_TEAM:-}" \
  --from-literal=WANDB_PROJECT="${WANDB_PROJECT:-}" \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Deployment + Service + Ingress (path /app on the team host, prefix stripped)
kubectl -n "$NS" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${APP_NAME}
  template:
    metadata:
      labels:
        app: ${APP_NAME}
    spec:
      containers:
      - name: app
        image: python:3.12-slim
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: ${APP_PORT}
        env:
        - name: PORT
          value: "${APP_PORT}"
        - name: LLM_MODEL
          value: "${LLM_MODEL:-meta-llama/Llama-3.1-8B-Instruct}"
        envFrom:
        - secretRef:
            name: ${APP_NAME}-secrets
        volumeMounts:
        - name: code
          mountPath: /code
        workingDir: /code
        command: ["bash", "-c"]
        args:
        - |
          set -euo pipefail
          pip install --no-cache-dir -q -r requirements.txt
          exec python main.py
        readinessProbe:
          httpGet:
            path: /health
            port: ${APP_PORT}
          initialDelaySeconds: 10
          periodSeconds: 10
      volumes:
      - name: code
        configMap:
          name: ${APP_NAME}-code
---
apiVersion: v1
kind: Service
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
spec:
  selector:
    app: ${APP_NAME}
  ports:
  - name: http
    port: 80
    targetPort: ${APP_PORT}
  type: ClusterIP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /\$2
spec:
  ingressClassName: nginx
  rules:
  - host: ${APP_HOST}
    http:
      paths:
      - path: /app(/|$)(.*)
        pathType: ImplementationSpecific
        backend:
          service:
            name: ${APP_NAME}
            port:
              number: 80
EOF

# ConfigMap changes are not picked up by a running pod, so always restart.
kubectl -n "$NS" rollout restart deploy/"$APP_NAME"
kubectl -n "$NS" rollout status deploy/"$APP_NAME" --timeout=300s

echo "Health: $(curl -sS "http://${APP_HOST}/app/health")"
echo "Live:   http://${APP_HOST}/app/"
