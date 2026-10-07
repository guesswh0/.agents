import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid
import warnings

from _activity import Activity
from _contracts import Job, LatestResult, Outcome, WorkerEvent, WorkflowState


ACCESS_TOOLS = {
    "none": [],
    "read": ["Read", "Glob", "Grep"],
    "edit": ["Read", "Glob", "Grep", "Edit", "Write"],
}


def emit(base, event_type, **fields):
    print(
        json.dumps({**base, "type": event_type, **fields}, ensure_ascii=False),
        flush=True,
    )


class Clock:
    def __init__(self):
        self.started = time.monotonic()
        self.paused = 0
        self.pause_started = None

    def pause(self):
        self.pause_started = time.monotonic()

    def resume(self):
        self.paused += time.monotonic() - self.pause_started
        self.pause_started = None

    def elapsed(self):
        end = self.pause_started if self.pause_started is not None else time.monotonic()
        return end - self.started - self.paused


class InputLines:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.buffer = b""
        self.loop = asyncio.get_running_loop()
        self.fd = sys.stdin.fileno()
        self.active = False

    def read_ready(self):
        data = os.read(self.fd, 65536)
        if not data:
            self.close()
            self.queue.put_nowait(None)
            return
        self.buffer += data
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            self.queue.put_nowait(line)

    async def read(self):
        if not self.active and self.queue.empty():
            self.loop.add_reader(self.fd, self.read_ready)
            self.active = True
        return await self.queue.get()

    def close(self):
        if self.active:
            self.loop.remove_reader(self.fd)
            self.active = False


@dataclass(frozen=True)
class PendingRequest:
    tool_name: str
    tool_input: dict
    input_sha256: str
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def question(self):
        return self.tool_name == "AskUserQuestion"

    @property
    def reference(self):
        return {"request_id": self.request_id, "input_sha256": self.input_sha256}


