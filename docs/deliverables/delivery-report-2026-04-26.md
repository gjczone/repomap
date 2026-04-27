# Delivery Report: RepoMap CLI v2

**Date**: 2026-04-26
**Source Platform**: Ubuntu Linux 24.04 (x86_64)
**Target Platforms**: Linux
**Delivery Form**: Standalone single-file ELF binary

## Binary Details

| Property | Value |
|----------|-------|
| Format | ELF 64-bit LSB executable, x86-64 |
| Build tool | PyInstaller 6.x `--onefile` |
| Contents | Python 3.12 interpreter + all .py modules + 8 tree-sitter .so bindings |
| Dependencies | Only glibc (libc, libdl, libz, libpthread) — all present on any standard Linux |
| Size | ~9 MB |
| PATH location | `~/.local/bin/repomap` |
| Source location | `~/.A1/ai/cli-created/cli/repomap/dist/repomap` |

`repomap` is a fully self-contained binary. It does NOT shell out to Python scripts or require a Python runtime. Copying the file to another Linux x86_64 machine with a compatible glibc is sufficient.

## Features

### Original Commands (v1)

`scan`, `overview`, `call-chain`, `query-symbol`, `file-detail`, `hotspots`, `cache`, `diff`, `git-history`, `refs`, `orphan`, `check`, `doctor`, `build-binary`

### New Commands (v2, 2026-04-26)

| Command | Purpose | Key capability |
|---------|---------|---------------|
| `query` | Find code by topic keyword | Path + filename + symbol scoring, reading order, test discovery |
| `impact` | Assess file-level change blast radius | Incoming/outgoing edge analysis, 3-layer risk model, test matching |
| `diff-risk` | Pre-commit safety check | Git status detection, affected file analysis, test suggestions |

### New Module

- `repomap_topic.py`: topic scoring engine, file role classification, 5-strategy test matching (name → path → import → symbol → git co-change), noise filtering

### Validation

Validated on two real projects:
- deeper-web (React/TypeScript, 141 source files, 1699 symbols)
- bi-next (Rust/React, 104 source files, 1656 symbols)

Test suite: 49 passed, 2 skipped (binary E2E requires PyInstaller).

---

## New Computer Setup Guide

When moving to a new Linux machine, follow these steps. This document is the single source of truth.

### Option A: Copy Binary Directly (fastest, same-arch Linux)

Only use when the new machine is Linux x86_64 with a compatible glibc (Ubuntu 22.04+, Debian 12+, etc.).

Copy the binary:

```bash
scp old-machine:~/.A1/ai/cli-created/cli/repomap/dist/repomap new-machine:/tmp/repomap
```

On the new machine:

```bash
mkdir -p ~/.local/bin
mv /tmp/repomap ~/.local/bin/repomap
chmod +x ~/.local/bin/repomap
repomap doctor
```

If `~/.local/bin` is not on PATH, add this to `~/.bashrc`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### Option B: Copy Source + Rebuild (safer, any Linux)

When the new machine has a different CPU arch, newer/older glibc, or you want a fresh build.

Copy the source tree:

```bash
scp -r old-machine:~/.A1/ai/cli-created/cli/repomap new-machine:/tmp/repomap-src
```

On the new machine, first install `uv` if needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then build:

```bash
cd /tmp/repomap-src
uv run --with pyinstaller,tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m repomap_cli build-binary --output dist

mkdir -p ~/.local/bin
ln -sf /tmp/repomap-src/dist/repomap ~/.local/bin/repomap
repomap doctor
```

Move source to permanent location:

```bash
mkdir -p ~/.A1/ai/cli-created/cli
mv /tmp/repomap-src ~/.A1/ai/cli-created/cli/repomap
```

### Required Files To Copy

If you want a complete migration, also copy:

| Path | Purpose |
|------|---------|
| `~/.A1/ai/cli-created/cli/repomap/` | Source code + tests + docs |
| `~/.A1/ai/cli-created/cli/repomap/dist/repomap` | Built binary |
| `~/.agents/skills/repomap/` | Skill definition (SKILL.md + references + scripts) |
| `~/.A1/ai/AGENTS.md` | Agent behavior rules (references repomap) |

### Post-Setup Verification

```bash
# Check binary is on PATH
command -v repomap

# Runtime self-check
repomap doctor

# Smoke test against a real repo
repomap overview --project ~/.A1/ai/cli-created/cli/repomap

# New commands check
repomap query --project ~/.A1/ai/cli-created/cli/repomap --query "topic" --max-files 5
repomap impact --project ~/.A1/ai/cli-created/cli/repomap --files repomap_topic.py
repomap diff-risk --project ~/.A1/ai/cli-created/cli/repomap
```

---

## Known Limitations

- Linux host cannot natively produce notarized macOS binaries.
- Windows/macOS binaries are defined in CI workflow, not locally generated on Linux.
- Highly dynamic runtime call construction may still reduce graph precision.
- `query` uses hand-weighted keyword scoring; BM25 upgrade planned for better multi-keyword ranking.
- `impact` and `diff-risk` detect file coupling via graph edges; event-level coupling (CustomEvent, postMessage) not yet detected.
- Test matching depends on project structure and git history depth; small/flat repos may see less precise results.

## Recommended Release Flow

1. Run full local tests on Linux: `uv run pytest tests/`
2. Rebuild binary: `uv run python -m repomap_cli build-binary --output dist`
3. Verify binary: `repomap doctor && repomap overview --project <some-repo>`
4. Push branch to remote.
5. Let `.github/workflows/build-binaries.yml` build and smoke-test all three platforms.
6. Download artifacts from CI.
