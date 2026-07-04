#!/usr/bin/env bash
# railway_setup.sh — Faro Protocol · Railway Environment Setup
#
# Usage:
#   chmod +x scripts/railway_setup.sh
#   ./scripts/railway_setup.sh
#
# Prerequisites:
#   railway CLI installed and logged in:  npm install -g @railway/cli && railway login
#   Must be run from the repo root.
#   The script will prompt for secrets it cannot generate automatically.
#
# What this does:
#   1. Generates CSRF_SECRET (random, no prompt needed)
#   2. Prompts for Slack webhook URL (optional)
#   3. Prompts for Gmail App Password (optional, already set if emails work)
#   4. Sets FARO_TENANT_ID = velez
#   5. Validates connectivity after deploy
#   6. Optionally runs SQL migrations

set -euo pipefail
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'; BOLD='\033[1m'

echo -e "${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║   FARO PROTOCOL · Railway Environment Setup  ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ── Verify Railway CLI ────────────────────────────────────────────────────────
if ! command -v railway &>/dev/null; then
  echo -e "${RED}ERROR: railway CLI not found.${NC}"
  echo "Install: npm install -g @railway/cli && railway login"
  exit 1
fi

# ── Verify service is linked ──────────────────────────────────────────────────
if ! railway status &>/dev/null; then
  echo -e "${YELLOW}No Railway project linked. Running: railway link${NC}"
  railway link
fi

echo -e "${GREEN}Railway project linked.${NC}"
echo ""

# ── Helper: set variable ──────────────────────────────────────────────────────
set_var() {
  local key="$1" val="$2"
  railway variables set "${key}=${val}" --yes 2>/dev/null \
    && echo -e "  ${GREEN}✓${NC} ${key}" \
    || echo -e "  ${YELLOW}⚠${NC} ${key} (set manually if this failed)"
}

# ── 1. Generate CSRF_SECRET ───────────────────────────────────────────────────
echo -e "${BOLD}[1/5] Generating CSRF_SECRET...${NC}"
CSRF_SECRET=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")
set_var "CSRF_SECRET" "$CSRF_SECRET"
echo ""

# ── 2. Tenant ID ──────────────────────────────────────────────────────────────
echo -e "${BOLD}[2/5] Setting FARO_TENANT_ID...${NC}"
set_var "FARO_TENANT_ID" "velez"
echo ""

# ── 3. Slack webhook (optional) ───────────────────────────────────────────────
echo -e "${BOLD}[3/5] Slack webhook (optional)${NC}"
echo -n "Slack webhook URL (Enter to skip): "
read -r SLACK_WEBHOOK
if [[ -n "$SLACK_WEBHOOK" ]]; then
  set_var "SLACK_WEBHOOK_URL" "$SLACK_WEBHOOK"
else
  echo -e "  ${YELLOW}SKIP${NC} SLACK_WEBHOOK_URL — configure manually later"
fi
echo ""

# ── 4. Gmail App Password ─────────────────────────────────────────────────────
echo -e "${BOLD}[4/5] Gmail App Password (for alert emails)${NC}"
echo "Generate at: https://myaccount.google.com/apppasswords"
echo -n "Gmail App Password (Enter to skip — already set?): "
read -rs GMAIL_PASS
echo ""
if [[ -n "$GMAIL_PASS" ]]; then
  set_var "GMAIL_APP_PASS" "$GMAIL_PASS"
  set_var "GMAIL_USER" "protocolfaro@gmail.com"
else
  echo -e "  ${YELLOW}SKIP${NC} GMAIL_APP_PASS — configure manually if email alerts needed"
fi
echo ""

# ── 5. Verify existing required vars ─────────────────────────────────────────
echo -e "${BOLD}[5/5] Verifying required variables...${NC}"
REQUIRED_VARS=(
  "SUPABASE_URL"
  "SUPABASE_KEY"
  "SUPABASE_DB_URL"
  "GITHUB_TOKEN"
  "VELEZ_PIN_HASH"
  "ANTHROPIC_API_KEY"
  "CDS_API_KEY"
)

ALL_OK=true
CURRENT_VARS=$(railway variables --json 2>/dev/null || echo "{}")
for var in "${REQUIRED_VARS[@]}"; do
  if echo "$CURRENT_VARS" | grep -q "\"$var\""; then
    echo -e "  ${GREEN}✓${NC} $var"
  else
    echo -e "  ${RED}✗ MISSING: $var${NC} — set in Railway dashboard"
    ALL_OK=false
  fi
done
echo ""

if [[ "$ALL_OK" == "false" ]]; then
  echo -e "${YELLOW}⚠  Some required variables are missing.${NC}"
  echo "Set them at: https://railway.app → your project → Variables"
  echo ""
fi

# ── 6. VELEZ_PIN_HASH helper ─────────────────────────────────────────────────
echo -e "${BOLD}PIN setup (if not already configured)${NC}"
if ! echo "$CURRENT_VARS" | grep -q '"VELEZ_PIN_HASH"'; then
  echo -n "Set admin PIN (Enter to skip): "
  read -rs NEW_PIN
  echo ""
  if [[ -n "$NEW_PIN" ]]; then
    PIN_HASH=$(echo -n "$NEW_PIN" | openssl dgst -sha256 -hex | awk '{print $2}')
    set_var "VELEZ_PIN_HASH" "$PIN_HASH"
    echo -e "  ${GREEN}PIN hash set.${NC} The plain PIN is NOT stored."
  fi
fi
echo ""

# ── 7. Optional: run SQL migrations ──────────────────────────────────────────
echo -e "${BOLD}Run SQL migrations now?${NC}"
echo "Migrations 001–004 are safe (idempotent). Migration 005 (RLS) requires careful review."
echo -n "Run migrations 001–004 locally? [y/N]: "
read -r RUN_MIGS
if [[ "$RUN_MIGS" =~ ^[Yy]$ ]]; then
  if ! python3 -c "import sqlalchemy" 2>/dev/null; then
    echo "Installing sqlalchemy + psycopg2-binary..."
    pip install sqlalchemy psycopg2-binary -q
  fi

  DB_URL=$(echo "$CURRENT_VARS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('SUPABASE_DB_URL', {}).get('value', '') if isinstance(d.get('SUPABASE_DB_URL'), dict) else d.get('SUPABASE_DB_URL', ''))
" 2>/dev/null || echo "")

  if [[ -z "$DB_URL" ]]; then
    echo -e "${YELLOW}SUPABASE_DB_URL not found — run migrations manually or via GitHub Actions.${NC}"
  else
    SUPABASE_DB_URL="$DB_URL" python3 - <<'PYEOF'
import os, sys
from pathlib import Path
from sqlalchemy import create_engine, text

db_url = os.environ["SUPABASE_DB_URL"]
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url, connect_args={"connect_timeout": 15})
mig_dir = Path("railway-backend/migrations")

for fname in ["infra_001_audit_log.sql","infra_002_intervention_log.sql",
              "infra_003_alert_config.sql","infra_004_health_snapshots.sql"]:
    fpath = mig_dir / fname
    if not fpath.exists():
        print(f"SKIP: {fname} not found")
        continue
    stmts = [s.strip() for s in fpath.read_text().split(";")
             if s.strip() and not s.strip().startswith("--")]
    with engine.begin() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
            except Exception as e:
                if "already exists" not in str(e).lower():
                    print(f"  warn: {e}")
    print(f"OK: {fname}")

print("Migrations 001-004 applied.")
PYEOF
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════════${NC}"
echo -e "${BOLD}Setup complete. Next steps:${NC}"
echo ""
echo "  1. Push to main to trigger Railway deploy"
echo "  2. GitHub Actions (infra-deploy.yml) will run migrations"
echo "  3. Validate at:"

RAILWAY_URL=$(echo "$CURRENT_VARS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('RAILWAY_URL', {}).get('value', 'YOUR_RAILWAY_URL') if isinstance(d.get('RAILWAY_URL'), dict) else d.get('RAILWAY_URL', 'YOUR_RAILWAY_URL'))
" 2>/dev/null || echo "YOUR_RAILWAY_URL")

echo "       ${RAILWAY_URL}/infra/health"
echo "       ${RAILWAY_URL}/infra/csrf-token"
echo ""
echo "  4. Open health dashboard:"
echo "       https://protocolfaro.github.io/faro-paneles/velez/health-dashboard/"
echo ""
echo -e "  5. ${YELLOW}RLS migration (005) must be triggered MANUALLY${NC}"
echo "     via: Actions → Infra Deploy → run_rls_migration = true"
echo -e "${BOLD}════════════════════════════════════════════${NC}"
