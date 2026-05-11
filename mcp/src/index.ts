#!/usr/bin/env node

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { registerTools } from "./tools.js";
import { ensureRepomapInstalled } from "./repomap.js";

const REPOMAP_INSTRUCTIONS = `# RepoMap — Mandatory Repository Intelligence

Repomap tools are your PRIMARY source of codebase intelligence. grep and raw file reads are FALLBACKS, not defaults. You MUST use repomap tools for all non-trivial coding tasks.

## Core Principle

Use repomap BEFORE edits, DURING symbol investigation, and AFTER edits. It gives you structured repository context instead of guessing.

## Mandatory Rules — NO EXCEPTIONS

1. **New repository or unfamiliar area**: FIRST call \`repomap_overview\`. Do NOT start with grep or file reads.
2. **Known topic, unknown file**: FIRST call \`repomap_query\`. Do NOT grep blindly.
3. **Known file, before editing**: FIRST call \`repomap_file_detail\`, then \`repomap_impact\` with \`with_symbols: true\`. This is the cheapest way to prevent broken builds.
4. **Known symbol, before changing signature/behavior**: FIRST call \`repomap_query_symbol\`, then \`repomap_call_chain\` and/or \`repomap_refs\`. Inspect every caller before changing the signature.
5. **After every non-trivial edit**: call \`repomap_verify\`. Address every contract risk warning before claiming completion.
6. **API/route changes**: call \`repomap_routes_consumers\` BEFORE editing handlers. Know every frontend consumer that will break.
7. **State/lifecycle changes**: call \`repomap_state_map\` BEFORE touching enums, constants, or state machines.
8. **After deleting code**: call \`repomap_orphan\` then verify every high-confidence candidate with \`repomap_refs\` before actual deletion.

## Fast Default Workflow

For any non-trivial coding task, follow this order:

1. Unknown area → \`repomap_overview\` or \`repomap_query\`
2. Known file → \`repomap_file_detail\` + \`repomap_impact\` (with_symbols: true)
3. Known symbol → \`repomap_query_symbol\` + \`repomap_call_chain\` + \`repomap_refs\`
4. After editing → \`repomap_verify\`

## Post-Edit Evidence Gate

- Default: \`repomap_verify\` (add \`with_lsp: true\` when available)
- Risk-only: \`repomap_verify\` with \`quick: true\`
- Diagnostics only: \`repomap_check\`

## Hard Constraints

- \`repomap_orphan\` output is a CANDIDATE LIST, not a deletion license. Always verify with \`repomap_refs\` first. Check for dynamic references the graph cannot see: string dispatch, reflection, macros, config-driven routing.
- \`repomap_verify\` does NOT run tests — run them separately.
- \`repomap_impact\` does NOT guarantee completeness — check \`repomap_routes\` and \`repomap_refs\` for cross-boundary relationships when touching API, state, or persistence.
- When \`repomap_check\` reports "unknown", it means no diagnostic tool ran — do NOT treat this as passing.
- \`repomap_overview\` is for orientation only — do NOT repeat its output as a summary to the user.
- NEVER skip \`repomap_impact\` before editing known files.
- NEVER delete code based solely on \`repomap_orphan\` output.`;

const server = new McpServer({
  name: "repomap-mcp-server",
  version: "2.2.0",
}, {
  instructions: REPOMAP_INSTRUCTIONS,
});

registerTools(server);

async function main() {
  await ensureRepomapInstalled();
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error("Fatal error starting repomap-mcp-server:", err);
  process.exit(1);
});
