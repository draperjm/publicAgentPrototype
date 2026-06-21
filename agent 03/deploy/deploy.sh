#!/usr/bin/env bash
set -e

# ── Configuration ──────────────────────────────────────────────────────────────
SUBSCRIPTION="f811905c-682a-44e3-bd73-a78f381be67e"
RG="rg-agent03"
LOCATION="australiaeast"
ACR="agent03acr"
ENV="agent03-env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"

# Load API keys
source "$APP_DIR/.env"

echo "==> Setting subscription"
az account set --subscription "$SUBSCRIPTION"

# ── Resource Group ─────────────────────────────────────────────────────────────
echo "==> Creating resource group: $RG"
az group create --name "$RG" --location "$LOCATION" --output none

# ── Container Registry ────────────────────────────────────────────────────────
echo "==> Creating ACR: $ACR"
az acr create --name "$ACR" --resource-group "$RG" --sku Basic --admin-enabled true --output none

ACR_LOGIN_SERVER=$(az acr show --name "$ACR" --query loginServer -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR" --query "passwords[0].value" -o tsv)
echo "    ACR: $ACR_LOGIN_SERVER"

echo "==> Logging in to ACR"
echo "$ACR_PASSWORD" | docker login "$ACR_LOGIN_SERVER" --username "$ACR" --password-stdin

# ── Build & Push Images ────────────────────────────────────────────────────────
build_push() {
  local NAME="$1" DOCKERFILE="$2" CONTEXT="$3"
  echo "==> Building $NAME"
  docker build -t "$ACR_LOGIN_SERVER/$NAME:latest" -f "$APP_DIR/$DOCKERFILE" "$CONTEXT"
  echo "==> Pushing $NAME"
  docker push "$ACR_LOGIN_SERVER/$NAME:latest"
}

build_push "decomposer"       "Dockerfile.decomposer"       "$APP_DIR"
build_push "orchestrator"     "Dockerfile.orchestrator"     "$APP_DIR"
build_push "asset-ops"        "Dockerfile.asset_ops"        "$APP_DIR"
build_push "content-reviewer" "Dockerfile.content_reviewer" "$APP_DIR"
build_push "agent-mapper"     "Dockerfile.mapper"           "$APP_DIR"
build_push "agent-verification" "Dockerfile.verification"   "$APP_DIR"
build_push "step-validator"   "Dockerfile.step_validator"   "$APP_DIR"
build_push "frontend"         "frontend/Dockerfile.frontend" "$APP_DIR/frontend"

# ── Container Apps Environment ────────────────────────────────────────────────
echo "==> Creating Container Apps Environment: $ENV"
az containerapp env create \
  --name "$ENV" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --output none

# ── Deploy Internal Services ──────────────────────────────────────────────────
deploy_internal() {
  local NAME="$1" PORT="$2"
  shift 2
  echo "==> Deploying (internal) $NAME"
  az containerapp create \
    --name "$NAME" \
    --resource-group "$RG" \
    --environment "$ENV" \
    --image "$ACR_LOGIN_SERVER/$NAME:latest" \
    --registry-server "$ACR_LOGIN_SERVER" \
    --registry-username "$ACR" \
    --registry-password "$ACR_PASSWORD" \
    --target-port "$PORT" \
    --ingress internal \
    --min-replicas 1 \
    --max-replicas 1 \
    --cpu 0.5 --memory 1.0Gi \
    "$@" \
    --output none
  echo "    $NAME deployed (internal)"
}

deploy_internal "step-validator" 8088 \
  --env-vars "GOOGLE_API_KEY=$GOOGLE_API_KEY" "OPENAI_API_KEY=$OPENAI_API_KEY"

deploy_internal "asset-ops" 8084 \
  --env-vars "OPENAI_API_KEY=$OPENAI_API_KEY"

deploy_internal "content-reviewer" 8085 \
  --env-vars "OPENAI_API_KEY=$OPENAI_API_KEY" "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" "GOOGLE_API_KEY=$GOOGLE_API_KEY"

deploy_internal "agent-mapper" 8087

deploy_internal "agent-verification" 8086 \
  --env-vars "GOOGLE_API_KEY=$GOOGLE_API_KEY"

deploy_internal "orchestrator" 8001 \
  --env-vars \
    "OPENAI_API_KEY=$OPENAI_API_KEY" \
    "ORCHESTRATOR_URL=http://orchestrator/execute" \
    "VALIDATOR_URL=http://step-validator/validate_step" \
    "ASSET_OPS_URL=http://asset-ops/extract_assets" \
    "REVIEWER_URL=http://content-reviewer/review_content" \
    "MAPPER_URL=http://agent-mapper/create_mapping" \
    "VERIF_URL=http://agent-verification/verify_assets"

# ── Deploy Decomposer (external) ──────────────────────────────────────────────
echo "==> Deploying (external) decomposer"
az containerapp create \
  --name "decomposer" \
  --resource-group "$RG" \
  --environment "$ENV" \
  --image "$ACR_LOGIN_SERVER/decomposer:latest" \
  --registry-server "$ACR_LOGIN_SERVER" \
  --registry-username "$ACR" \
  --registry-password "$ACR_PASSWORD" \
  --target-port 8000 \
  --ingress internal \
  --min-replicas 1 --max-replicas 1 \
  --cpu 0.5 --memory 1.0Gi \
  --env-vars "OPENAI_API_KEY=$OPENAI_API_KEY" "ORCHESTRATOR_URL=http://orchestrator/execute" \
  --output none

# ── Deploy Frontend (external / public) ───────────────────────────────────────
echo "==> Deploying (public) frontend"
az containerapp create \
  --name "frontend" \
  --resource-group "$RG" \
  --environment "$ENV" \
  --image "$ACR_LOGIN_SERVER/frontend:latest" \
  --registry-server "$ACR_LOGIN_SERVER" \
  --registry-username "$ACR" \
  --registry-password "$ACR_PASSWORD" \
  --target-port 80 \
  --ingress external \
  --min-replicas 1 --max-replicas 1 \
  --cpu 0.25 --memory 0.5Gi \
  --env-vars "DECOMPOSER_HOST=decomposer" "ORCHESTRATOR_HOST=orchestrator" \
  --output none

FRONTEND_URL=$(az containerapp show \
  --name "frontend" \
  --resource-group "$RG" \
  --query "properties.configuration.ingress.fqdn" -o tsv)

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  DEPLOYMENT COMPLETE                                     ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Frontend URL:  https://$FRONTEND_URL"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "NEXT STEP: Add https://$FRONTEND_URL as an Authorised JavaScript"
echo "origin in your Google Cloud Console OAuth 2.0 Client ID settings."
