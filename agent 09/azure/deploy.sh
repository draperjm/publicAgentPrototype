#!/usr/bin/env bash
# =============================================================================
# deploy.sh  —  Deploy Agent 09 to Azure Container Apps
#
# Usage:
#   ./deploy.sh [--skip-build] [--skip-push] [--env-file azure/.env.production]
#
# Prerequisites:
#   - az cli logged in  (az login)
#   - Docker running
#   - Secrets file (see azure/.env.example)
#
# First run:  ./deploy.sh                (builds, pushes, deploys everything)
# Code-only:  ./deploy.sh --skip-build   (re-deploys without rebuilding images)
# =============================================================================
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
RESOURCE_GROUP="rg-agent09"
LOCATION="australiaeast"
ACR_NAME="agent09acr"              # Must be globally unique; lowercase alphanumeric
ACA_ENV="env-agent09"
ACA_INTERNAL_SUFFIX="internal.reddesert-36d5f493.australiaeast.azurecontainerapps.io"
STORAGE_ACCOUNT="agent09sa"        # Globally unique; 3-24 lowercase alphanumeric chars
DOCUMENTS_SHARE="documents"
OUTPUT_SHARE="output"
CHUNK_SHARE="chunks"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# ── Parse args ────────────────────────────────────────────────────────────────
SKIP_BUILD=false
SKIP_PUSH=false
ENV_FILE="$SCRIPT_DIR/.env.production"

for arg in "$@"; do
    case $arg in
        --skip-build)   SKIP_BUILD=true  ;;
        --skip-push)    SKIP_PUSH=true   ;;
        --env-file=*)   ENV_FILE="${arg#*=}" ;;
    esac
done

# ── Load secrets ──────────────────────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: Secrets file not found: $ENV_FILE"
    echo "       Copy azure/.env.example → azure/.env.production and fill in keys."
    exit 1
fi
set -a; source "$ENV_FILE"; set +a

: "${OPENAI_API_KEY:?Missing OPENAI_API_KEY in $ENV_FILE}"
: "${ANTHROPIC_API_KEY:?Missing ANTHROPIC_API_KEY in $ENV_FILE}"
: "${GOOGLE_API_KEY:?Missing GOOGLE_API_KEY in $ENV_FILE}"

LLM_MODEL="${LLM_MODEL:-gpt-4o}"
TAGGING_MODEL="${TAGGING_MODEL:-gpt-4o-mini}"
VISION_MODEL="${VISION_MODEL:-gemini-2.0-flash}"
ASSET_EXTRACTION_MODEL="${ASSET_EXTRACTION_MODEL:-claude-sonnet-4-6}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.0-flash}"
VALIDATOR_MODEL="${VALIDATOR_MODEL:-gpt-4o}"
ANALYTICS_MODEL="${ANALYTICS_MODEL:-gpt-4o}"

# ── Step 1: Resource group ─────────────────────────────────────────────────────
echo "=== [1/9] Resource group: $RESOURCE_GROUP ==="
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
echo "    OK"

# ── Step 2: Container Registry ───────────────────────────────────────────────
echo "=== [2/9] Container Registry: $ACR_NAME ==="
az acr create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ACR_NAME" \
    --sku Basic \
    --admin-enabled true \
    --output none 2>/dev/null || true

ACR_SERVER=$(az acr show  --name "$ACR_NAME" --query loginServer -o tsv)
ACR_USER=$(az acr credential show  --name "$ACR_NAME" --query username -o tsv)
ACR_PASS=$(az acr credential show  --name "$ACR_NAME" --query "passwords[0].value" -o tsv)
echo "    OK: $ACR_SERVER"

# ── Step 3: Build & push images ───────────────────────────────────────────────
echo "=== [3/9] Docker images ==="

# Map:  service-name → "build-context:dockerfile"
declare -A IMAGES=(
    ["responder"]="$REPO_ROOT:Dockerfile.responder"
    ["orchestrator"]="$REPO_ROOT:Dockerfile.orchestrator"
    ["document-reviewer"]="$REPO_ROOT:Dockerfile.document_reviewer"
    ["document-extractor"]="$REPO_ROOT:Dockerfile.document_extractor"
    ["document-chunker"]="$REPO_ROOT:Dockerfile.document_chunker"
    ["step-validator"]="$REPO_ROOT:Dockerfile.step_validator"
    ["data-analytics"]="$REPO_ROOT:Dockerfile.data_analytics"
    ["variance-validator"]="$REPO_ROOT:Dockerfile.variance_validator"
    ["frontend"]="$REPO_ROOT/frontend:Dockerfile.frontend"
)

