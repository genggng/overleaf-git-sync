"""Backend factory."""

from __future__ import annotations

from ol_ce_sync.backends.base import OverleafBackend
from ol_ce_sync.backends.http_backend import HttpBackend
from ol_ce_sync.backends.pyoverleaf_backend import PyOverleafBackend
from ol_ce_sync.config import Config
from ol_ce_sync.errors import ConfigError


def create_backend(config: Config) -> OverleafBackend:
    backend_type = config.backend.type.lower()
    if backend_type == "http":
        return HttpBackend(config)
    if backend_type == "pyoverleaf":
        return PyOverleafBackend(config.project.host, config.auth.profile)
    raise ConfigError(f"Unsupported backend type: {config.backend.type}")
