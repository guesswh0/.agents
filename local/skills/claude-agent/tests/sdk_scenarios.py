import asyncio
import atexit
import json
import os
from pathlib import Path
import time

from claude_agent_sdk import ToolPermissionContext

from sdk_messages import (
    result_message,
    stream_event,
    workflow_started,
    workflow_finished,
)


async def interact(options, requests):
    cwd = Path(options.cwd)
    for index, request in enumerate(requests):
        decision = await options.can_use_tool(
            request["name"],
            request["input"],
            ToolPermissionContext(
                tool_use_id=f"call-{index}", title=f"Request {index}"
            ),
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


async def workflow(options, case, session):
    cwd = Path(options.cwd)

    async def request(index, data):
        return await options.can_use_tool(
            "Workflow",
            data,
            ToolPermissionContext(
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
        (cwd / "decisions.json").write_text(json.dumps([d.behavior for d in decisions]))
        yield result_message(session, result="requests resolved")
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
            continue
        (cwd / f"approved-{index}.json").write_text(json.dumps(decision.updated_input))
        yield workflow_started(task_id=f"task-{index}")
        yield result_message(session, result="launched")
        yield workflow_finished(task_id=f"task-{index}")
    yield result_message(session, result="finished")


async def query(prompt, options):
    cwd = Path(options.cwd)
    case = os.environ["ADAPTER_TEST_SCENARIO"]
    session = options.resume or options.session_id
    (cwd / "sdk.pid").write_text(str(os.getpid()))
    selected = {
        key: getattr(options, key)
        for key in (
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
            "include_partial_messages",
        )
    }
    (cwd / "sdk-options.json").write_text(json.dumps(selected))
    if case == "crash":
        os._exit(17)
    if case == "blocked_exit":
        atexit.register(time.sleep, 60)
    elif case == "blocked_event_loop":
        time.sleep(60)
        return
    elif case == "blocked_host":
        await asyncio.create_subprocess_exec(options.cli_path, cwd=cwd)
        yield workflow_started(description="x" * 500000)
        time.sleep(60)
        return
    elif case in {"hidden_thinking", "hidden_thinking_then_idle"}:
        for _ in range(30):
            yield stream_event(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": ""},
                }
            )
            await asyncio.sleep(0.1)
        if case == "hidden_thinking_then_idle":
            await asyncio.sleep(30)
        yield result_message(session, result="finished after sustained activity")
        return
    elif case in {"interaction", "slow_question"}:
        if case == "slow_question":
            yield stream_event({"type": "message_start"})
            await asyncio.sleep(0.25)
        await interact(options, json.loads(os.environ["ADAPTER_TEST_REQUESTS"]))
        if case == "slow_question":
            await asyncio.sleep(0.25)
        yield result_message(session, result="requests resolved")
        return
    elif case == "stubborn":
        child = await asyncio.create_subprocess_exec(options.cli_path, cwd=cwd)
        try:
            await asyncio.sleep(30)
        finally:
            await asyncio.sleep(20)
            child.kill()
            await child.wait()
        return
    elif case == "workflow_metrics":
        directory = (
            Path(os.environ["CLAUDE_CONFIG_DIR"])
            / "projects"
            / "test-project"
            / session
            / "workflows"
        )
        directory.mkdir(parents=True)
        (directory / "run.json").write_text(
            json.dumps(
                {
                    "taskId": "task-0",
                    "agentCount": 1,
                    "totalTokens": 123,
                    "startTime": 1000,
                    "durationMs": 500,
                    "workflowProgress": [
                        {"type": "workflow_phase", "index": 1, "title": "Check"},
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
        yield workflow_started()
        yield workflow_finished()
        yield result_message(session, result="finished")
        return
    elif case.startswith("workflow"):
        async for message in workflow(options, case, session):
            yield message
        return
    elif case != "success":
        raise ValueError(f"Unknown test scenario: {case}")
    yield result_message(
        session, result=json.dumps({"prompt": prompt, "options": selected})
    )
