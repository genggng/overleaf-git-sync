"""Domain-specific exceptions for ol."""


class OlSyncError(RuntimeError):
    """Base class for user-facing sync failures."""


class ConfigError(OlSyncError):
    """Configuration is missing or invalid."""


class GitError(OlSyncError):
    """A Git command failed."""


class DirtyWorktreeError(GitError):
    """The working tree is dirty and the requested operation requires it clean."""


class SyncConflictError(GitError):
    """Git detected a merge conflict during sync."""

    def __init__(self, files: list[str]) -> None:
        self.files = files
        message = "Sync conflict detected."
        if files:
            message += "\nConflicted files:\n" + "\n".join(f"  - {path}" for path in files)
        message += "\n\nResolve conflicts locally, commit the result, then run:\n  ol push"
        super().__init__(message)


class LockError(OlSyncError):
    """A sync lock could not be acquired."""


class BackendError(OlSyncError):
    """Backend operation failed."""


class UnsupportedBackendOperation(BackendError):
    """The configured backend does not support an operation."""


class VerificationError(OlSyncError):
    """Remote verification did not match the expected local state."""
