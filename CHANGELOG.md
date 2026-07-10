# Changelog

All notable changes to `overleaf-git-sync` are documented here.

## 0.2.0 - 2026-07-10

### Safety

- Add full remote-state preconditions before every push operation, durable push
  journals, and `ol recover` for interrupted transactions.
- Update existing editable documents through versioned ShareJS OT and verify
  the resulting document content before reporting success.
- Reject unsupported `history-ot` documents and unsafe non-BMP insertions before
  remote mutation.
- Reconcile ambiguous accepted-but-response-lost file replacements from a fresh
  Overleaf entity tree.
- Reject symlinks, submodules, nested repositories, special filesystem entries,
  path collisions, non-UTF-8 text, and unsafe ZIP members.

### Git and local state

- Import Overleaf snapshots with Git plumbing without switching or rewriting
  the user's active working tree.
- Protect ownership of the managed `overleaf-remote` branch and preserve tracked
  sync-ignored local files across remote snapshot imports.
- Parse Git name-status output with NUL delimiters for exact Unicode and unusual
  filename handling.
- Make `init --force` preserve pre-existing local content through Git.

### Credentials and reliability

- Add stdin-based password and cookie input, restrictive atomic metadata writes,
  consistent TLS handling, stale-lock recovery, streamed ZIP downloads, and
  stable user-facing error wrapping.
- Clarify dry-run and `--fast` behavior and correct sync metadata semantics.
- Add deterministic safety coverage plus opt-in live read/write, concurrent OT,
  and ambiguous-response tests for disposable Overleaf CE projects.

## 0.1.0

- Initial working CLI for cookie authentication, initialization, pull, push,
  status, verify, Git conflict handling, and Overleaf CE HTTP synchronization.
