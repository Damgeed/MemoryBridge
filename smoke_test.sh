#!/usr/bin/env bash
#
# Memory Bridge — Full Smoke Test Suite
# Tests all 9 endpoints with visual output
#
# Usage: ./smoke_test.sh [base_url]
#   Default: http://localhost:8000
#
# Exit codes:
#   0 — all tests pass
#   1 — one or more tests failed
#

set -euo pipefail

BASE="${1:-http://localhost:8000}"
PASS=0
FAIL=0
TIMESTAMP=$(date +%s)
SESSION="smoke-$TIMESTAMP"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

header()  { printf "\n${CYAN}═══════════════════════════════════════════════════${NC}\n"; }
section() { printf "\n${BOLD}${YELLOW}  ▶ $1${NC}\n"; }
ok()      { printf "  ${GREEN}✓${NC} %s\n" "$1"; ((PASS++)); }
fail()    { printf "  ${RED}✗${NC} %s\n" "$1"; ((FAIL++)); }
detail()  { printf "    ${YELLOW}→${NC} %s\n" "$1"; }

call() {
  # call METHOD PATH [data]  → prints response on failure
  local method="$1" path="$2" data="${3:-}"
  local url="$BASE$path"
  local resp
  if [ -n "$data" ]; then
    resp=$(curl -sf -X "$method" "$url" -H "Content-Type: application/json" -d "$data" 2>&1) || {
      echo "    RESP: $resp"
      return 1
    }
  else
    resp=$(curl -sf -X "$method" "$url" 2>&1) || {
      echo "    RESP: $resp"
      return 1
    }
  fi
  echo "$resp"
  return 0
}

extract() {
  # extract JSON value from stdin using Python
  python3 -c "import sys,json; print(json.load(sys.stdin)['$1'])"
}

cleanup() {
  rm -f /tmp/memory_bridge_smoke_*.json
}
trap cleanup EXIT


header
echo -e "  ${BOLD}Memory Bridge — Smoke Test${NC}"
echo -e "  Base URL: ${CYAN}$BASE${NC}"
echo -e "  Session:  ${CYAN}$SESSION${NC}"
header


# ─── 1. HEALTH ────────────────────────────────────────────────

section "1. Health Check"

RESP=$(call GET /health) && {
  STATUS=$(echo "$RESP" | extract status)
  SERVICE=$(echo "$RESP" | extract service)
  [ "$STATUS" = "ok" ] && [ "$SERVICE" = "memory-bridge" ] \
    && ok "GET /health → status=$STATUS, service=$SERVICE" \
    || fail "GET /health → unexpected response: $RESP"
} || fail "GET /health → connection refused"


# ─── 2. CREATE MEMORY ─────────────────────────────────────────

section "2. Create Memory"

CREATE_DATA=$(cat <<JSON
{"session_id":"$SESSION","agent_id":"bud","key":"user_name","value":"Danny","tags":["user-preference","important"]}
JSON
)

RESP=$(call POST /memories "$CREATE_DATA") && {
  MEM_ID=$(echo "$RESP" | extract id)
  MEM_KEY=$(echo "$RESP" | extract key)
  MEM_VAL=$(echo "$RESP" | extract value)
  [ "$MEM_KEY" = "user_name" ] && [ "$MEM_VAL" = "Danny" ] \
    && ok "POST /memories → id=$MEM_ID, key=$MEM_KEY" \
    || fail "POST /memories → wrong data: $RESP"
} || fail "POST /memories → request failed"


# ─── 3. GET MEMORY ────────────────────────────────────────────

section "3. Get Memory by ID"

RESP=$(call GET "/memories/$MEM_ID") && {
  GOT_ID=$(echo "$RESP" | extract id)
  GOT_VAL=$(echo "$RESP" | extract value)
  [ "$GOT_ID" = "$MEM_ID" ] && [ "$GOT_VAL" = "Danny" ] \
    && ok "GET /memories/$MEM_ID → value=$GOT_VAL" \
    || fail "GET /memories/$MEM_ID → mismatch: $RESP"
} || fail "GET /memories/$MEM_ID → request failed"


# ─── 4. QUERY MEMORIES ────────────────────────────────────────

section "4. Query Memories"

QUERY_DATA=$(cat <<JSON
{"session_id":"$SESSION","limit":10}
JSON
)

RESP=$(call POST /memories/query "$QUERY_DATA") && {
  TOTAL=$(echo "$RESP" | extract total)
  [ "$TOTAL" -ge 1 ] \
    && ok "POST /memories/query → $TOTAL entries found for session $SESSION" \
    || fail "POST /memories/query → expected ≥1 entry, got $TOTAL"
} || fail "POST /memories/query → request failed"


# ─── 5. QUERY BY AGENT ────────────────────────────────────────

section "5. Query Memories by Agent"

