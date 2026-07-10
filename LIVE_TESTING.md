# Live Overleaf CE compatibility testing

The HTTP backend relies on Overleaf CE web and socket routes that are not a
stable public API. Unit tests never write to a live server. Use a disposable
project for the opt-in checks below before releasing changes to write behavior.

## Required environment

Create a session file with `ol auth login`, then set:

```bash
export OL_LIVE_HOST='https://overleaf.example.test'
export OL_LIVE_PROJECT_ID='YOUR_DISPOSABLE_PROJECT_ID'
export OL_LIVE_SESSION_FILE='/absolute/path/to/.ol-sync/session.json'
```

Read-only compatibility check:

```bash
.venv/bin/python -m pytest -q tests/test_live_overleaf.py
```

The write test creates a uniquely named probe, updates it through versioned OT,
downloads and verifies it, then deletes it. Enable it only when the project is
disposable:

```bash
export OL_LIVE_ALLOW_WRITE=1
.venv/bin/python -m pytest -q tests/test_live_overleaf.py
```

The fault-injection test creates a binary probe, lets Overleaf accept the first
replacement rename, then simulates losing the HTTP response. It verifies that
the original bytes and name are restored and that no upload/backup entities are
left behind:

```bash
export OL_LIVE_ALLOW_FAULT_INJECTION=1
.venv/bin/python -m pytest -q tests/test_live_overleaf.py -k lost_response
```

The concurrent OT test joins a probe at one document version, applies a second
client's edit, then submits the stale operation. It requires Overleaf to
transform both operations and requires the backend to report the non-exact
result instead of claiming the local content was published:

```bash
export OL_LIVE_ALLOW_CONCURRENT_OT=1
.venv/bin/python -m pytest -q tests/test_live_overleaf.py -k concurrent_ot
```

Record successful server versions here before a release:

| Tool version | Overleaf CE version | Read/tree | OT write/delete | Concurrent OT | Lost-response recovery | Notes |
|---|---|---:|---:|---:|---:|---|
| 0.2.0 | Exact image tag not exposed by instance | pass | pass | pass | pass | 2026-07-10; Bootstrap protocol 5, Socket.IO 1 transport, `sharejs-text-ot`; `joinDoc` returned six fields and `applyOtUpdate` used a versioned operation |

The tested server does not expose its Docker image tag or release number in its
HTTP status, headers, or authenticated project metadata. The observable
protocol fingerprint is recorded instead of guessing a CE release. Operators
should add their exact `sharelatex/sharelatex:<tag>` here when it is available.

This `sharejs-text-ot` implementation replaces each UTF-16 surrogate unit in an
insert with `U+FFFD`. The backend therefore rejects non-BMP insertions (for
example many emoji and mathematical alphabet symbols) before sending an update.
The live suite verifies that rejection leaves the remote document unchanged.

## Concurrency validation performed

The backend performs a complete snapshot precondition check immediately before
every operation. Existing editable documents are updated with `joinDoc` and a
versioned `applyOtUpdate`, then verified through the document download route.
Binary replacement fault injection covers the critical accepted-rename/lost-
response boundary and confirms state-based rollback from a fresh entity tree.
The concurrent test confirms a stale text operation is transformed with the
online edit and then rejected by exact-content verification. Deterministic tests
cover the remaining replacement boundaries and push journal recovery.

Repeat the full suite and add a matrix row before claiming support for another
Overleaf CE release or protocol fingerprint. `history-ot` is intentionally
rejected until a compatible operation encoder has its own live coverage.