if [ "$SKIP_BUILD" = false ]; then
    # Stage ACA-specific files into the frontend build context, overwriting the
    # local dev versions. Originals are saved as .bak and restored after the build.
    cp "$REPO_ROOT/frontend/nginx.conf"    "$REPO_ROOT/frontend/nginx.conf.bak"
    cp "$REPO_ROOT/frontend/entrypoint.sh" "$REPO_ROOT/frontend/entrypoint.sh.bak"
    cp "$SCRIPT_DIR/nginx.aca.conf"        "$REPO_ROOT/frontend/nginx.conf"
    cp "$SCRIPT_DIR/entrypoint-nginx.sh"   "$REPO_ROOT/frontend/entrypoint.sh"

    # Use 'az acr build' — builds and pushes in the cloud, bypasses local Docker proxy.
    for svc in "${!IMAGES[@]}"; do
        IFS=':' read -r ctx dockerfile <<< "${IMAGES[$svc]}"
        echo "    Building  $svc (cloud build) ..."
        PYTHONUTF8=1 az acr build \
            --registry "$ACR_NAME" \
            --image "$svc:latest" \
            --file "$ctx/$dockerfile" \
            "$ctx" \
            --no-logs \
            --output none
        echo "    OK: $svc"
    done

    # Restore local dev frontend config
    mv "$REPO_ROOT/frontend/nginx.conf.bak"    "$REPO_ROOT/frontend/nginx.conf"
    mv "$REPO_ROOT/frontend/entrypoint.sh.bak" "$REPO_ROOT/frontend/entrypoint.sh"
fi

# (push is handled by az acr build above; --skip-push has no effect in cloud-build mode)

# ── Step 4: Storage account + file shares ─────────────────────────────────────
echo "=== [4/9] Storage: $STORAGE_ACCOUNT ==="
az storage account create \
    --name "$STORAGE_ACCOUNT" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --sku Standard_LRS \
    --kind StorageV2 \
    --output none 2>/dev/null || true

STORAGE_KEY=$(az storage account keys list \
    --resource-group "$RESOURCE_GROUP" \
    --account-name "$STORAGE_ACCOUNT" \
    --query "[0].value" -o tsv)

for share in "$DOCUMENTS_SHARE" "$OUTPUT_SHARE" "$CHUNK_SHARE"; do
    az storage share create \
        --name "$share" \
        --account-name "$STORAGE_ACCOUNT" \
        --account-key "$STORAGE_KEY" \
        --output none 2>/dev/null || true
    echo "    Share: $share"
done

# ── Step 5: ACA environment ───────────────────────────────────────────────────
echo "=== [5/9] ACA environment: $ACA_ENV ==="
az containerapp env create \
    --name "$ACA_ENV" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none 2>/dev/null || true

# Register file shares in the environment (idempotent)
az containerapp env storage set \
    --name "$ACA_ENV" --resource-group "$RESOURCE_GROUP" \
    --storage-name "documents" \
    --azure-file-account-name "$STORAGE_ACCOUNT" \
    --azure-file-account-key "$STORAGE_KEY" \
    --azure-file-share-name "$DOCUMENTS_SHARE" \
    --access-mode ReadWrite --output none 2>/dev/null || true

az containerapp env storage set \
    --name "$ACA_ENV" --resource-group "$RESOURCE_GROUP" \
    --storage-name "output" \
    --azure-file-account-name "$STORAGE_ACCOUNT" \
    --azure-file-account-key "$STORAGE_KEY" \
    --azure-file-share-name "$OUTPUT_SHARE" \
    --access-mode ReadWrite --output none 2>/dev/null || true

az containerapp env storage set \
    --name "$ACA_ENV" --resource-group "$RESOURCE_GROUP" \
    --storage-name "chunks" \
    --azure-file-account-name "$STORAGE_ACCOUNT" \
    --azure-file-account-key "$STORAGE_KEY" \
    --azure-file-share-name "$CHUNK_SHARE" \
    --access-mode ReadWrite --output none 2>/dev/null || true

echo "    OK"

