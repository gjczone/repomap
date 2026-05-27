#!/usr/bin/env node
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const platforms = [
   '@gjczone/repomap-linux-x64',
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

const child = spawn(binPath, process.argv.slice(2), { stdio: 'inherit' });

// Handle spawn failure (binary not found, no execute permission, etc.)
child.on('error', (err) => {
   console.error('repomap: failed to start binary:', err.message);
   process.exit(1);
});

// Forward signals to child process so Ctrl+C works
process.on('SIGINT', () => { child.kill('SIGINT'); });
process.on('SIGTERM', () => { child.kill('SIGTERM'); });

child.on('close', (code) => {
   process.exit(code !== null ? code : 1);
});