AGENT_DATA=$(cat <<JSON
{"agent_id":"bud"}
JSON
)

RESP=$(call POST /memories/query "$AGENT_DATA") && {
  TOTAL=$(echo "$RESP" | extract total)
  [ "$TOTAL" -ge 1 ] \
    && ok "POST /memories/query (by agent) → $TOTAL entries for bud" \
    || fail "POST /memories/query (by agent) → expected ≥1"
} || fail "POST /memories/query (by agent) → request failed"


# ─── 6. QUERY BY TAG ──────────────────────────────────────────

section "6. Query Memories by Tags"

TAG_DATA=$(cat <<JSON
{"tags":["important"]}
JSON
)

RESP=$(call POST /memories/query "$TAG_DATA") && {
  TOTAL=$(echo "$RESP" | extract total)
  [ "$TOTAL" -ge 1 ] \
    && ok "POST /memories/query (by tag) → $TOTAL entries tagged 'important'" \
    || fail "POST /memories/query (by tag) → expected ≥1"
} || fail "POST /memories/query (by tag) → request failed"


# ─── 7. COMPLEX VALUE TYPE ────────────────────────────────────

section "7. Complex Value Types"

COMPLEX_DATA=$(cat <<JSON
{"session_id":"$SESSION","agent_id":"bud","key":"config","value":{"theme":"dark","language":"en","notifications":true,"thresholds":[1,2,3]},"tags":["config"]}
JSON
)

RESP=$(call POST /memories "$COMPLEX_DATA") && {
  CMPLX_ID=$(echo "$RESP" | extract id)
  ok "POST /memories (complex value) → id=$CMPLX_ID"
} || fail "POST /memories (complex value) → request failed"


# ─── 8. CREATE SESSION ────────────────────────────────────────

section "8. Create Session"

SESSION_DATA=$(cat <<JSON
{"session_id":"$SESSION","agent_id":"bud","metadata":{"test":"smoke","version":"0.1.0"}}
JSON
)

RESP=$(call POST /sessions "$SESSION_DATA") && {
  SID=$(echo "$RESP" | extract session_id)
  AID=$(echo "$RESP" | extract agent_id)
  [ "$SID" = "$SESSION" ] && [ "$AID" = "bud" ] \
    && ok "POST /sessions → session_id=$SID, agent=$AID" \
    || fail "POST /sessions → mismatch: $RESP"
} || fail "POST /sessions → request failed"


# ─── 9. GET SESSION ───────────────────────────────────────────

section "9. Get Session"

RESP=$(call GET "/sessions/$SESSION") && {
  SID=$(echo "$RESP" | extract session_id)
  [ "$SID" = "$SESSION" ] \
    && ok "GET /sessions/$SESSION → found" \
    || fail "GET /sessions/$SESSION → mismatch: $RESP"
} || fail "GET /sessions/$SESSION → request failed"


# ─── 10. SESSION WITH PARENT ──────────────────────────────────

section "10. Session with Parent"

PARENT_DATA=$(cat <<JSON
{"session_id":"$SESSION-sub","agent_id":"nova","parent_session_id":"$SESSION"}
JSON
)

RESP=$(call POST /sessions "$PARENT_DATA") && {
  PARENT=$(echo "$RESP" | extract parent_session_id)
  [ "$PARENT" = "$SESSION" ] \
    && ok "POST /sessions (with parent) → parent=$PARENT" \
    || fail "POST /sessions (with parent) → $RESP"
} || fail "POST /sessions (with parent) → request failed"


# ─── 11. DELETE MEMORY ────────────────────────────────────────

section "11. Delete Memory"

RESP=$(call DELETE "/memories/$MEM_ID") && {
  DELETED=$(echo "$RESP" | extract deleted)
  [ "$DELETED" = "true" ] \
    && ok "DELETE /memories/$MEM_ID → deleted=$DELETED" \
    || fail "DELETE /memories/$MEM_ID → $RESP"
} || fail "DELETE /memories/$MEM_ID → request failed"


# ─── 12. 404 ON DELETED MEMORY ────────────────────────────────

section "12. 404 on Deleted Memory"

RESP=$(call GET "/memories/$MEM_ID" 2>&1) && {
  fail "GET /memories/$MEM_ID → should 404 but got: $RESP"
} || {
  # curl -f returns exit code 22 on HTTP 4xx/5xx
  ok "GET /memories/$MEM_ID → 404 Not Found (expected)"
}


# ─── 13. HANDOFF PREPARE ──────────────────────────────────────

section "13. Handoff — Prepare"

HANDOFF_DATA=$(cat <<JSON
{"from_agent_id":"bud","to_agent_id":"nova","session_id":"$SESSION","context":{},"handoff_type":"summary"}
JSON
)

