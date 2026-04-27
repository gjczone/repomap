# Repomap Acceptance Checklist

This checklist captures the real-project acceptance items for the standalone `repomap` CLI.

## Core Behavior Contract

- `check` treats a non-skipped tool with `exit_code != 0` as a failed report, even when no structured issues are parsed.
- `diff-risk` parses `git status --porcelain` without trimming away the leading status column, so unstaged paths like ` M todo.md` remain `todo.md`.
- Member calls such as `session.pty.onData()` do not use unrelated global or exported fallback targets. They resolve only when same-file, explicit import binding, or imported-file evidence exists.
- JS/TS object literal API methods are recognized when properties are arrow functions or function expressions, for example `export const api = { getMetadata: () => ... }`.
- `cache save` and `diff` use the same scan semantics, so saving a cache and immediately diffing unchanged code reports zero graph changes.
- `impact` and `diff-risk` de-duplicate related-test recommendations by `(test_file, target_file)` and keep the best confidence/reason.

## Full Local Verification

Run from the repository root:

```bash
cd /home/guojiancheng/.A1/ai/cli-created/cli/repomap
uv run python -m unittest discover -s tests -v
```

Expected result:

- exit code `0`
- all non-skipped tests pass

## Real Project Acceptance Commands

```bash
repomap doctor
repomap overview --project /home/guojiancheng/.A1/deeper-web --max-files 8000 --json
repomap overview --project /home/guojiancheng/.A1/bi-next --max-files 8000 --json
repomap check --project /home/guojiancheng/.A1/deeper-web --max-issues 10 --no-symbols
repomap check --project /home/guojiancheng/.A1/bi-next --max-issues 10 --no-symbols
repomap diff-risk --project /home/guojiancheng/.A1/deeper-web
repomap query-symbol --project /home/guojiancheng/.A1/bi-next --symbol getMetadata --file-path bi-frontend/src/services/api.ts
repomap call-chain --project /home/guojiancheng/.A1/deeper-web --symbol create --file-path server/src/terminal.ts --depth 1
repomap cache save --project /home/guojiancheng/.A1/deeper-web && repomap diff --project /home/guojiancheng/.A1/deeper-web
```

## Rebuild And PATH Refresh

```bash
cd /home/guojiancheng/.A1/ai/cli-created/cli/repomap
uv run --with pyinstaller python -m repomap_cli build-binary --output dist
ln -sf /home/guojiancheng/.A1/ai/cli-created/cli/repomap/dist/repomap /home/guojiancheng/.local/bin/repomap
command -v repomap
repomap doctor
```

Expected PATH target:

```text
/home/guojiancheng/.local/bin/repomap
```

## Troubleshooting Notes

- If `check` reports failed only because a project tool exits non-zero without structured issues, this is intentional. The raw excerpt should explain the failing tool output.
- If `diff-risk` says there are no project-local changes, confirm the command is run under the intended git repository and project root.
- If object literal API methods are missing, verify the file is parsed as JavaScript or TypeScript and the method value is an arrow function or function expression.
- If immediate `diff` after `cache save` is non-zero, re-run the full unit test suite before trusting the cache baseline.