# ── Step 6: Deploy backend services ───────────────────────────────────────────
echo "=== [6/9] Backend container apps ==="

# Use az rest (ARM API) to deploy container apps.
# The --yaml flag in az containerapp create has a known bug on Windows that causes
# a UnicodeDecodeError / Boolean parse failure. az rest bypasses the CLI parser entirely.
SUB_ID=$(az account show --query id -o tsv)
ACA_BASE="https://management.azure.com/subscriptions/$SUB_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.App/containerApps"
API="?api-version=2024-03-01"

# Resolve environment resource ID once
ACA_ENV_ID=$(az containerapp env show \
    --name "$ACA_ENV" \
    --resource-group "$RESOURCE_GROUP" \
    --query id -o tsv)

# deploy_app  <name>  <json-body>
deploy_app() {
    local name="$1"
    local body="$2"
    echo "    Deploying $name ..."
    az rest --method PUT \
        --url "${ACA_BASE}/${name}${API}" \
        --body "$body" \
        --output none
    echo "    OK: $name"
}

# ─ responder ──────────────────────────────────────────────────────────────────
deploy_app "responder" "{
  \"location\": \"$LOCATION\",
  \"properties\": {
    \"managedEnvironmentId\": \"$ACA_ENV_ID\",
    \"configuration\": {
      \"ingress\": {\"external\": false, \"targetPort\": 8000, \"allowInsecure\": true},
      \"registries\": [{\"server\": \"$ACR_SERVER\", \"username\": \"$ACR_USER\", \"passwordSecretRef\": \"acr-password\"}],
      \"secrets\": [{\"name\": \"acr-password\", \"value\": \"$ACR_PASS\"}]
    },
    \"template\": {
      \"containers\": [{
        \"name\": \"responder\", \"image\": \"$ACR_SERVER/responder:latest\",
        \"resources\": {\"cpu\": 0.5, \"memory\": \"1Gi\"},
        \"env\": [
          {\"name\": \"OPENAI_API_KEY\", \"value\": \"$OPENAI_API_KEY\"},
          {\"name\": \"ORCHESTRATOR_URL\", \"value\": \"http://orchestrator/execute\"}
        ]
      }],
      \"scale\": {\"minReplicas\": 1, \"maxReplicas\": 1}
    }
  }
}"

