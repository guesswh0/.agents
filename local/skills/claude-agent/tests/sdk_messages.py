import claude_agent_sdk as sdk


SESSION = "00000000-0000-0000-0000-000000000001"


def result_message(session_id=SESSION, **fields):
    return sdk.ResultMessage(
        **{
            "subtype": "success",
            "duration_ms": 0,
            "duration_api_ms": 0,
            "num_turns": 1,
            "is_error": False,
            "session_id": session_id,
            "model_usage": {"claude-test": {}},
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 30,
                "cache_creation_input_tokens": 40,
            },
            **fields,
        }
    )


def workflow_started(task_id="task-0", description="test workflow"):
    return sdk.TaskStartedMessage(
        subtype="task_started",
        data={},
        task_id=task_id,
        description=description,
        task_type="local_workflow",
        uuid="start",
        session_id=SESSION,
    )


def workflow_finished(task_id="task-0", status="completed"):
    return sdk.TaskNotificationMessage(
        subtype="task_notification",
        data={},
        task_id=task_id,
        status=status,
        summary=status,
        output_file="",
        uuid="finish",
        session_id=SESSION,
    )


def stream_event(event):
    return sdk.StreamEvent(uuid="stream", session_id=SESSION, event=event)
