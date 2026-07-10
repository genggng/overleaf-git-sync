"""Durable journal for recovery from partially applied pushes."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ol_ce_sync.diff import PushOperation
from ol_ce_sync.errors import RecoveryRequiredError
from ol_ce_sync.utils.files import atomic_write_text


@dataclass
class PushJournalData:
    transaction_id: str
    project_id: str
    base_commit: str
    started_at: float
    operations: list[dict[str, object]]
    completed_indices: list[int]
    active_index: int | None = None


class PushJournal:
    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def start(
        self,
        *,
        project_id: str,
        base_commit: str,
        operations: list[PushOperation],
    ) -> PushJournalData:
        if self.exists():
            raise RecoveryRequiredError(
                "A previous push journal is still present. Run `ol recover` before pushing again."
            )
        data = PushJournalData(
            transaction_id=uuid.uuid4().hex,
            project_id=project_id,
            base_commit=base_commit,
            started_at=time.time(),
            operations=[asdict(operation) for operation in operations],
            completed_indices=[],
        )
        self.save(data)
        return data

    def load(self) -> PushJournalData:
        if not self.exists():
            raise RecoveryRequiredError("No interrupted push journal was found.")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return PushJournalData(**raw)
        except (OSError, ValueError, TypeError) as exc:
            raise RecoveryRequiredError(
                f"Interrupted push journal is invalid: {self.path}"
            ) from exc

    def before_operation(self, data: PushJournalData, index: int) -> None:
        data.active_index = index
        self.save(data)

    def after_operation(self, data: PushJournalData, index: int) -> None:
        if index not in data.completed_indices:
            data.completed_indices.append(index)
        data.active_index = None
        self.save(data)

    def save(self, data: PushJournalData) -> None:
        atomic_write_text(
            self.path,
            json.dumps(asdict(data), indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
