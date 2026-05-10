import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const localRequire = createRequire(import.meta.url);

const __dirname = dirname(fileURLToPath(import.meta.url));

const REPOMAP_BIN = process.env.REPOMAP_BIN || "repomap";
const REPOMAP_TIMEOUT_MS = parseInt(process.env.REPOMAP_TIMEOUT_MS || "120000", 10);

const PLATFORM_PACKAGES: Record<string, { pkg: string; bin: string }> = {
  "linux-x64": { pkg: "repomap-bin-linux-x64", bin: "repomap" },
  "darwin-arm64": { pkg: "repomap-bin-darwin-arm64", bin: "repomap" },
  "win32-x64": { pkg: "repomap-bin-windows-x64", bin: "repomap.exe" },
};

function getNpmBinPath(): string | null {
  const key = `${process.platform}-${process.arch}`;
  const info = PLATFORM_PACKAGES[key];
  if (!info) return null;

  // 1. Node module resolution (handles hoisted, workspaces, npx, yarn, pnpm)
  try {
    const pkgJson = localRequire.resolve(`${info.pkg}/package.json`);
    const bin = join(dirname(pkgJson), info.bin);
    if (existsSync(bin)) return bin;
  } catch { /* not resolvable at top level */ }

  // 2. repomap-bin's nested node_modules (non-hoisted layout)
  try {
    const binPkg = localRequire.resolve("repomap-bin/package.json");
    const nested = join(dirname(binPkg), "node_modules", info.pkg, info.bin);
    if (existsSync(nested)) return nested;
  } catch { /* repomap-bin not resolvable */ }

  // 3. Sibling to MCP server dist/ (fallback for edge-case layouts)
  const sibling = join(__dirname, "..", info.pkg, info.bin);
  if (existsSync(sibling)) return sibling;

  // 4. Local repo development: mcp/repomap-bin/platforms/<pkg>/<bin>
  const devPath = join(__dirname, "..", "repomap-bin", "platforms", info.pkg, info.bin);
  if (existsSync(devPath)) return devPath;

  return null;
}

function buildArgs(command: string, flags: Record<string, unknown>): string[] {
  const args: string[] = [command];

  for (const [key, value] of Object.entries(flags)) {
    if (value === undefined || value === null) continue;

    const kebabKey = key.replace(/_/g, "-");

    if (typeof value === "boolean") {
      if (value) args.push(`--${kebabKey}`);
    } else if (Array.isArray(value)) {
      if (value.length > 0) {
        args.push(`--${kebabKey}`, ...value.map(String));
      }
    } else {
      args.push(`--${kebabKey}`, String(value));
    }
  }

  return args;
}

class RepomapError extends Error {
  readonly exitCode: number;
  readonly stderr: string;

  constructor(exitCode: number, stderr: string) {
    const detail = stderr.trim() || "unknown error";
    super(`repomap exited with code ${exitCode}: ${detail}`);
    this.name = "RepomapError";
    this.exitCode = exitCode;
    this.stderr = stderr;
  }
}

export interface RepomapOptions {
  flags?: Record<string, unknown>;
  positional?: string[];
  expectJson?: boolean;
}

let resolvedBinPath: string | null = null;

function getResolvedBin(): string {
  if (resolvedBinPath) return resolvedBinPath;
  return REPOMAP_BIN;
}

export async function runRepomap(
  command: string,
  flagsOrOptions?: Record<string, unknown> | RepomapOptions,
  expectJson: boolean = false,
): Promise<string> {
  let flags: Record<string, unknown> = {};
  let positional: string[] = [];

  if (flagsOrOptions && typeof flagsOrOptions === "object" && "flags" in flagsOrOptions) {
    const opts = flagsOrOptions as RepomapOptions;
    flags = opts.flags ?? {};
    positional = opts.positional ?? [];
    if (opts.expectJson) expectJson = true;
  } else if (flagsOrOptions) {
    flags = flagsOrOptions as Record<string, unknown>;
  }

  if (expectJson) {
    flags.json = true;
  }

  const args = buildArgs(command, flags);
  for (const p of positional) {
    args.push(p);
  }

  const bin = getResolvedBin();

  try {
    const { stdout, stderr } = await execFileAsync(bin, args, {
      timeout: REPOMAP_TIMEOUT_MS,
      maxBuffer: 50 * 1024 * 1024,
      encoding: "utf-8",
    });

    return (stdout || "").trim();
  } catch (err: unknown) {
    const error = err as NodeJS.ErrnoException & { stdout?: string; stderr?: string; status?: number };
    if (error.code === "ENOENT") {
      throw new Error(
        `repomap binary not found at "${bin}". ` +
        "Run: npm install -g repomap-bin " +
        "or set the REPOMAP_BIN environment variable.",
      );
    }
    throw new RepomapError(
      error.status ?? 1,
      error.stderr || error.stdout || error.message || "",
    );
  }
}

export function parseJsonOutput<T>(raw: string): T {
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new Error(
      `repomap returned non-JSON output. First 200 chars: ${raw.slice(0, 200)}`,
    );
  }
}

async function canRun(bin: string, args: string[] = ["--help"]): Promise<boolean> {
  try {
    await execFileAsync(bin, args, { timeout: 10000, encoding: "utf-8" });
    return true;
  } catch {
    return false;
  }
}

export async function ensureRepomapInstalled(): Promise<void> {
  const npmBin = getNpmBinPath();
  if (npmBin && await canRun(npmBin, ["--help"])) {
    resolvedBinPath = npmBin;
    return;
  }

  if (await canRun(REPOMAP_BIN, ["--help"])) {
    resolvedBinPath = REPOMAP_BIN;
    return;
  }

  throw new Error(
    "repomap binary not found. Tried:\n" +
    "  1. npm platform package (repomap-bin-*) — not available for this platform or not installed\n" +
    "  2. 'repomap' in PATH — not found\n\n" +
    "Please install repomap:\n" +
    "  npm install -g repomap-bin\n" +
    "Or set the REPOMAP_BIN environment variable to the full path of the repomap binary.",
  );
}