RESP=$(call POST /handoff/prepare "$HANDOFF_DATA") && {
  SUCCESS=$(echo "$RESP" | extract success)
  SUMMARY=$(echo "$RESP" | extract summary)
  WARNINGS=$(echo "$RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['warnings']))")
  [ "$SUCCESS" = "true" ] \
    && ok "POST /handoff/prepare → success, $SUMMARY, $WARNINGS warnings" \
    || fail "POST /handoff/prepare → $RESP"
} || fail "POST /handoff/prepare → request failed"


# ─── 14. HANDOFF EXECUTE ──────────────────────────────────────

section "14. Handoff — Execute"

EXEC_DATA=$(cat <<JSON
{"from_agent_id":"bud","to_agent_id":"nova","session_id":"$SESSION","context":{},"handoff_type":"summary"}
JSON
)

RESP=$(call POST /handoff/execute "$EXEC_DATA") && {
  SUCCESS=$(echo "$RESP" | extract success)
  SUMMARY=$(echo "$RESP" | extract summary)
  [ "$SUCCESS" = "true" ] \
    && ok "POST /handoff/execute → success, $SUMMARY" \
    || fail "POST /handoff/execute → $RESP"
} || fail "POST /handoff/execute → request failed"


# ─── 15. VERIFY HANDOFF RECEIVED ──────────────────────────────

section "15. Verify Handoff — Nova received context"

VERIFY_DATA=$(cat <<JSON
{"agent_id":"nova"}
JSON
)

RESP=$(call POST /memories/query "$VERIFY_DATA") && {
  TOTAL=$(echo "$RESP" | extract total)
  [ "$TOTAL" -ge 1 ] \
    && ok "POST /memories/query (nova) → $TOTAL memories received from handoff" \
    || fail "POST /memories/query (nova) → expected ≥1 handoff memories"
} || fail "POST /memories/query (nova) → request failed"


# ─── 16. GUARDRAILS — SENSITIVE KEYS ──────────────────────────

section "16. Guardrails — Sensitive Key Detection"

GUARD_DATA=$(cat <<JSON
{"from_agent_id":"bud","to_agent_id":"nova","session_id":"$SESSION","context":{"api_key":"sk-1234","password":"hunter2","theme":"dark"},"handoff_type":"summary"}
JSON
)

RESP=$(call POST /handoff/prepare "$GUARD_DATA") && {
  CTX=$(echo "$RESP" | python3 -c "import sys,json; ctx=json.load(sys.stdin)['context']; print(list(ctx.keys()))")
  WARN_COUNT=$(echo "$RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['warnings']))")
  SUCCESS=$(echo "$RESP" | extract success)
  # Context should NOT contain api_key or password
  if echo "$CTX" | grep -qv "api_key\|password"; then
    ok "POST /handoff/prepare → sensitive keys blocked, $WARN_COUNT warnings (ctx=$CTX)"
  else
    fail "POST /handoff/prepare → sensitive keys leaked: $CTX"
  fi
} || fail "POST /handoff/prepare (sensitive) → request failed"


# ─── 17. GUARDRAILS — EMPTY HANDOFF ──────────────────────────

section "17. Guardrails — Empty Handoff (no memories)"

EMPTY_DATA=$(cat <<JSON
{"from_agent_id":"ghost","to_agent_id":"nova","session_id":"nonexistent","context":{},"handoff_type":"summary"}
JSON
)

RESP=$(call POST /handoff/prepare "$EMPTY_DATA") && {
  SUCCESS=$(echo "$RESP" | extract success)
  [ "$SUCCESS" = "false" ] \
    && ok "POST /handoff/prepare (ghost) → correctly returns success=false" \
    || fail "POST /handoff/prepare (ghost) → expected false, got $SUCCESS"
} || fail "POST /handoff/prepare (ghost) → request failed"


# ─── 18. CLEANUP — DELETE REMAINING ───────────────────────────

section "18. Cleanup — Delete Remaining Test Data"

# Delete the complex value memory
RESP=$(call DELETE "/memories/$CMPLX_ID" 2>&1) && {
  ok "DELETE /memories/$CMPLX_ID (cleanup)"
} || {
  detail "Already deleted or not found — skipping"
}


# ─── 19. HEALTH AFTER OPERATIONS ──────────────────────────────

section "19. Health Check (post-operations)"

RESP=$(call GET /health) && {
  STATUS=$(echo "$RESP" | extract status)
  [ "$STATUS" = "ok" ] \
    && ok "GET /health → still healthy after 18 operations" \
    || fail "GET /health → degraded: $RESP"
} || fail "GET /health → server died"


# ─── SUMMARY ──────────────────────────────────────────────────

header
TOTAL=$((PASS + FAIL))
echo -e "\n${BOLD}  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}  (${TOTAL} total)"
echo -e "  Session: ${CYAN}$SESSION${NC}"
echo -e "  Server:  ${CYAN}$BASE${NC}"
header
echo ""

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
