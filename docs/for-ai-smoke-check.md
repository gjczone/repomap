# For AI: Repomap Smoke Check

This file is written so a future AI assistant can read it and perform a safe, repeatable health check for the `repomap` CLI without asking the user for technical details.

## Goal

Verify that the installed `repomap` command still works on this machine and still gives useful repository-analysis results.

## When To Run

Run this smoke check when any of these is true:

- the user says `检查一下 repomap 还能不能用`
- the user says `最近没更新过，帮我验证一下`
- the user moved the CLI to a new machine
- the user rebuilt the binary
- the user suspects parsing quality or command behavior changed
- roughly every 1-2 months if nothing else triggered a check

## What To Check

There are four checks. Run them in order.

### Check 1: Command Exists

Run:

```bash
command -v repomap
```

Pass condition:

- command exits with code `0`
- output points to a real executable path

Fail meaning:

- `repomap` is not on `PATH`

Fix:

- recreate the symlink:

```bash
ln -sf /home/guojiancheng/.A1/ai/cli-created/cli/repomap/dist/repomap /home/guojiancheng/.local/bin/repomap
```

### Check 2: Runtime Self Check

Run:

```bash
repomap doctor
```

Pass condition:

- command exits with code `0`
- output lists tree-sitter parsers

Fail meaning:

- runtime dependencies are broken
- wrong binary may be on `PATH`

### Check 3: Real Repository Overview

Run:

```bash
repomap overview --project /home/guojiancheng/.A1/ai/cli-created/cli/repomap
```

Pass condition:

- command exits with code `0`
- output starts with a project-map style summary
- output mentions files and symbols

Fail meaning:

- parsing/runtime is broken
- binary and source may be out of sync

### Check 4: Quality Gate

Run:

```bash
repomap check --project /home/guojiancheng/.A1/ai/cli-created/cli/repomap
```

Pass condition:

- command completes normally
- output is a structured diagnostics report

Important:

- `check` may report project issues; that does not automatically mean the CLI itself is broken
- the command is considered healthy if it runs and produces a coherent report

## Stronger Verification

If the user wants a stronger check, run:

```bash
cd /home/guojiancheng/.A1/ai/cli-created/cli/repomap
uv run --with pyinstaller,tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m unittest discover -s tests -v
```

Pass condition:

- all tests pass

This is slower, but much stronger than a simple smoke check.

## If The Binary Is Missing

If `/home/guojiancheng/.A1/ai/cli-created/cli/repomap/dist/repomap` is missing, rebuild it:

```bash
cd /home/guojiancheng/.A1/ai/cli-created/cli/repomap
uv run --with pyinstaller,tree-sitter,tree-sitter-python,tree-sitter-javascript,tree-sitter-typescript,tree-sitter-go,tree-sitter-rust,tree-sitter-html,tree-sitter-css,tree-sitter-json \
  python -m repomap_cli build-binary --output dist
```

Then restore the PATH link:

```bash
ln -sf /home/guojiancheng/.A1/ai/cli-created/cli/repomap/dist/repomap /home/guojiancheng/.local/bin/repomap
```

## What To Tell The User

If all checks pass, tell the user:

- `repomap` is installed
- the binary is present
- the command runs successfully
- repository analysis still works

If a check fails, tell the user:

- exactly which check failed
- what that failure means
- the concrete fix command you ran or recommend
