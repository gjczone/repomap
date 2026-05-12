import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { runRepomap, parseJsonOutput } from "./repomap.js";

const ProjectPathSchema = z.string()
  .describe("Absolute path to the project root directory");

const MaxFilesSchema = z.number().int().min(1).max(50000).optional()
  .describe("Maximum number of source files to scan (default: 8000)");

function toolError(message: string) {
  return {
    content: [{ type: "text" as const, text: `Error: ${message}` }],
    isError: true,
  };
}

function textResult(output: string) {
  return { content: [{ type: "text" as const, text: output }] };
}

function jsonResult(output: string) {
  const data = parseJsonOutput(output);
  return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
}

export function registerTools(server: McpServer): void {

  server.registerTool(
    "repomap_scan",
    {
      title: "Scan Repository",
      description:
        "Build the symbol graph via tree-sitter AST parsing. " +
        "MANDATORY first step — this caches the repository structure that ALL other repomap tools depend on. " +
        "Run this before overview, query, or impact when you haven't scanned yet. " +
        "Skip only if you just ran it and haven't changed branches.",
      inputSchema: {
        project: ProjectPathSchema,
        max_files: MaxFilesSchema,
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, max_files }) => {
      try {
        const output = await runRepomap("scan", { project, max_files });
        return textResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_overview",
    {
      title: "Project Overview",
      description:
        "Complete project map: entry points, module clusters, hotspots, key symbols (PageRank), reading order. " +
        "MANDATORY for any new or unfamiliar repository — run this BEFORE your first grep or file read. " +
        "Also use when returning to a project after time away. " +
        "Do NOT repeat its output as a summary to the user — use it to plan your next action, then move on to query or file_detail on the top candidates.",
      inputSchema: {
        project: ProjectPathSchema,
        max_files: MaxFilesSchema,
        max_chars: z.number().int().min(1000).optional()
          .describe("Maximum overview text size (default: 16000)"),
        with_heat: z.boolean().optional()
          .describe("Mark files changed in the last 30 days with [HOT]"),
        with_co_change: z.boolean().optional()
          .describe("Include Git co-change coupling section"),
        granularity: z.enum(["full", "medium", "compact", "auto"]).optional()
          .describe("Report granularity (default: auto)"),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, max_files, max_chars, with_heat, with_co_change, granularity }) => {
      try {
        const output = await runRepomap("overview", {
          project, max_files, max_chars,
          with_heat, with_co_change, granularity,
        }, true);
        return jsonResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_call_chain",
    {
      title: "Symbol Call Chain",
      description:
        "Callers and callees of a symbol with configurable depth. " +
        "MANDATORY before changing any function/class/method signature, return type, or behavior. " +
        "Shows every caller you might break and every dependency you rely on. " +
        "When the chain is deep, focus on DIRECT callers first — they're the ones that will break immediately. " +
        "Use AFTER query_symbol to confirm you found the right definition.",
      inputSchema: {
        project: ProjectPathSchema,
        symbol: z.string().describe("Symbol name to analyze (e.g. function or class name)"),
        file_path: z.string().optional()
          .describe("Relative file path to disambiguate when symbol name exists in multiple files"),
        direction: z.enum(["callers", "callees", "both"]).optional()
          .describe("Direction of call chain traversal (default: both)"),
        depth: z.number().int().min(1).max(10).optional()
          .describe("Traversal depth (default: 3)"),
        max_files: MaxFilesSchema,
        max_chars: z.number().int().min(500).optional()
          .describe("Maximum text output size (default: 4000)"),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, symbol, file_path, direction, depth, max_files, max_chars }) => {
      try {
        const output = await runRepomap("call-chain", {
          project, symbol, file_path, direction, depth, max_files, max_chars,
        }, true);
        return jsonResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_query_symbol",
    {
      title: "Query Symbol",
      description:
        "Find where a symbol (function, class, method, variable) is defined. Supports exact and fuzzy matching. " +
        "MANDATORY before editing, renaming, or changing the signature of ANY named symbol. " +
        "Use this to confirm you have the right definition before acting on it. " +
        "Add file_path when the same name exists in multiple files. " +
        "After finding the symbol, follow up with call_chain or refs before making changes.",
      inputSchema: {
        project: ProjectPathSchema,
        symbol: z.string().describe("Symbol name to search for"),
        file_path: z.string().optional()
          .describe("Optional relative file path filter to narrow results"),
        max_files: MaxFilesSchema,
        max_chars: z.number().int().min(500).optional()
          .describe("Maximum text output size (default: 4000)"),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, symbol, file_path, max_files, max_chars }) => {
      try {
        const output = await runRepomap("query-symbol", {
          project, symbol, file_path, max_files, max_chars,
        });
        return textResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_query",
    {
      title: "Topic Keyword Search",
      description:
        "Search by business topic or concept keyword to find relevant files and symbols. " +
        "MANDATORY when the task describes a FEATURE or CONCEPT but no specific file or symbol name. " +
        "Use this INSTEAD OF grep — it understands domain synonyms and scores results by relevance, not just string matching. " +
        "Examples: 'authentication', 'payment processing', 'websocket handler', 'rate limiting'. " +
        "After query, read the top candidate files before editing — query results are starting points, not confirmed locations.",
      inputSchema: {
        project: ProjectPathSchema,
        query: z.string().describe("Topic keyword or phrase to search for"),
        max_files_result: z.number().int().min(1).max(100).optional()
          .describe("Maximum number of result files (default: 20)"),
        max_symbols: z.number().int().min(1).max(200).optional()
          .describe("Maximum number of result symbols (default: 40)"),
        no_tests: z.boolean().optional()
          .describe("Exclude test files from results"),
        paths: z.string().optional()
          .describe("Comma-separated directory prefixes to limit search scope"),
        exclude: z.string().optional()
          .describe("Comma-separated directory prefixes to exclude from search"),
        context_lines: z.number().int().min(0).max(10).optional()
          .describe("Context lines around matched code (default: 2, 0 to disable)"),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, query, max_files_result, max_symbols, no_tests, paths, exclude, context_lines }) => {
      try {
        const output = await runRepomap("query", {
          project, query,
          max_files: max_files_result,
          max_symbols, no_tests, paths, exclude, context_lines,
        }, true);
        return jsonResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_impact",
    {
      title: "Change Impact Analysis",
      description:
        "Pre-edit blast radius analysis — the cheapest way to prevent broken builds. " +
        "MANDATORY before EVERY non-trivial file edit. Shows: affected files with confidence levels, " +
        "related tests, risk assessment (low/medium/high), key symbols, and recommended reading order. " +
        "ALWAYS use with_symbols: true for full context — it adds key symbols, reading order, and LSP hints. " +
        "Read the 'Read Next' files BEFORE editing. Run the suggested related tests AFTER editing. " +
        "Note: impact does not guarantee completeness — use refs and routes for cross-boundary checks when touching API, state, or persistence.",
      inputSchema: {
        project: ProjectPathSchema,
        files: z.array(z.string()).min(1)
          .describe("List of relative file paths to analyze for change impact"),
        max_affected_files: z.number().int().min(1).max(100).optional()
          .describe("Maximum number of affected files to show (default: 20)"),
        with_symbols: z.boolean().optional()
          .describe("Include key symbols, reading order, and LSP availability hint"),
        depth: z.number().int().min(1).max(5).optional()
          .describe("Transitive impact depth: 1=direct, 2=one hop out (default: 1)"),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, files, max_affected_files, with_symbols, depth }) => {
      try {
        const output = await runRepomap("impact", {
          project, files, max_files: max_affected_files,
          with_symbols, depth,
        }, true);
        return jsonResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_verify",
    {
      title: "Post-Edit Verification",
      description:
        "Post-edit evidence gate — your FINAL CHECK before claiming a task is complete. " +
        "MANDATORY after every non-trivial code change. Aggregates: git changes, risk assessment, " +
        "contract risk warnings, suggested tests, compiler/LSP diagnostics, and optional graph diff. " +
        "Address EVERY contract risk warning before final handoff. " +
        "IMPORTANT: verify does NOT run tests — you must run them separately. " +
        "When diagnostics or graph diff show SKIPPED, state the limitation in your completion report. " +
        "Use quick: true for risk-only mode (skips compiler/LSP, faster). " +
        "Use with_lsp: true when a language server is available for compiler-grade diagnostics on changed files.",
      inputSchema: {
        project: ProjectPathSchema,
        types: z.array(z.enum(["typescript", "rust", "python", "go", "javascript"])).optional()
          .describe("Explicit project types to check (auto-detected if omitted)"),
        max_issues: z.number().int().min(1).max(200).optional()
          .describe("Maximum issues per diagnostic tool (default: 50)"),
        with_lsp: z.boolean().optional()
          .describe("Include focused LSP diagnostics for changed files"),
        with_diff: z.boolean().optional()
          .describe("Include graph diff when a cache baseline exists"),
        quick: z.boolean().optional()
          .describe("Risk-only mode for current Git changes; skips compiler and LSP checks"),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false,
      },
    },
    async ({ project, types, max_issues, with_lsp, with_diff, quick }) => {
      try {
        const output = await runRepomap("verify", {
          project, types, max_issues,
          with_lsp, with_diff, quick,
        }, true);
        return jsonResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_file_detail",
    {
      title: "File Detail",
      description:
        "All symbols in a single file: signatures, visibility, PageRank scores, and cross-references. " +
        "MANDATORY before reading or editing any non-trivial file — use this to understand structure BEFORE raw reading. " +
        "For edits, always follow file_detail with impact (with_symbols: true) to get the blast radius. " +
        "Tune max_symbols/max_chars when output is too large for the file size.",
      inputSchema: {
        project: ProjectPathSchema,
        file_path: z.string().describe("Relative file path to inspect"),
        max_symbols: z.number().int().min(1).max(100).optional()
          .describe("Maximum symbols to expand in output (default: 12, auto-adjusted for large files)"),
        max_files: MaxFilesSchema,
        max_chars: z.number().int().min(500).optional()
          .describe("Maximum text output size (default: 6000)"),
        with_lsp: z.boolean().optional()
          .describe("Include hierarchical LSP symbol tree with NamePath (e.g. ClassName/method_name)"),
        lsp_timeout: z.number().optional()
          .describe("Seconds to wait for LSP responses (default: 8.0)"),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, file_path, max_symbols, max_files, max_chars, with_lsp, lsp_timeout }) => {
      try {
        const output = await runRepomap("file-detail", {
          project, file_path, max_symbols, max_files, max_chars, with_lsp, lsp_timeout,
        });
        return textResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_hotspots",
    {
      title: "Hotspot Files",
      description:
        "Highest-density files ranked by symbol count and complexity. " +
        "Use SPARINGLY — only after overview or query when you need to identify the most complex files in an area. " +
        "Hotspots are where bugs and refactoring effort concentrate — pay attention to high-risk files.",
      inputSchema: {
        project: ProjectPathSchema,
        limit: z.number().int().min(1).max(100).optional()
          .describe("Number of files to return (default: 15)"),
        max_files: MaxFilesSchema,
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, limit, max_files }) => {
      try {
        const output = await runRepomap("hotspots", { project, limit, max_files });
        return textResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_cache_save",
    {
      title: "Save Graph Baseline",
      description:
        "Save the current symbol graph as a baseline for future diff comparison. " +
        "MANDATORY to run BEFORE making target edits when you plan to verify with graph diff evidence. " +
        "Call this, make your edits, then use verify with with_diff: true for the final comparison. " +
        "Without this baseline, graph diff is unavailable.",
      inputSchema: {
        project: ProjectPathSchema,
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project }) => {
      try {
        const output = await runRepomap("cache", { flags: { project }, positional: ["save"] });
        return textResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_diff",
    {
      title: "Graph Diff",
      description:
        "Compare current repository graph against a cache_save baseline. " +
        "Shows added/removed/modified symbols and changed call relationships. " +
        "Requires a prior cache_save baseline — run cache_save BEFORE edits, then diff after. " +
        "Prefer verify with with_diff: true for final evidence — use this directly only for raw graph change data.",
      inputSchema: {
        project: ProjectPathSchema,
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false,
      },
    },
    async ({ project }) => {
      try {
        const output = await runRepomap("diff", { project }, true);
        return jsonResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_refs",
    {
      title: "Reference Analysis",
      description:
        "All references to a symbol across the entire codebase — who imports it, who calls it, who references it. " +
        "MANDATORY before deleting, renaming, or changing the signature of any symbol. " +
        "When refs shows callers in multiple files, inspect EACH caller before changing the contract. " +
        "Without a symbol, returns global analysis: entry points, orphaned symbols, most-referenced symbols. " +
        "Use AFTER query_symbol to confirm you found all usage sites.",
      inputSchema: {
        project: ProjectPathSchema,
        symbol: z.string().optional()
          .describe("Symbol name for specific analysis (omit for global analysis)"),
        file_path: z.string().optional()
          .describe("Relative file path to disambiguate symbol"),
        max_files: MaxFilesSchema,
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, symbol, file_path, max_files }) => {
      try {
        const output = await runRepomap("refs", {
          project, symbol, file_path, max_files,
        }, true);
        return jsonResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_orphan",
    {
      title: "Dead Code Detection",
      description:
        "Find orphaned (potentially dead) code candidates with confidence tiers (high/medium/low). " +
        "MANDATORY after deleting features or large refactors to find leftover code. " +
        "WARNING: output is CANDIDATES only, NOT a deletion license. " +
        "ALWAYS verify each high-confidence candidate with repomap_refs before actual deletion. " +
        "Check for dynamic references the graph cannot see: string-based dispatch, reflection, macros, config-driven routing, test fixtures. " +
        "Run the full test suite after any deletion. NEVER delete based solely on orphan output. " +
        "Use min_confidence: 70 to filter out noise and focus on likely dead code.",
      inputSchema: {
        project: ProjectPathSchema,
        limit: z.number().int().min(1).max(100).optional()
          .describe("Max candidates per confidence tier (default: 20)"),
        min_confidence: z.number().int().min(0).max(100).optional()
          .describe("Minimum confidence score 0-100 to include (default: 0)"),
        max_files: MaxFilesSchema,
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, limit, min_confidence, max_files }) => {
      try {
        const output = await runRepomap("orphan", {
          project, limit, min_confidence, max_files,
        }, true);
        return jsonResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_check",
    {
      title: "Compiler/Static Analysis Diagnostics",
      description:
        "Run compiler and static analysis tools (tsc, cargo check, ruff, mypy, go vet) on the project. " +
        "MANDATORY after any edit that changes types, interfaces, or function signatures. " +
        "Use when you need type/lint evidence without the full verify gate. " +
        "CRITICAL: when status is 'unknown', it means NO diagnostic tool ran — do NOT treat this as 'passed'. " +
        "When check reports failure, investigate and fix BEFORE claiming completion. " +
        "Use modified_files to narrow diagnostics to specific changed files.",
      inputSchema: {
        project: ProjectPathSchema,
        types: z.array(z.enum(["typescript", "rust", "python", "go", "javascript"])).optional()
          .describe("Explicit project types to check (auto-detected if omitted)"),
        max_issues: z.number().int().min(1).max(200).optional()
          .describe("Maximum issues per tool (default: 50)"),
        since_commit: z.string().optional()
          .describe("Only check files changed since the given commit"),
        modified_files: z.array(z.string()).optional()
          .describe("Explicit modified file paths to focus diagnostics"),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, types, max_issues, since_commit, modified_files }) => {
      try {
        const output = await runRepomap("check", {
          project, types, max_issues, since_commit, modified_files,
        });
        return textResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_routes",
    {
      title: "HTTP Route Inventory",
      description:
        "Extract all HTTP/API route definitions from the codebase (method, path, handler, file, framework). " +
        "Supports FastAPI, Flask, Express, Axum, Spring Boot. " +
        "MANDATORY when any task touches API endpoints, handlers, response shapes, or client-server contracts. " +
        "For full context BEFORE changing routes, use repomap_routes_consumers instead — it maps each route to its frontend/test consumers.",
      inputSchema: {
        project: ProjectPathSchema,
        max_files: MaxFilesSchema,
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, max_files }) => {
      try {
        const output = await runRepomap("routes", { project, max_files }, true);
        return jsonResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_routes_consumers",
    {
      title: "API Route Consumer Mapping",
      description:
        "Map every backend API route to its frontend/test consumers with confidence levels (high/medium/low). " +
        "MANDATORY before changing ANY API handler, response shape, or route signature. " +
        "Shows exactly which frontend components and test files call each route — so you know what will break. " +
        "Use this BEFORE touching routes, not after. Each consumer includes match type (fetch/axios/requests) and confidence.",
      inputSchema: {
        project: ProjectPathSchema,
        max_files: MaxFilesSchema,
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, max_files }) => {
      try {
        const output = await runRepomap("routes", { project, max_files, with_consumers: true }, true);
        return jsonResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_state_map",
    {
      title: "State Definition Map",
      description:
        "Find enum/const state definitions with all values, writers, and readers across the codebase. " +
        "MANDATORY before adding, removing, or changing ANY state value, enum member, or lifecycle logic. " +
        "Supports Python Enum, TypeScript enum/string unions, Rust enum, Go const blocks. " +
        "Shows every place that reads or writes each state value — missing one can cause production bugs. " +
        "Use symbol for exact enum name, or query for keyword-based discovery of state definitions.",
      inputSchema: {
        project: ProjectPathSchema,
        max_files: MaxFilesSchema,
        symbol: z.string().optional().describe("Exact symbol name (e.g. TaskStatus)"),
        query: z.string().optional().describe("Keywords to find relevant state definitions (e.g. 'task status')"),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, max_files, symbol, query }) => {
      try {
        const output = await runRepomap("state-map", { project, max_files, symbol, query }, true);
        return jsonResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_lsp_setup",
    {
      title: "LSP Server Setup",
      description:
        "Auto-detect and install missing LSP servers for the project's languages. " +
        "Run this after repomap_scan when LSP servers are missing. " +
        "Supports 13 languages: Python (pyright), TypeScript/JS, Rust (rust-analyzer), Go (gopls), " +
        "C/C++ (clangd), C# (csharp-ls), Java (jdtls), Lua, PHP (intelephense), Ruby (ruby-lsp), " +
        "Swift (sourcekit-lsp), Kotlin. " +
        "Always use dry_run: true first to preview the install plan.",
      inputSchema: {
        project: ProjectPathSchema,
        languages: z.array(z.string()).optional()
          .describe("Languages to install servers for (default: auto-detect from project files)"),
        dry_run: z.boolean().optional()
          .describe("Preview install plan without executing (always use this first)"),
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, languages, dry_run }) => {
      try {
        const output = await runRepomap("lsp setup", {
          project, languages, dry_run,
        });
        return textResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );

  server.registerTool(
    "repomap_doctor",
    {
      title: "Health Check",
      description:
        "Validate runtime prerequisites: tree-sitter parsers, LSP server availability, binary integrity. " +
        "Use with_lsp: true to also check which LSP servers are available and get install suggestions for missing ones. " +
        "Use when suspecting stale binary, parser/runtime issues, or PATH mismatch.",
      inputSchema: {
        project: ProjectPathSchema,
        with_lsp: z.boolean().optional()
          .describe("Also check LSP server availability and suggest install commands for missing servers"),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, with_lsp }) => {
      try {
        const output = await runRepomap("doctor", { project, lsp: with_lsp });
        return textResult(output);
      } catch (err) {
        return toolError(err instanceof Error ? err.message : String(err));
      }
    },
  );
}
