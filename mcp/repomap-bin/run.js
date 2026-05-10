import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const localRequire = createRequire(import.meta.url);

function findBinary() {
  const platform = process.platform;
  const arch = process.arch;
  const binName = platform === "win32" ? "repomap.exe" : "repomap";

  // 1. Check repo dist/ directory (local development)
  const repoBin = join(__dirname, "..", "..", "dist", binName);
  if (existsSync(repoBin)) return repoBin;

  // 2. Resolve platform package via Node module resolution (handles hoisted, npx, yarn, pnpm)
  const platformPackages = {
    "linux-x64": { pkg: "repomap-bin-linux-x64", bin: "repomap" },
    "darwin-arm64": { pkg: "repomap-bin-darwin-arm64", bin: "repomap" },
    "win32-x64": { pkg: "repomap-bin-windows-x64", bin: "repomap.exe" },
  };

  const key = `${platform}-${arch}`;
  const info = platformPackages[key];
  if (info) {
    try {
      const pkgJson = localRequire.resolve(`${info.pkg}/package.json`);
      const candidate = join(dirname(pkgJson), info.bin);
      if (existsSync(candidate)) return candidate;
    } catch { /* not resolvable */ }

    // Non-hoisted: inside repomap-bin's own node_modules
    const nested = join(__dirname, "node_modules", info.pkg, info.bin);
    if (existsSync(nested)) return nested;
  }

  // 3. PATH fallback
  return binName;
}

const binaryPath = findBinary();

if (!binaryPath) {
  console.error(
    "repomap binary not found for this platform.\n" +
    "Try: npm install repomap-bin"
  );
  process.exit(1);
}

try {
  execFileSync(binaryPath, process.argv.slice(2), {
    stdio: "inherit",
    env: { ...process.env },
  });
} catch (e) {
  process.exit(e.status ?? 1);
}
