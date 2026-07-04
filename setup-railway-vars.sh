#!/usr/bin/env bash
set -euo pipefail

BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

echo -e "${BOLD}Faro Protocol — Railway Environment Setup${NC}"
echo ""

if ! command -v railway &>/dev/null; then
  echo -e "${RED}railway CLI not found: npm install -g @railway/cli && railway login${NC}"
  exit 1
fi

set_var() {
  local k="$1" v="$2"
  railway variables set "${k}=${v}" --yes 2>/dev/null \
    && echo -e "  ${GREEN}✓${NC} $k" \
    || echo -e "  ${YELLOW}~${NC} $k (set manually if this failed)"
}

prompt_set() {
  local k="$1" hint="$2" secret="${3:-n}"
  echo -n "  $k ($hint): "
  if [[ "$secret" == "y" ]]; then read -rs v; echo ""; else read -r v; fi
  [[ -n "$v" ]] && set_var "$k" "$v" || echo -e "  ${YELLOW}SKIP${NC} $k"
}

echo -e "${BOLD}[1] Required — Supabase${NC}"
echo "  Find at: Supabase → Project Settings → API"
prompt_set "SUPABASE_URL"    "https://xxxx.supabase.co"
prompt_set "SUPABASE_KEY"    "service_role key"                        y
prompt_set "SUPABASE_DB_URL" "postgresql://postgres.xxxx:pass@aws.../postgres" y

echo ""
echo -e "${BOLD}[2] Required — Satellite Data${NC}"
prompt_set "CDS_API_KEY"           "UID:APIkey from cds.climate.copernicus.eu" y
prompt_set "NASA_EARTHDATA_USER"   "Earthdata username"
prompt_set "NASA_EARTHDATA_PASS"   "Earthdata password"                         y
prompt_set "GCS_BUCKET"            "faro-dprvi-exports"
prompt_set "GCS_SERVICE_ACCOUNT_JSON" "base64-encoded service account JSON"    y

echo ""
echo -e "${BOLD}[3] Required — AI + GitHub${NC}"
prompt_set "ANTHROPIC_API_KEY" "sk-ant-..."  y
prompt_set "GITHUB_TOKEN"      "ghp_..."     y

echo ""
echo -e "${BOLD}[4] Alerts — Slack + Email${NC}"
prompt_set "SLACK_WEBHOOK_URL" "https://hooks.slack.com/services/..." y
prompt_set "GMAIL_USER"        "protocolfaro@gmail.com"
prompt_set "GMAIL_APP_PASS"    "xxxx xxxx xxxx xxxx"                  y
prompt_set "ALERT_EMAIL"       "protocolfaro@gmail.com"

echo ""
echo -e "${BOLD}[5] Security${NC}"
CSRF=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)
set_var "CSRF_SECRET"    "$CSRF"
set_var "FARO_VENUE_ID"  "velez"
prompt_set "FARO_TENANT_ID" "velez (or new tenant id)"

echo ""
echo -e "${BOLD}[6] PIN hash (SHA-256 of admin PIN)${NC}"
echo -n "  Admin PIN (Enter to skip): "
read -rs PIN; echo ""
if [[ -n "$PIN" ]]; then
  HASH=$(echo -n "$PIN" | openssl dgst -sha256 | awk '{print $2}')
  set_var "VELEZ_PIN_HASH" "$HASH"
  echo -e "  ${GREEN}PIN hash set — plain PIN not stored${NC}"
fi

echo ""
echo -e "${BOLD}[7] Optional — External services${NC}"
prompt_set "RAILWAY_URL"       "https://faroprotocol-production-45fd.up.railway.app"
prompt_set "HEALTHCHECKS_UUID" "uuid from healthchecks.io"

echo ""
echo -e "${BOLD}Required GitHub Secrets (set at github.com → repo → Settings → Secrets):${NC}"
echo "  SUPABASE_URL"
echo "  SUPABASE_KEY"
echo "  SUPABASE_DB_URL"
echo "  CDS_API_KEY"
echo "  NASA_EARTHDATA_USER"
echo "  NASA_EARTHDATA_PASS"
echo "  GCS_BUCKET"
echo "  GCS_SERVICE_ACCOUNT_JSON"
echo "  ANTHROPIC_API_KEY"
echo "  GITHUB_TOKEN"
echo "  SLACK_WEBHOOK_URL"
echo "  RAILWAY_URL"
echo ""
echo -e "${GREEN}Setup complete. Push to main to trigger infra-deploy.yml.${NC}"