class Approvals:
    def __init__(self, job, clock, sdk):
        self.job = job
        self.clock = clock
        self.sdk = sdk
        self.lock = asyncio.Lock()
        self.input = None
        self.denials = []
        self.ending = None

    def snapshot(self, name, data):
        snapshot = json.loads(json.dumps(data))
        if name == "Workflow":
            source = snapshot.pop("scriptPath", None)
            if source and not snapshot.get("script"):
                path = Path(source).expanduser()
                if not path.is_absolute():
                    path = Path(self.job.cwd) / path
                snapshot["script"] = path.read_text(encoding="utf-8")
            if (
                not isinstance(snapshot.get("script"), str)
                or not snapshot["script"].strip()
            ):
                raise ValueError("Workflow supplied no script")
        digest = hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return snapshot, digest

    def answer(self, snapshot, reply):
        updated = {
            key: value
            for key, value in snapshot.items()
            if key not in ("answers", "response")
        }
        if "response" in reply:
            response = reply["response"]
            if (
                "answers" in reply
                or not isinstance(response, str)
                or not response.strip()
            ):
                raise ValueError("Provide either answers or a non-empty response")
            return {**updated, "response": response}
        questions = snapshot.get("questions", [])
        answers = reply.get("answers")
        if (
            not questions
            or not isinstance(answers, dict)
            or set(answers) != {q["question"] for q in questions}
        ):
            raise ValueError("Answers must match every question")
        for question in questions:
            value = answers[question["question"]]
            values = (
                value
                if isinstance(value, list) and question.get("multiSelect")
                else [value]
            )
            if not values or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise ValueError("Each answer must contain non-empty text")
        return {**updated, "answers": answers}

    def deny(self, name, context, reason):
        self.denials.append(
            {
                "tool_name": name,
                "tool_use_id": getattr(context, "tool_use_id", None),
                "reason": reason,
            }
        )
        return self.sdk.PermissionResultDeny(message=reason)

    def end(self, name, context, status, reason):
        self.ending = (status, reason)
        return self.deny(name, context, reason)

    def parse_reply(self, request, line):
        invalid = "Response must match the pending request type, ID, and input hash."
        try:
            reply = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(invalid) from exc
        reply_type = "answer" if request.question else "approval"
        choices = (None, "skip") if request.question else ("approve", "deny")
        if (
            not isinstance(reply, dict)
            or reply.get("type") != reply_type
            or reply.get("request_id") != request.request_id
            or reply.get("input_sha256") != request.input_sha256
            or reply.get("decision") not in choices
        ):
            raise ValueError(invalid)
        if reply.get("decision") in ("deny", "skip"):
            reason = reply.get("message") or "The user declined this request."
            if not isinstance(reason, str):
                raise ValueError("Message must be text")
            return self.sdk.PermissionResultDeny(message=reason)
        updated = (
            self.answer(request.tool_input, reply)
            if request.question
            else request.tool_input
        )
        return self.sdk.PermissionResultAllow(updated_input=updated)

    def request_input(self, request, context):
        metadata = {
            key: getattr(context, key, None)
            for key in (
                "tool_use_id",
                "display_name",
                "description",
                "decision_reason",
                "blocked_path",
                "agent_id",
            )
        }
        emit(
            self.job.base,
            "question_required" if request.question else "approval_required",
            **request.reference,
            tool_name=request.tool_name,
            title=getattr(context, "title", None) or request.tool_name,
            tool_input=request.tool_input,
            **metadata,
        )

    async def wait_for_reply(self, request):
        while True:
            line = await self.input.read()
            if line is None:
                return None
            try:
                return self.parse_reply(request, line)
            except (KeyError, TypeError, ValueError) as exc:
                emit(self.job.base, "control_error", error=str(exc))

    def resolve(self, request, context, reply):
        name = request.tool_name
        if reply is None:
            return self.end(
                name, context, "denied", "User input closed before a response."
            )
        if reply.behavior == "deny":
            if name == "Workflow":
                return self.end(name, context, "denied", reply.message)
            return self.deny(name, context, reply.message)
        emit(
            self.job.base,
            "answer_accepted" if request.question else "approval_accepted",
            **request.reference,
        )
        return reply

    async def decide(self, name, data, context):
        if self.ending:
            return self.deny(name, context, self.ending[1])
        async with self.lock:
            # a queued request may acquire the lock after the channel closes
            if self.ending:
                return self.deny(name, context, self.ending[1])
            try:
                snapshot, digest = self.snapshot(name, data)
            except (OSError, ValueError, UnicodeError) as exc:
                return self.deny(name, context, str(exc))
            request = PendingRequest(name, snapshot, digest)
            if self.input is None:
                self.input = InputLines()
            self.clock.pause()
            try:
                emit({}, WorkerEvent.INPUT_WAIT)
                self.request_input(request, context)
                async with asyncio.timeout(self.job.input_timeout):
                    reply = await self.wait_for_reply(request)
                return self.resolve(request, context, reply)
            except TimeoutError:
                return self.end(name, context, "timed_out", "User input wait expired.")
            except (OSError, ValueError) as exc:
                return self.end(
                    name, context, "denied", "User input unavailable: " + str(exc)
                )
            finally:
                self.clock.resume()
                emit({}, WorkerEvent.INPUT_RESUME)

    def close(self):
        if self.input is not None:
            self.input.close()


