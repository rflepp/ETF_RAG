#!/usr/bin/env bash
# One-time GCP setup for the agent API on Cloud Run (.github/workflows/deploy-agent.yml).
#   PROJECT_ID=my-project GITHUB_REPO=owner/etf_rag [REGION=europe-west6] tools/gcp-setup.sh
# Creates: Artifact Registry repo, a deployer and a runtime service account, Secret Manager secrets, and a Workload
# Identity Federation provider so GitHub Actions of GITHUB_REPO can deploy without a stored key. Safe to re-run.
set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}" "${GITHUB_REPO:?set GITHUB_REPO (owner/name)}"
REGION=${REGION:-europe-west6}   # Zurich
POOL=github
DEPLOY_SA="etf-agent-deploy@$PROJECT_ID.iam.gserviceaccount.com"
RUNTIME_SA="etf-agent-run@$PROJECT_ID.iam.gserviceaccount.com"
SECRETS=(mistral-api-key qdrant-api-key langsmith-api-key agent-api-tokens)

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com

gcloud artifacts repositories describe etf-compass --location="$REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create etf-compass --repository-format=docker --location="$REGION"

for sa in etf-agent-deploy etf-agent-run; do
  gcloud iam service-accounts describe "$sa@$PROJECT_ID.iam.gserviceaccount.com" >/dev/null 2>&1 ||
    gcloud iam service-accounts create "$sa"
done

# deployer: push images, deploy the service, run it as the runtime account
for role in roles/run.admin roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$DEPLOY_SA" --role="$role" \
    --condition=None >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" --member="serviceAccount:$DEPLOY_SA" \
  --role=roles/iam.serviceAccountUser >/dev/null

# runtime: read its secrets, nothing else
for s in "${SECRETS[@]}"; do
  gcloud secrets describe "$s" >/dev/null 2>&1 || gcloud secrets create "$s" --replication-policy=automatic
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$RUNTIME_SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done

# keyless GitHub Actions -> deployer, restricted to this repository
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools create "$POOL" --location=global
gcloud iam workload-identity-pools providers describe github --location=global --workload-identity-pool="$POOL" \
  >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools providers create-oidc github --location=global --workload-identity-pool="$POOL" \
    --issuer-uri=https://token.actions.githubusercontent.com \
    --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository \
    --attribute-condition="assertion.repository=='$GITHUB_REPO'"
NUM=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$NUM/locations/global/workloadIdentityPools/$POOL/attribute.repository/$GITHUB_REPO" \
  >/dev/null

cat <<EOF

Done. Next:
1. Secret values (each needs one version before the first deploy):
     printf %s "\$MISTRAL_API_KEY" | gcloud secrets versions add mistral-api-key --data-file=-
   likewise qdrant-api-key, langsmith-api-key and agent-api-tokens (comma-separated bearer tokens).
2. GitHub repository variables (Settings > Secrets and variables > Actions > Variables):
     GCP_PROJECT_ID   = $PROJECT_ID
     GCP_REGION       = $REGION
     GCP_WIF_PROVIDER = projects/$NUM/locations/global/workloadIdentityPools/$POOL/providers/github
     GCP_DEPLOY_SA    = $DEPLOY_SA
     GCP_RUNTIME_SA   = $RUNTIME_SA
     QDRANT_URL       = https://<cluster>.cloud.qdrant.io
   and the repository secret QDRANT_API_KEY for the monthly index refresh.
3. Run the "Deploy agent API (Cloud Run)" workflow.
EOF