# ─ orchestrator ───────────────────────────────────────────────────────────────
deploy_app "orchestrator" "{
  \"location\": \"$LOCATION\",
  \"properties\": {
    \"managedEnvironmentId\": \"$ACA_ENV_ID\",
    \"configuration\": {
      \"ingress\": {\"external\": false, \"targetPort\": 8001, \"allowInsecure\": true},
      \"registries\": [{\"server\": \"$ACR_SERVER\", \"username\": \"$ACR_USER\", \"passwordSecretRef\": \"acr-password\"}],
      \"secrets\": [{\"name\": \"acr-password\", \"value\": \"$ACR_PASS\"}]
    },
    \"template\": {
      \"volumes\": [
        {\"name\": \"documents\", \"storageType\": \"AzureFile\", \"storageName\": \"documents\"},
        {\"name\": \"output\",    \"storageType\": \"AzureFile\", \"storageName\": \"output\"}
      ],
      \"containers\": [{
        \"name\": \"orchestrator\", \"image\": \"$ACR_SERVER/orchestrator:latest\",
        \"resources\": {\"cpu\": 0.5, \"memory\": \"1Gi\"},
        \"env\": [
          {\"name\": \"OPENAI_API_KEY\",          \"value\": \"$OPENAI_API_KEY\"},
          {\"name\": \"DOCUMENTS_FOLDER\",         \"value\": \"/documents\"},
          {\"name\": \"OUTPUT_DIR\",               \"value\": \"/app/OUTPUT\"},
          {\"name\": \"CHUNKER_URL\",              \"value\": \"http://document-chunker/chunk\"},
          {\"name\": \"ANALYTICS_URL\",            \"value\": \"http://data-analytics/analyse\"},
          {\"name\": \"DOC_REVIEWER_URL\",         \"value\": \"http://document-reviewer/review\"},
          {\"name\": \"DOC_PLAN_URL\",             \"value\": \"http://document-reviewer/plan_processing\"},
          {\"name\": \"DOC_EXTRACTOR_URL\",        \"value\": \"http://document-extractor/extract\"},
          {\"name\": \"DOC_RAW_TEXT_URL\",         \"value\": \"http://document-extractor/raw_text\"},
          {\"name\": \"VARIANCE_VALIDATOR_URL\",   \"value\": \"http://variance-validator/validate_variance\"},
          {\"name\": \"VALIDATOR_URL\",            \"value\": \"http://step-validator/validate_step\"}
        ],
        \"volumeMounts\": [
          {\"mountPath\": \"/documents\",  \"volumeName\": \"documents\"},
          {\"mountPath\": \"/app/OUTPUT\", \"volumeName\": \"output\"}
        ]
      }],
      \"scale\": {\"minReplicas\": 1, \"maxReplicas\": 1}
    }
  }
}"

# ─ document-reviewer ──────────────────────────────────────────────────────────
deploy_app "document-reviewer" "{
  \"location\": \"$LOCATION\",
  \"properties\": {
    \"managedEnvironmentId\": \"$ACA_ENV_ID\",
    \"configuration\": {
      \"ingress\": {\"external\": false, \"targetPort\": 8089, \"allowInsecure\": true},
      \"registries\": [{\"server\": \"$ACR_SERVER\", \"username\": \"$ACR_USER\", \"passwordSecretRef\": \"acr-password\"}],
      \"secrets\": [{\"name\": \"acr-password\", \"value\": \"$ACR_PASS\"}]
    },
    \"template\": {
      \"volumes\": [
        {\"name\": \"documents\", \"storageType\": \"AzureFile\", \"storageName\": \"documents\"}
      ],
      \"containers\": [{
        \"name\": \"document-reviewer\", \"image\": \"$ACR_SERVER/document-reviewer:latest\",
        \"resources\": {\"cpu\": 0.5, \"memory\": \"1Gi\"},
        \"env\": [
          {\"name\": \"OPENAI_API_KEY\", \"value\": \"$OPENAI_API_KEY\"},
          {\"name\": \"LLM_MODEL\",      \"value\": \"$LLM_MODEL\"}
        ],
        \"volumeMounts\": [{\"mountPath\": \"/documents\", \"volumeName\": \"documents\"}]
      }],
      \"scale\": {\"minReplicas\": 1, \"maxReplicas\": 1}
    }
  }
}"

# ─ document-extractor ─────────────────────────────────────────────────────────
deploy_app "document-extractor" "{
  \"location\": \"$LOCATION\",
  \"properties\": {
    \"managedEnvironmentId\": \"$ACA_ENV_ID\",
    \"configuration\": {
      \"ingress\": {\"external\": false, \"targetPort\": 8090, \"allowInsecure\": true},
      \"registries\": [{\"server\": \"$ACR_SERVER\", \"username\": \"$ACR_USER\", \"passwordSecretRef\": \"acr-password\"}],
      \"secrets\": [{\"name\": \"acr-password\", \"value\": \"$ACR_PASS\"}]
    },
    \"template\": {
      \"volumes\": [
        {\"name\": \"documents\", \"storageType\": \"AzureFile\", \"storageName\": \"documents\"},
        {\"name\": \"chunks\",    \"storageType\": \"AzureFile\", \"storageName\": \"chunks\"},
        {\"name\": \"output\",    \"storageType\": \"AzureFile\", \"storageName\": \"output\"}
      ],
      \"containers\": [{
        \"name\": \"document-extractor\", \"image\": \"$ACR_SERVER/document-extractor:latest\",
        \"resources\": {\"cpu\": 1.0, \"memory\": \"2Gi\"},
        \"env\": [
          {\"name\": \"OPENAI_API_KEY\",           \"value\": \"$OPENAI_API_KEY\"},
          {\"name\": \"ANTHROPIC_API_KEY\",         \"value\": \"$ANTHROPIC_API_KEY\"},
          {\"name\": \"GOOGLE_API_KEY\",            \"value\": \"$GOOGLE_API_KEY\"},
          {\"name\": \"LLM_MODEL\",                \"value\": \"$LLM_MODEL\"},
          {\"name\": \"TAGGING_MODEL\",            \"value\": \"$TAGGING_MODEL\"},
          {\"name\": \"VISION_MODEL\",             \"value\": \"$VISION_MODEL\"},
          {\"name\": \"ASSET_EXTRACTION_MODEL\",   \"value\": \"$ASSET_EXTRACTION_MODEL\"}
        ],
        \"volumeMounts\": [
          {\"mountPath\": \"/documents\",    \"volumeName\": \"documents\"},
          {\"mountPath\": \"/output/chunks\", \"volumeName\": \"chunks\"},
          {\"mountPath\": \"/app/OUTPUT\",   \"volumeName\": \"output\"}
        ]
      }],
      \"scale\": {\"minReplicas\": 1, \"maxReplicas\": 1}
    }
  }
}"

# ─ document-chunker ───────────────────────────────────────────────────────────
deploy_app "document-chunker" "{
  \"location\": \"$LOCATION\",
  \"properties\": {
    \"managedEnvironmentId\": \"$ACA_ENV_ID\",
    \"configuration\": {
      \"ingress\": {\"external\": false, \"targetPort\": 8091, \"allowInsecure\": true},
      \"registries\": [{\"server\": \"$ACR_SERVER\", \"username\": \"$ACR_USER\", \"passwordSecretRef\": \"acr-password\"}],
      \"secrets\": [{\"name\": \"acr-password\", \"value\": \"$ACR_PASS\"}]
    },
    \"template\": {
      \"volumes\": [
        {\"name\": \"documents\", \"storageType\": \"AzureFile\", \"storageName\": \"documents\"},
        {\"name\": \"chunks\",    \"storageType\": \"AzureFile\", \"storageName\": \"chunks\"},
        {\"name\": \"output\",    \"storageType\": \"AzureFile\", \"storageName\": \"output\"}
      ],
      \"containers\": [{
        \"name\": \"document-chunker\", \"image\": \"$ACR_SERVER/document-chunker:latest\",
        \"resources\": {\"cpu\": 0.5, \"memory\": \"1Gi\"},
        \"volumeMounts\": [
          {\"mountPath\": \"/documents\",    \"volumeName\": \"documents\"},
          {\"mountPath\": \"/output/chunks\", \"volumeName\": \"chunks\"},
          {\"mountPath\": \"/app/OUTPUT\",   \"volumeName\": \"output\"}
        ]
      }],
      \"scale\": {\"minReplicas\": 1, \"maxReplicas\": 1}
    }
  }
}"

# ─ step-validator ─────────────────────────────────────────────────────────────
deploy_app "step-validator" "{
  \"location\": \"$LOCATION\",
  \"properties\": {
    \"managedEnvironmentId\": \"$ACA_ENV_ID\",
    \"configuration\": {
      \"ingress\": {\"external\": false, \"targetPort\": 8088, \"allowInsecure\": true},
      \"registries\": [{\"server\": \"$ACR_SERVER\", \"username\": \"$ACR_USER\", \"passwordSecretRef\": \"acr-password\"}],
      \"secrets\": [{\"name\": \"acr-password\", \"value\": \"$ACR_PASS\"}]
    },
    \"template\": {
      \"containers\": [{
        \"name\": \"step-validator\", \"image\": \"$ACR_SERVER/step-validator:latest\",
        \"resources\": {\"cpu\": 0.5, \"memory\": \"1Gi\"},
        \"env\": [
          {\"name\": \"GOOGLE_API_KEY\",   \"value\": \"$GOOGLE_API_KEY\"},
          {\"name\": \"OPENAI_API_KEY\",   \"value\": \"$OPENAI_API_KEY\"},
          {\"name\": \"GEMINI_MODEL\",     \"value\": \"$GEMINI_MODEL\"},
          {\"name\": \"VALIDATOR_MODEL\",  \"value\": \"$VALIDATOR_MODEL\"}
        ]
      }],
      \"scale\": {\"minReplicas\": 1, \"maxReplicas\": 1}
    }
  }
}"

# ─ data-analytics ─────────────────────────────────────────────────────────────
deploy_app "data-analytics" "{
  \"location\": \"$LOCATION\",
  \"properties\": {
    \"managedEnvironmentId\": \"$ACA_ENV_ID\",
    \"configuration\": {
      \"ingress\": {\"external\": false, \"targetPort\": 8092, \"allowInsecure\": true},
      \"registries\": [{\"server\": \"$ACR_SERVER\", \"username\": \"$ACR_USER\", \"passwordSecretRef\": \"acr-password\"}],
      \"secrets\": [{\"name\": \"acr-password\", \"value\": \"$ACR_PASS\"}]
    },
    \"template\": {
      \"containers\": [{
        \"name\": \"data-analytics\", \"image\": \"$ACR_SERVER/data-analytics:latest\",
        \"resources\": {\"cpu\": 0.5, \"memory\": \"1Gi\"},
        \"env\": [
          {\"name\": \"ANTHROPIC_API_KEY\",  \"value\": \"$ANTHROPIC_API_KEY\"},
          {\"name\": \"OPENAI_API_KEY\",     \"value\": \"$OPENAI_API_KEY\"},
          {\"name\": \"ANALYTICS_MODEL\",    \"value\": \"$ANALYTICS_MODEL\"}
        ]
      }],
      \"scale\": {\"minReplicas\": 1, \"maxReplicas\": 1}
    }
  }
}"

