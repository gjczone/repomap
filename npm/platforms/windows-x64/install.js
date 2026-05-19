#!/usr/bin/env node
// Postinstall: ensure the binary is executable
const fs = require('fs');
const path = require('path');
const bin = path.join(__dirname, 'repomap');
try {
  fs.chmodSync(bin, 0o755);
} catch (e) {
  // binary might not exist during dev; ignore
}
