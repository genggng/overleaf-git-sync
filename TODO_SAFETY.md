# Safety and Reliability TODO

This checklist tracks the follow-up work from the V1 safety audit. Items are
ordered by risk. A checkbox may only be marked complete after implementation,
automated tests, and user-facing documentation agree.

## P0: data loss and confidentiality

- [x] Add a remote concurrency guard so a push aborts if Overleaf changes after
      the freshness snapshot and before a write. Document the exact guarantee
      and residual limits of the Overleaf CE backend.
- [x] Use Overleaf's versioned text OT channel after live compatibility testing
      to eliminate the remaining check-to-write window for editable documents.
- [x] Make `ol init --force` non-destructive for a non-empty, non-Git directory.
      Existing local files must be committed/backed up or initialization must
      abort before replacing anything.
- [x] Reject symbolic links, submodules, nested Git repositories, and other
      unsupported special filesystem entries before pull, push, init, or
      verify can read, replace, or upload them.

## P1: transaction and Git safety

- [x] Add a durable push journal and recovery path for partially applied remote
      operations. A failed push must report which operations completed and how
      to verify/recover.
- [x] Add compensating cleanup/rollback for conservative remote file
      replacement when upload, rename, or delete fails.
- [x] Import remote snapshots without switching or rewriting the user's active
      working tree. Do not reuse an unowned `overleaf-remote` branch.
- [x] Prevent repository hooks, GPG signing, filters, or branch-switch failures
      from leaving snapshot imports half-finished.

## P1: paths, ignores, and content

- [x] Parse Git name-status output with NUL delimiters so Unicode, tabs,
      newlines, and quoted paths are handled exactly.
- [x] Stop ignoring all `*.pdf` files by default; PDF figures and attachments
      are project source files. Reassess other ambiguous generated-file rules.
- [x] Stop approximating `.gitignore` semantics in sync filtering. Explicit
      sync ignore rules must be distinct from Git's tracked-file behavior.
- [x] Validate every text file as UTF-8 before the first remote mutation so an
      encoding error cannot cause a partial push.
- [x] Detect case-folding and Unicode-normalization path collisions before
      extracting or importing a remote snapshot.

## P2: credentials and transport

- [x] Add stdin-based password/cookie input and warn or deprecate secrets passed
      as command-line arguments.
- [x] Ensure the session path is ignored before saving credentials and write
      session/config/metadata files atomically with restrictive permissions.
- [x] Make insecure TLS configuration consistent across login, init, pull,
      push, status, and verify, with a visible warning.
- [x] Wrap requests, websocket, ZIP, JSON, Unicode, and configuration failures
      in stable user-facing `OlSyncError` subclasses; reject unexpected GET
      redirects and non-ZIP snapshot responses.

## P2: operation lifecycle and scalability

- [x] Make transaction locks crash-aware and recover stale locks safely.
- [x] Run verify under the repository transaction lock.
- [x] Clean all temporary snapshot directories, including retry and failure
      paths, and stream large downloads instead of retaining the ZIP in memory.
- [x] Reduce full-tree memory duplication during verification or enforce a
      documented project-size limit.
- [x] Make `push --dry-run` semantics explicit and avoid surprising local
      mutations, or split planning from remote refresh.
- [x] Correct `last_synced_commit` semantics when local commits are ahead of an
      unchanged remote snapshot.
- [x] Remove obsolete Git configuration fields, use `snapshot_dir` for managed
      temporary snapshots, and retain auth profile only for backend selection.

## Tests and documentation

- [x] Add deterministic tests for concurrent remote changes, critical push
      failure boundaries, recovery journals, compensation, and stale locks.
- [x] Add tests for Unicode/special filenames, symlinks, submodules, nested Git
      repositories, PDF assets, non-UTF-8 files, case collisions, and Git
      worktrees.
- [x] Add live fault-injection coverage for ambiguous network failures after a
      remote operation is accepted but before its HTTP response is received.
- [x] Add an opt-in disposable-project compatibility suite and document the
      private-route assumptions in `LIVE_TESTING.md`.
- [x] Run the live suite and record the supported Overleaf CE version matrix
      before enabling versioned text OT or publishing a release.
- [x] Reconcile `AGENTS.md`, README files, `SKILL.md`, executable names, and
      supported flags such as the removed `--autostash` option.