# ─ variance-validator ─────────────────────────────────────────────────────────
deploy_app "variance-validator" "{
  \"location\": \"$LOCATION\",
  \"properties\": {
    \"managedEnvironmentId\": \"$ACA_ENV_ID\",
    \"configuration\": {
      \"ingress\": {\"external\": false, \"targetPort\": 8093, \"allowInsecure\": true},
      \"registries\": [{\"server\": \"$ACR_SERVER\", \"username\": \"$ACR_USER\", \"passwordSecretRef\": \"acr-password\"}],
      \"secrets\": [{\"name\": \"acr-password\", \"value\": \"$ACR_PASS\"}]
    },
    \"template\": {
      \"volumes\": [
        {\"name\": \"output\", \"storageType\": \"AzureFile\", \"storageName\": \"output\"}
      ],
      \"containers\": [{
        \"name\": \"variance-validator\", \"image\": \"$ACR_SERVER/variance-validator:latest\",
        \"resources\": {\"cpu\": 0.25, \"memory\": \"0.5Gi\"},
        \"env\": [{\"name\": \"OUTPUT_DIR\", \"value\": \"/app/OUTPUT\"}],
        \"volumeMounts\": [{\"mountPath\": \"/app/OUTPUT\", \"volumeName\": \"output\"}]
      }],
      \"scale\": {\"minReplicas\": 1, \"maxReplicas\": 1}
    }
  }
}"

