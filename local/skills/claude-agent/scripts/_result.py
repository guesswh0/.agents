from _presentation import base_summary, execution_summary, save_answer


def finalize(job, outcome=None, reason=None, error=None):
    observation = outcome or {}
    latest = observation.get("latest")
    tasks = observation.get("workflows", {})
    denials = observation.get("permission_denials", [])
    ending = observation.get("approval_ending")
    reason = reason or observation.get("reason")
    error = error or observation.get("error")
    if reason:
        status = reason
    elif error:
        status = "failed"
    elif ending:
        status, error = ending
    elif denials:
        status = "needs_permission"
    elif latest is None or latest["is_error"] or latest["subtype"] != "success":
        status = "failed"
        error = (latest or {}).get("errors") or "Claude returned no successful result"
    elif job["workflow"] and not tasks:
        status, error = "workflow_not_started", "No workflow launch was observed"
    elif any(
        task["status"] != "completed" for task in tasks.values()
    ) or observation.get("result_sequence", 0) < observation.get(
        "completion_sequence", 0
    ):
        status, error = (
            "incomplete",
            "The workflow or its final synthesis did not complete",
        )
    else:
        status = "completed"

    models = observation.get("models", [])
    summary_models = observation.get("assistant_models") or models
    duration = observation.get("duration_ms")
    summary = base_summary(job, tasks, duration, summary_models)
    warnings = []
    answer = (latest or {}).get("result") or ""
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
    try:
        summary, summary_warnings = execution_summary(
            job, (latest or {}).get("usage"), tasks, duration, summary_models
        )
        warnings.extend(summary_warnings)
    except Exception as exc:
        # presentation must not discard the answer or change execution status
        warnings.append(f"Could not prepare execution summary: {exc}")
    event = {
        **job["base"],
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
