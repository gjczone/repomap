#!/usr/bin/env node
const { spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const platforms = [
  '@gjczone/repomap-linux-x64',
  '@gjczone/repomap-darwin-arm64',
  '@gjczone/repomap-windows-x64',
];

let binPath = null;
for (const pkg of platforms) {
  try {
    const pkgPath = require.resolve(pkg + '/package.json');
    const pkgDir = path.dirname(pkgPath);
    const pkgData = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
    const binName = pkgData.bin.repomap;
    const candidate = path.join(pkgDir, binName);
    if (fs.existsSync(candidate)) {
      binPath = candidate;
      break;
    }
  } catch (e) {
    continue;
  }
}

if (!binPath) {
  console.error('repomap: no platform binary found. Please reinstall repomap-bin.');
  process.exit(1);
}

const result = spawnSync(binPath, process.argv.slice(2), { stdio: 'inherit' });
process.exit(result.status !== null ? result.status : 1);
