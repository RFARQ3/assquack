from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import TYPE_CHECKING, Literal

from assquack._config import AssquackConfig
from assquack._request import AssquackRequest
from assquack._storage.database import database_operation
from assquack._storage.fragment_writer import FragmentFile
from assquack._storage.metadata import MetadataRepository
from assquack._storage.records import PublicationRecord
from assquack._storage.tables import TableReference

if TYPE_CHECKING:
    from assquack._pipeline.resolve import ResolvedAsset

StageName = Literal["resolve", "materialize", "stage", "publish", "export"]
StageStatus = Literal["unstarted", "running", "success", "failed", "skipped"]
_LOGGER = logging.getLogger(__name__)
# Access while holding the shared database lock, including recovery.
ACTIVE_RUNS: set[tuple[str, str]] = set()


@dataclass
class AssquackRun:
    """Track one call from resolution through publication and exports.

    The request holds the call's inputs. This object holds changing execution
    state, including the last completed stage and the reason a stage failed.
    """

    request: AssquackRequest
    runtime: datetime = field(default_factory=lambda: datetime.now(UTC))
    started: float = field(default_factory=perf_counter)
    run_id: str | None = None
    asset: ResolvedAsset | None = None
    metadata: MetadataRepository | None = None
    catalog: str | None = None
    persisted: bool = False
    cached: PublicationRecord | None = None
    candidate_table: TableReference | None = None
    fragments: list[FragmentFile] = field(default_factory=list[FragmentFile])
    row_count: int = 0
    resolve_status: StageStatus = "unstarted"
    materialize_status: StageStatus = "unstarted"
    stage_status: StageStatus = "unstarted"
    publish_status: StageStatus = "unstarted"
    export_status: StageStatus = "unstarted"
    outcome: Literal["running", "success", "failed"] = "running"
    failed_stage: StageName | None = None
    error_type: str | None = None
    error_message: str | None = None

    @property
    def config(self) -> AssquackConfig:
        return self.request.config

    @property
    def resolved(self) -> ResolvedAsset:
        assert self.asset is not None
        return self.asset

    @property
    def repository(self) -> MetadataRepository:
        assert self.metadata is not None
        return self.metadata

    @property
    def id(self) -> str:
        assert self.run_id is not None
        return self.run_id

    @property
    def duration_ms(self) -> int:
        return int((perf_counter() - self.started) * 1000)

    def identify(self) -> None:
        self.run_id = hashlib.md5(
            f"{self.resolved.asset_id}:{self.runtime.isoformat()}".encode()
        ).hexdigest()

    def save_state(self) -> None:
        """Save progress only after this call has created its own metadata row."""
        if self.persisted:
            with database_operation(self.config):
                self.repository.save_run_state(self)

    def fail(self, stage: StageName | None, error: BaseException) -> None:
        """Record the failed stage without replacing the original exception."""
        if stage is not None:
            setattr(self, f"{stage}_status", "failed")
        self.outcome = "failed"
        self.failed_stage = stage
        self.error_type = type(error).__name__
        self.error_message = str(error)[:4000]
        error.add_note(
            f"Assquack {stage or 'final state save'} failed "
            f"for run {self.run_id or '(not yet identified)'}"
        )
        _LOGGER.error("%s failed for run %s", stage, self.run_id, exc_info=True)
        try:
            if self.persisted:
                with database_operation(self.config):
                    if self.publish_status != "success":
                        self.repository.mark_run_failed(
                            self.id, str(error), self.duration_ms
                        )
                    self.repository.save_run_state(self)
        except Exception:
            _LOGGER.warning(
                "Could not record failure for run %s", self.run_id, exc_info=True
            )