class Results:
    def __init__(self, job, sdk):
        self.job = job
        self.sdk = sdk
        self.latest = None
        self.models = set()
        self.assistant_models = set()
        self.denials = []
        self.tasks: dict[str, WorkflowState] = {}
        self.sequence = 0
        self.result_sequence = 0
        self.completion_sequence = 0
        self.last_progress = 0

    def accept(self, message):
        self.sequence += 1
        if (
            isinstance(message, self.sdk.AssistantMessage)
            and message.parent_tool_use_id is None
            and message.model
            and not message.model.startswith("<")
        ):
            self.assistant_models.add(message.model)
        if isinstance(message, self.sdk.ResultMessage):
            if message.session_id != self.job.session_id:
                raise ValueError("Claude returned a different session ID")
            self.latest = message
            self.result_sequence = self.sequence
            self.models.update(message.model_usage or {})
            self.denials.extend(message.permission_denials or [])
        elif isinstance(message, self.sdk.TaskStartedMessage):
            if message.task_type in ("workflow", "local_workflow"):
                self.tasks[message.task_id] = {
                    "status": "running",
                    "description": message.description,
                }
                emit(
                    self.job.base,
                    "workflow_started",
                    task_id=message.task_id,
                    description=message.description,
                )
        elif isinstance(message, self.sdk.TaskNotificationMessage):
            if message.task_id in self.tasks:
                self.tasks[message.task_id]["status"] = message.status
                self.completion_sequence = self.sequence
                emit(
                    self.job.base,
                    "workflow_finished",
                    task_id=message.task_id,
                    status=message.status,
                    summary=message.summary,
                )
        elif isinstance(message, self.sdk.TaskProgressMessage):
            if (
                message.task_id in self.tasks
                and time.monotonic() - self.last_progress >= 2
            ):
                self.last_progress = time.monotonic()
                emit(
                    self.job.base,
                    "progress",
                    task_id=message.task_id,
                    description=message.description,
                    usage=message.usage,
                )

    def outcome(self, approvals, reason=None, error=None):
        latest = self.latest
        return Outcome(
            latest=LatestResult(
                result=latest.result,
                is_error=latest.is_error,
                subtype=latest.subtype,
                errors=latest.errors,
                usage=latest.usage,
            )
            if latest is not None
            else None,
            models=sorted(self.models),
            assistant_models=sorted(self.assistant_models),
            workflows=self.tasks,
            permission_denials=self.denials + approvals.denials,
            approval_ending=approvals.ending,
            result_sequence=self.result_sequence,
            completion_sequence=self.completion_sequence,
            duration_ms=round(approvals.clock.elapsed() * 1000),
            reason=reason,
            error=error,
        )


async def run(job, sdk):
    clock = Clock()
    approvals = Approvals(job, clock, sdk)
    results = Results(job, sdk)
    activity = Activity(sdk)
    tools = list(ACCESS_TOOLS[job.access])
    allowed = list(tools)
    tools.append("AskUserQuestion")
    if job.allow_command:
        tools.append("Bash")
        allowed.extend("Bash(" + rule + ")" for rule in job.allow_command)
    if job.workflow:
        tools.append("Workflow")
    session_env = {"DISABLE_AUTO_COMPACT": "0", "DISABLE_COMPACT": "0"}
    if job.context_window is not None:
        session_env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(job.context_window)
    options = sdk.ClaudeAgentOptions(
        cli_path=job.cli_path,
        cwd=job.cwd,
        session_id=None if job.resume else job.session_id,
        resume=job.resume,
        model=job.model,
        effort=job.effort,
        tools=tools,
        allowed_tools=allowed,
        system_prompt={"type": "preset", "preset": "claude_code"},
        setting_sources=["user", "project", "local"],
        settings=json.dumps({"autoCompactEnabled": True, "env": session_env}),
        permission_mode=job.permission_mode,
        env={**job.claude_env, **session_env},
        can_use_tool=approvals.decide,
        include_partial_messages=True,
        verbatim_prompts=True,
    )

    async def consume():
        async for message in sdk.query(prompt=job.prompt, options=options):
            if activity.observe(message):
                emit({}, WorkerEvent.ACTIVITY)
            results.accept(message)

    task = asyncio.create_task(consume())
    reason = None

    def stop(status):
        nonlocal reason
        if reason is None and not task.done():
            reason = status
            emit(job.base, WorkerEvent.SHUTDOWN, status=status)
            task.cancel()

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        loop.add_signal_handler(signum, stop, "cancelled")
    error = None
    emit(job.base, "started", workflow=job.workflow)
    try:
        await task
    except asyncio.CancelledError:
        reason = reason or "cancelled"
    except Exception as exc:
        error = str(exc)
    finally:
        approvals.close()
    return results.outcome(approvals, reason, error)


def main():
    with os.fdopen(int(sys.argv[1]), encoding="utf-8") as request:
        job = Job(**json.load(request))
    try:
        import claude_agent_sdk as sdk

        # file tools are deliberately approved before the permission callback
        warnings.filterwarnings("ignore", category=sdk.CanUseToolShadowedWarning)
    except ImportError:
        outcome = Outcome(
            reason="dependency_missing",
            error="Run scripts/install_runtime.py to install the pinned Claude Agent SDK.",
        )
    else:
        try:
            outcome = asyncio.run(run(job, sdk))
        except Exception as exc:
            outcome = Outcome(error=str(exc))
    print(json.dumps(outcome.to_event(), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.exit(main())
