import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

function findBinary() {
  const platform = process.platform;
  const arch = process.arch;
  const binName = platform === "win32" ? "repomap.exe" : "repomap";

  // 1. Check repo dist/ directory (local development)
  const repoBin = join(__dirname, "..", "..", "dist", binName);
  if (existsSync(repoBin)) return repoBin;

  // 2. Check npm platform packages
  const platformPackages = {
    "linux-x64": { pkg: "repomap-bin-linux-x64", bin: "repomap" },
    "darwin-arm64": { pkg: "repomap-bin-darwin-arm64", bin: "repomap" },
    "win32-x64": { pkg: "repomap-bin-windows-x64", bin: "repomap.exe" },
  };

  const key = `${platform}-${arch}`;
  const info = platformPackages[key];
  if (info) {
    const candidate = join(__dirname, "node_modules", info.pkg, info.bin);
    if (existsSync(candidate)) return candidate;
  }

  // 3. Vendor fallback
  const fallback = join(__dirname, "vendor", binName);
  if (existsSync(fallback)) return fallback;

  // 4. PATH fallback
  return binName;
}

export function getBinaryPath() {
  return findBinary();
}

export default { getBinaryPath };