# ─ frontend (external ingress) ────────────────────────────────────────────────
deploy_app "frontend" "{
  \"location\": \"$LOCATION\",
  \"properties\": {
    \"managedEnvironmentId\": \"$ACA_ENV_ID\",
    \"configuration\": {
      \"ingress\": {\"external\": true, \"targetPort\": 80, \"transport\": \"http2\"},
      \"registries\": [{\"server\": \"$ACR_SERVER\", \"username\": \"$ACR_USER\", \"passwordSecretRef\": \"acr-password\"}],
      \"secrets\": [{\"name\": \"acr-password\", \"value\": \"$ACR_PASS\"}]
    },
    \"template\": {
      \"containers\": [{
        \"name\": \"frontend\", \"image\": \"$ACR_SERVER/frontend:latest\",
        \"resources\": {\"cpu\": 0.25, \"memory\": \"0.5Gi\"}
      }],
      \"scale\": {\"minReplicas\": 1, \"maxReplicas\": 1}
    }
  }
}"

# ── Step 7–8: (handled inline above) ─────────────────────────────────────────
echo "=== [7/9] (handled by az rest deploy calls) ==="
echo "=== [8/9] (storage registered at step 5) ==="
echo "=== [9/9] Summary ==="

FRONTEND_FQDN=$(az containerapp show \
    --name frontend \
    --resource-group "$RESOURCE_GROUP" \
    --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || echo "pending")

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Agent 09  —  Azure Container Apps  ✓ Deployed           ║"
echo "╠══════════════════════════════════════════════════════════╣"
printf "║  Resource Group  : %-36s ║\n" "$RESOURCE_GROUP"
printf "║  Environment     : %-36s ║\n" "$ACA_ENV"
printf "║  Registry        : %-36s ║\n" "$ACR_SERVER"
printf "║  Frontend URL    : %-36s ║\n" "https://$FRONTEND_FQDN"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Upload documents:"
echo "  az storage file upload-batch \\"
echo "    --account-name $STORAGE_ACCOUNT --account-key '<key>' \\"
echo "    --destination $DOCUMENTS_SHARE --source <local-docs-folder>"
echo ""
