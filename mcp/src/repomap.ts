import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

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

  const candidate = join(__dirname, "..", info.pkg, info.bin);
  if (existsSync(candidate)) return candidate;

  const nested = join(__dirname, "..", "repomap-bin", "node_modules", info.pkg, info.bin);
  if (existsSync(nested)) return nested;

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
        "It should be auto-installed on first run. If not, run: pip install repomap-cli " +
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

async function findPythonCommand(): Promise<string | null> {
  const candidates = ["python3", "python"];
  for (const cmd of candidates) {
    if (await canRun(cmd, ["--version"])) return cmd;
  }
  return null;
}

async function pipInstall(pythonCmd: string): Promise<void> {
  const pipArgs = ["-m", "pip", "install", "repomap-cli"];

  await execFileAsync(pythonCmd, pipArgs, {
    timeout: 120000,
    maxBuffer: 10 * 1024 * 1024,
    encoding: "utf-8",
  }).catch(async (err) => {
    const stderr: string = err.stderr || "";
    if (stderr.includes("externally-managed-environment") || stderr.includes("--break-system-packages")) {
      const retryArgs = ["-m", "pip", "install", "--break-system-packages", "repomap-cli"];
      return execFileAsync(pythonCmd, retryArgs, {
        timeout: 120000,
        maxBuffer: 10 * 1024 * 1024,
        encoding: "utf-8",
      });
    }
    throw err;
  });
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

  const pythonCmd = await findPythonCommand();
  if (pythonCmd) {
    await pipInstall(pythonCmd);

    if (await canRun(REPOMAP_BIN, ["--help"])) {
      resolvedBinPath = REPOMAP_BIN;
      return;
    }
  }

  throw new Error(
    "repomap binary not found. Tried:\n" +
    "  1. npm platform package (repomap-bin-*) — not available for this platform or not installed\n" +
    "  2. 'repomap' in PATH — not found\n" +
    "  3. pip install repomap-cli — " + (pythonCmd ? "installed but not on PATH" : "no Python found") + "\n\n" +
    "Please install repomap manually:\n" +
    "  npm install -g repomap-bin\n" +
    "  pip install repomap-cli\n" +
    "Or set the REPOMAP_BIN environment variable to the full path of the repomap binary.",
  );
}
