from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, TypedDict


class WorkerEvent(StrEnum):
    ACTIVITY = "_activity"
    INPUT_WAIT = "_input_wait"
    INPUT_RESUME = "_input_resume"
    SHUTDOWN = "_shutdown"
    OUTCOME = "_outcome"


@dataclass(frozen=True)
class Job:
    session_id: str
    cwd: str
    config_dir: str
    prompt: str
    cli_path: str
    python: str
    title: str | None = None
    resume: str | None = None
    model: str | None = None
    effort: str | None = None
    context_window: int | None = None
    access: str = "read"
    allow_command: list[str] = field(default_factory=list)
    permission_mode: str | None = None
    workflow: bool = False
    idle_timeout: float = 900
    input_timeout: float = 3600
    claude_env: dict[str, str] = field(default_factory=dict)

    @property
    def base(self):
        return {
            "session_id": self.session_id,
            "cwd": self.cwd,
            "requested_model": self.model,
            "effort": self.effort,
        }


class WorkflowState(TypedDict):
    status: str
    description: str


@dataclass(frozen=True)
class LatestResult:
    is_error: bool
    subtype: str
    result: str | None = None
    errors: list[str] | None = None
    usage: dict[str, Any] | None = None


@dataclass(frozen=True)
class Outcome:
    latest: LatestResult | None = None
    models: list[str] = field(default_factory=list)
    assistant_models: list[str] = field(default_factory=list)
    workflows: dict[str, WorkflowState] = field(default_factory=dict)
    permission_denials: list[dict[str, Any]] = field(default_factory=list)
    approval_ending: tuple[str, str] | None = None
    result_sequence: int = 0
    completion_sequence: int = 0
    duration_ms: int | None = None
    reason: str | None = None
    error: str | None = None

    def to_event(self):
        return {"type": WorkerEvent.OUTCOME, **asdict(self)}

    @classmethod
    def from_event(cls, event):
        fields = dict(event)
        if fields.pop("type") != WorkerEvent.OUTCOME:
            raise ValueError("Expected a worker outcome")
        latest = fields.pop("latest", None)
        ending = fields.pop("approval_ending", None)
        return cls(
            latest=LatestResult(**latest) if latest is not None else None,
            approval_ending=tuple(ending) if ending is not None else None,
            **fields,
        )
