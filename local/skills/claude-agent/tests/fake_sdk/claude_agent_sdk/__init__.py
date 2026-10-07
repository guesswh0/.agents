import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from .types import ClaudeAgentOptions as ClaudeAgentOptions
from .types import (
    PermissionResultAllow,
    ResultMessage,
    TaskStartedMessage,
    TaskNotificationMessage,
    StreamEvent,
    TaskProgressMessage,
)


async def query(prompt, options):
    cwd = Path(options.cwd)
    case = os.environ.get("FAKE_SDK_CASE", "success")
    session = options.resume or options.session_id
    (cwd / "sdk.pid").write_text(str(os.getpid()))
    selected = {
        key: getattr(options, key, None)
        for key in [
            "model",
            "effort",
            "cwd",
            "tools",
            "allowed_tools",
            "permission_mode",
            "system_prompt",
            "setting_sources",
            "settings",
            "verbatim_prompts",
            "env",
            "session_id",
            "resume",
        ]
    }
    (cwd / "sdk-options.json").write_text(json.dumps(selected))
    assert not getattr(options, "hooks", None)
    if case == "blocked_host":
        await asyncio.create_subprocess_exec(options.cli_path, cwd=cwd)
        yield TaskStartedMessage(
            task_id="task-0", task_type="local_workflow", description="x" * 500000
        )
        time.sleep(60)
        return
    if case in {
        "active_stream",
        "active_then_idle",
        "empty_heartbeats",
        "hidden_thinking",
        "hidden_thinking_then_idle",
    }:
        assert options.include_partial_messages is True
        hidden = case in {"hidden_thinking", "hidden_thinking_then_idle"}
        iterations, interval = (
            (60, 0.05) if case == "active_stream" or hidden else (12, 0.1)
        )
        for _ in range(iterations):
            yield StreamEvent(
                event={"type": "ping"}
                if case == "empty_heartbeats"
                else {
                    "type": "content_block_delta",
                    "delta": {
                        "type": "thinking_delta",
                        "thinking": "" if hidden else "working",
                    },
                }
            )
            await asyncio.sleep(interval)
        if case in {"active_then_idle", "hidden_thinking_then_idle"}:
            await asyncio.sleep(30)
        yield ResultMessage(
            session_id=session, result="finished after sustained activity"
        )
        return
    if case in {"progress_active", "progress_stalled"}:
        yield TaskStartedMessage(
            task_id="task-0",
            task_type="local_workflow",
            description="progress workflow",
        )
        for index in range(12):
            yield TaskProgressMessage(
                task_id="task-0",
                description="working",
                usage={
                    "total_tokens": index if case == "progress_active" else 0,
                    "tool_uses": 0,
                    "duration_ms": index * 100,
                },
            )
            await asyncio.sleep(0.1)
        yield TaskNotificationMessage(
            task_id="task-0", status="completed", summary="done"
        )
        yield ResultMessage(session_id=session, result="finished")
        return
    if case == "blocked_event_loop":
        time.sleep(60)
        return
    if case in {"interaction", "slow_question"}:
        if case == "slow_question":
            yield StreamEvent(event={"type": "message_start"})
            await asyncio.sleep(0.25)
        for index, request in enumerate(json.loads(os.environ["FAKE_REQUESTS"])):
            name = request["name"]
            assert name in options.tools or name.startswith("mcp__")
            if name == "AskUserQuestion":
                assert name not in options.allowed_tools
            decision = await options.can_use_tool(
                name,
                request["input"],
                SimpleNamespace(tool_use_id=f"call-{index}", title=f"Request {index}"),
            )
            (cwd / f"handled-{index}.json").write_text(
                json.dumps(
                    {
                        "behavior": decision.behavior,
                        "updated_input": getattr(decision, "updated_input", None),
                        "message": getattr(decision, "message", None),
                    }
                )
            )
        if case == "slow_question":
            await asyncio.sleep(0.25)
        yield ResultMessage(session_id=session, result="requests resolved")
        return
    if case == "stubborn":
        child = await asyncio.create_subprocess_exec(options.cli_path, cwd=cwd)
        try:
            await asyncio.sleep(30)
        finally:
            await asyncio.sleep(20)
            child.kill()
            await child.wait()
    if case.startswith("workflow"):
        assert "Workflow" in options.tools
        assert "Workflow" not in options.allowed_tools
        permission = os.environ.get("FAKE_WORKFLOW_PERMISSION", "allow")
        if permission == "deny" or (
            permission == "ask" and options.permission_mode == "dontAsk"
        ):
            yield ResultMessage(
                session_id=session,
                result="not launched",
                permission_denials=[{"tool_name": "Workflow"}],
            )
            return

        async def request(index, data):
            if permission == "allow":
                return PermissionResultAllow(updated_input=data)
            return await options.can_use_tool(
                "Workflow",
                data,
                SimpleNamespace(
                    tool_use_id=f"call-{index}", title="Review test workflow"
                ),
            )

        if case == "workflow_concurrent":
            requests = [
                asyncio.create_task(request(index, {"script": f"return {index}"}))
                for index in range(2)
            ]
            await asyncio.sleep(0)
            (cwd / "concurrent-ready").touch()
            decisions = await asyncio.gather(*requests)
            (cwd / "decisions.json").write_text(
                json.dumps([decision.behavior for decision in decisions])
            )
            yield ResultMessage(session_id=session, result="requests resolved")
            return
        count = 2 if case in ("workflow_twice", "workflow_retry") else 1
        for index in range(count):
            data = {
                "script": "export const meta = {name: 'test'}; return 'done'",
                "args": {"scope": "test"},
            }
            if case == "workflow_file":
                data = {"scriptPath": str(cwd / "workflow.js")}
            decision = await request(index, data)
            if decision.behavior != "allow":
                if case == "workflow_retry":
                    continue
                yield ResultMessage(session_id=session, result="not launched")
                return
            (cwd / f"approved-{index}.json").write_text(
                json.dumps(decision.updated_input)
            )
            task_id = f"task-{index}"
            yield TaskStartedMessage(
                task_id=task_id, task_type="local_workflow", description="test workflow"
            )
            yield ResultMessage(session_id=session, result="launched")
            await asyncio.sleep(0.01)
            if case == "workflow_pending":
                return
            status = "failed" if case == "workflow_failed" else "completed"
            if case == "workflow_metrics":
                directory = (
                    Path(os.environ["CLAUDE_CONFIG_DIR"])
                    / "projects"
                    / "test-project"
                    / session
                    / "workflows"
                )
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "run.json").write_text(
                    json.dumps(
                        {
                            "taskId": task_id,
                            "agentCount": 1,
                            "totalTokens": 123,
                            "startTime": 1000,
                            "durationMs": 500,
                            "workflowProgress": [
                                {
                                    "type": "workflow_phase",
                                    "index": 1,
                                    "title": "Check",
                                },
                                {
                                    "type": "workflow_agent",
                                    "phaseIndex": 1,
                                    "model": "worker",
                                    "tokens": 123,
                                    "startedAt": 1000,
                                    "durationMs": 400,
                                },
                            ],
                        }
                    )
                )
            yield TaskNotificationMessage(
                task_id=task_id, status=status, summary=status
            )
            if case == "workflow_no_synthesis":
                return
        yield ResultMessage(session_id=session, result="finished")
        return
    if case == "no_result":
        return
    if case == "wrong_session":
        session = "00000000-0000-0000-0000-000000000000"
    yield ResultMessage(
        session_id=session,
        result=json.dumps({"prompt": prompt, "options": selected}),
        is_error=case == "api_error",
        permission_denials=[{"tool_name": "Bash"}] if case == "denied" else [],
    )
