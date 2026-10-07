from _contracts import Job, Outcome
from _presentation import execution_summary, save_answer


def finalize(job: Job, outcome: Outcome | None = None, reason=None, error=None):
    observation = outcome if outcome is not None else Outcome()
    latest = observation.latest
    tasks = observation.workflows
    denials = observation.permission_denials
    ending = observation.approval_ending
    reason = reason or observation.reason
    error = error or observation.error
    if reason:
        status = reason
    elif error:
        status = "failed"
    elif ending:
        status, error = ending
    elif denials:
        status = "needs_permission"
    elif latest is None or latest.is_error or latest.subtype != "success":
        status = "failed"
        error = (
            latest.errors if latest else None
        ) or "Claude returned no successful result"
    elif job.workflow and not tasks:
        status, error = "workflow_not_started", "No workflow launch was observed"
    elif (
        any(task["status"] != "completed" for task in tasks.values())
        or observation.result_sequence < observation.completion_sequence
    ):
        status, error = (
            "incomplete",
            "The workflow or its final synthesis did not complete",
        )
    else:
        status = "completed"

    models = observation.models
    summary_models = observation.assistant_models or models
    duration = observation.duration_ms
    summary, summary_warnings = execution_summary(
        job, (latest.usage if latest else None), tasks, duration, summary_models
    )
    warnings = []
    answer = (latest.result if latest else None) or ""
    answer_file = None
    if (
        status == "completed"
        and summary["kind"] == "agent"
        and isinstance(answer, str)
        and answer
    ):
        try:
            answer_file = save_answer(job, answer)
        except Exception as exc:
            warnings.append(f"Could not save Claude's answer: {exc}")
    warnings.extend(summary_warnings)
    event = {
        **job.base,
        "type": "result",
        "status": status,
        "result": answer,
        "models": models,
        "workflows": tasks,
        "permission_denials": denials,
        "summary": summary,
        "answer_file": answer_file,
        "presentation_warnings": warnings,
    }
    if error:
        event["error"] = error
    return event, {
        "completed": 0,
        "dependency_missing": 2,
        "denied": 3,
        "needs_permission": 3,
        "timed_out": 124,
        "cancelled": 130,
    }.get(status, 1)
