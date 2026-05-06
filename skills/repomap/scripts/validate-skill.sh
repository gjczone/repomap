#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="${1:-.}"
SKILL_MD="$SKILL_DIR/SKILL.md"
COMMAND_MAP="$SKILL_DIR/references/command-map.md"
PROMPT_EXAMPLES="$SKILL_DIR/references/prompt-examples.md"
ERRORS=0
SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

err() {
  local line="$1"
  local expected="$2"
  local actual="$3"
  echo "${SCRIPT_PATH}:${line}:${expected}:${actual}"
  ERRORS=$((ERRORS + 1))
}

[ -f "$SKILL_MD" ] || { err "${LINENO}" "skill_md_exists" "$SKILL_MD missing"; exit 1; }
[ -f "$COMMAND_MAP" ] || err "${LINENO}" "reference_exists=references/command-map.md" "missing"
[ -f "$PROMPT_EXAMPLES" ] || err "${LINENO}" "reference_exists=references/prompt-examples.md" "missing"

first_line="$(head -n 1 "$SKILL_MD")"
[ "$first_line" = "---" ] || err "${LINENO}" "frontmatter_first_line=---" "$first_line"
closing_line="$(awk 'NR>1 && $0=="---" { print NR; exit }' "$SKILL_MD")"
[ -n "$closing_line" ] || err "${LINENO}" "frontmatter_closing_delimiter" "missing"

grep -Eq '^name: repomap$' "$SKILL_MD" || err "${LINENO}" "name_field" "invalid_or_missing"
grep -Eq "^description: ['\"]?Use (when|proactively) " "$SKILL_MD" || err "${LINENO}" "description_prefix=Use_when_or_Use_proactively" "missing"

for heading in \
  "## Command selection" \
  "## Decision rules" \
  "## Boundaries" \
  "## References"; do
  grep -Fx "$heading" "$SKILL_MD" >/dev/null || err "${LINENO}" "heading=${heading}" "missing"
done

required_texts=(
  "overview"
  "query"
  "file-detail"
  "impact --with-symbols"
  "query-symbol"
  "call-chain"
  "refs"
  "verify --quick"
  "repomap routes"
  "diagnostics --project"
  "lsp doctor"
  "hotspots"
  "git-history"
  "orphan"
  "cache save"
  "diff --project"
  "build-binary"
  "exit_code != 0"
  "obj.method()"
  "getMetadata"
  "AI agent"
  "Claude Code LSP"
  "--with-lsp"
  "supporting file"
)
for required in "${required_texts[@]}"; do
  grep -F -- "$required" "$SKILL_MD" >/dev/null || err "${LINENO}" "required_text=${required}" "missing_in_skill"
done

for required in \
  "verify --quick" \
  "routes --json" \
  "diagnostics" \
  "cache save" \
  "diff" \
  "supporting file"; do
  grep -F -- "$required" "$COMMAND_MAP" >/dev/null || err "${LINENO}" "command_map_required=${required}" "missing"
done

for required in \
  "verify --quick" \
  "routes" \
  "diagnostics" \
  "cache save" \
  "repomap diff --project"; do
  grep -F -- "$required" "$PROMPT_EXAMPLES" >/dev/null || err "${LINENO}" "prompt_examples_required=${required}" "missing"
done

if grep -R -nE 'repomap[[:space:]]+diff-risk|`diff-risk` \||choose `repomap diff-risk|cache[[:space:]]+save\|load|repomap[[:space:]]+cache[[:space:]]+load|choose `repomap cache load' \
  "$SKILL_MD" "$COMMAND_MAP" "$PROMPT_EXAMPLES"; then
  err "${LINENO}" "no_public_diff-risk_or_cache-load_recommendations" "found"
fi

if [ "$ERRORS" -gt 0 ]; then
  echo "FAIL:${SCRIPT_PATH}:errors=${ERRORS}"
  exit 1
fi

echo "PASS:${SCRIPT_PATH}:errors=0"
