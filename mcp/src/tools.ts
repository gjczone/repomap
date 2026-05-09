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
        "Scan a repository with tree-sitter AST parsing and return scan summary. " +
        "This is the first step before using other analysis tools — it builds the symbol graph and caches it for subsequent calls. " +
        "Returns: file count, symbol count, entry points, and top hotspot files.",
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
        "Generate a comprehensive project overview report including module structure, reading order, " +
        "hotspot files, entry points, and key symbols. This is the primary tool for understanding a codebase. " +
        "Returns: structured JSON with scanStats, entry_points, hotspots, reading_order, modules, summary_symbols, supporting_files.",
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
        "Analyze the call chain for a specific symbol — who calls it (callers) and what it calls (callees). " +
        "Essential for understanding how a function/class fits into the broader codebase. " +
        "Returns: symbol info, callers list, and callees list with file locations.",
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
        "Search for symbols by name in the repository. Supports exact and fuzzy matching. " +
        "Use this to find where a function, class, or variable is defined. " +
        "Returns: list of matching symbols with kind, file location, signature, and PageRank score.",
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
        "Search the repository by topic keyword to find relevant files and symbols. " +
        "Unlike query-symbol which searches by exact name, this uses topic scoring to find " +
        "files related to a concept (e.g. 'authentication', 'payment processing'). " +
        "Returns: matched files with roles and scores, related tests, and key symbols.",
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
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, query, max_files_result, max_symbols, no_tests, paths, exclude }) => {
      try {
        const output = await runRepomap("query", {
          project, query,
          max_files: max_files_result,
          max_symbols, no_tests, paths, exclude,
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
        "Analyze the impact of changes to specific files. Identifies which other files are affected, " +
        "related tests, and assesses risk level. Critical for safe code modifications. " +
        "Returns: affected files with confidence levels, related tests, risk assessment, key symbols, and reading order.",
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
        "Aggregate post-edit evidence before final handoff. Combines risk assessment, compiler checks, " +
        "LSP diagnostics, and graph diff to verify code changes are safe. " +
        "Returns: status (passed/warning/failed), changed files, risk level, affected files, test coverage, and diagnostics.",
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
        "Get detailed information about a specific source file including its symbols, " +
        "imports, exports, and call relationships. Useful for understanding a file before editing it. " +
        "Returns: file symbols with signatures, PageRank scores, and cross-references.",
      inputSchema: {
        project: ProjectPathSchema,
        file_path: z.string().describe("Relative file path to inspect"),
        max_symbols: z.number().int().min(1).max(100).optional()
          .describe("Maximum symbols to expand in output (default: 12, auto-adjusted for large files)"),
        max_files: MaxFilesSchema,
        max_chars: z.number().int().min(500).optional()
          .describe("Maximum text output size (default: 6000)"),
      },
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ project, file_path, max_symbols, max_files, max_chars }) => {
      try {
        const output = await runRepomap("file-detail", {
          project, file_path, max_symbols, max_files, max_chars,
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
        "List the highest-density files ranked by symbol count and risk level. " +
        "These are the files most likely to need attention during code review or refactoring. " +
        "Returns: ranked list of files with symbol counts and risk indicators.",
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
        "Save the current symbol graph as a baseline for future comparison. " +
        "Call this BEFORE making code edits so that subsequent diff/verify can detect changes. " +
        "Returns: cache path, symbol count, and edge count.",
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
        "Compare the current repository state against a previously saved graph baseline. " +
        "Detects added/removed/modified symbols and changed call relationships. " +
        "You must run repomap_cache_save before edits for this to work. " +
        "Returns: summary of changes, added/removed/modified symbols, and call chain changes.",
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
    "repomap_git_history",
    {
      title: "Symbol Git History",
      description:
        "Show the Git history for a specific symbol — when it was last modified, by whom, " +
        "and recent commits affecting its file. Useful for understanding ownership and change patterns. " +
        "Returns: current commit, recent commits with authors and dates.",
      inputSchema: {
        project: ProjectPathSchema,
        symbol: z.string().describe("Symbol name to inspect"),
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
        const output = await runRepomap("git-history", {
          project, symbol, file_path, max_files,
        });
        return textResult(output);
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
        "Analyze symbol references — who calls a symbol and what it calls. " +
        "Without a specific symbol, returns global analysis: entry points, orphaned symbols, " +
        "and most-referenced symbols. With a symbol, returns detailed caller/callee breakdown. " +
        "Returns: reference counts, caller/callee lists, entry/leaf/orphan classification.",
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
        "Find orphaned (potentially dead) code — symbols that are neither called by nor call any other symbol. " +
        "Results are classified by confidence level (high/medium/low) based on heuristics. " +
        "Returns: candidates grouped by confidence tier with file locations and reasoning.",
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
        "Run compiler and static analysis tools (tsc, cargo check, ruff, etc.) on the project. " +
        "Detects type errors, lint issues, and other problems. Optionally resolves issues to code symbols. " +
        "Returns: diagnostic status, errors/warnings by file, and tool execution details.",
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
        "Extract and list all HTTP/API route definitions from the codebase. " +
        "Supports FastAPI, Flask, Express, and Axum frameworks. " +
        "Returns: list of routes with method, path, handler, file location, and framework.",
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
}
