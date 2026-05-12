import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const mcpRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(mcpRoot, "..");

function readJson(relativePath: string): Record<string, any> {
  return JSON.parse(readFileSync(path.join(repoRoot, relativePath), "utf8")) as Record<string, any>;
}

describe("release packaging", () => {
  it("keeps npm package versions and platform binary dependencies aligned", () => {
    const mcpPackage = readJson("mcp/package.json");
    const metaPackage = readJson("mcp/repomap-bin/package.json");
    const platformPackagePaths = [
      "mcp/repomap-bin/platforms/repomap-bin-linux-x64/package.json",
      "mcp/repomap-bin/platforms/repomap-bin-darwin-arm64/package.json",
      "mcp/repomap-bin/platforms/repomap-bin-windows-x64/package.json",
    ];

    assert.equal(metaPackage.version, mcpPackage.version);

    const [major, minor] = String(metaPackage.version).split(".");
    assert.equal(mcpPackage.dependencies["repomap-bin"], `^${major}.${minor}.0`);

    for (const packagePath of platformPackagePaths) {
      const platformPackage = readJson(packagePath);
      assert.equal(platformPackage.version, metaPackage.version, packagePath);
      assert.equal(metaPackage.optionalDependencies[platformPackage.name], metaPackage.version, packagePath);
    }
  });

  it("publishes the binary meta package before the MCP server", () => {
    const workflow = readFileSync(path.join(repoRoot, ".github/workflows/build-binaries.yml"), "utf8");
    const metaPublishStep = workflow.indexOf("Publish binary meta package to npm");
    const serverPublishStep = workflow.indexOf("Publish MCP server to npm");

    assert.ok(metaPublishStep >= 0);
    assert.ok(serverPublishStep > metaPublishStep);
  });
});
