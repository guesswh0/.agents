import json
import math
from pathlib import Path
import uuid


def number(value):
    return (
        value
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        else None
    )


def total(values):
    values = list(values)
    return sum(values) if values and all(v is not None for v in values) else None


def tokens(usage):
    if not isinstance(usage, dict):
        return None
    return total(
        number(usage.get(key, 0 if key.startswith("cache_") else None))
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    )


def span(items, start_key, duration_key):
    intervals = []
    for item in items:
        start = number(item.get(start_key))
        duration = number(item.get(duration_key))
        if start is None or duration is None:
            return None
        intervals.append((start, start + duration))
    if not intervals:
        return None
    return max(end for _, end in intervals) - min(start for start, _ in intervals)


def configurations(agents):
    pairs = {
        (agent.get("model"), agent.get("effort"))
        for agent in agents
        if isinstance(agent.get("model"), str)
        and not agent["model"].startswith("<")
        and (agent.get("effort") is None or isinstance(agent["effort"], str))
    }
    return [
        {"model": model, "effort": effort}
        for model, effort in sorted(pairs, key=lambda pair: (pair[0], pair[1] or ""))
    ]


def phase_summaries(document):
    progress = document.get("workflowProgress", [])
    if not isinstance(progress, list):
        return []
    agents = [
        item
        for item in progress
        if isinstance(item, dict) and item.get("type") == "workflow_agent"
    ]
    phases = {
        item["index"]: {"title": item.get("title"), "agents": []}
        for item in progress
        if isinstance(item, dict)
        and item.get("type") == "workflow_phase"
        and isinstance(item.get("index"), int)
    }
    for agent in agents:
        index = agent.get("phaseIndex")
        if not isinstance(index, int):
            index = None
        phase = phases.setdefault(
            index, {"title": agent.get("phaseTitle"), "agents": []}
        )
        phase["agents"].append(agent)
    return [
        {
            "index": index,
            "title": phase["title"],
            "agent_count": len(phase["agents"]),
            "configurations": configurations(phase["agents"]),
            "tokens": total(number(agent.get("tokens")) for agent in phase["agents"]),
            "duration_ms": span(phase["agents"], "startedAt", "durationMs"),
        }
        for index, phase in phases.items()
    ]


def workflow_summaries(job, tasks):
    documents = {}
    warnings = []
    projects = Path(job.config_dir) / "projects"
    session = job.session_id
    try:
        paths = projects.glob(f"*/{session}/workflows/*.json")
        for path in paths:
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                isinstance(document, dict)
                and isinstance(document.get("taskId"), str)
                and document["taskId"] in tasks
            ):
                documents[document["taskId"]] = (document, path)
    except OSError as exc:
        warnings.append(f"Could not read workflow metrics: {exc}")
    summaries = []
    for task_id, task in tasks.items():
        document, path = documents.get(task_id, ({}, None))
        if path is None:
            warnings.append(f"Native metrics unavailable for workflow {task_id}")
        summaries.append(
            {
                "task_id": task_id,
                "title": document.get("workflowName") or task["description"],
                "status": task["status"],
                "source_file": str(path) if path else None,
                "agent_count": number(document.get("agentCount")),
                "tokens": number(document.get("totalTokens")),
                "started_at": number(document.get("startTime")),
                "duration_ms": number(document.get("durationMs")),
                "phases": phase_summaries(document),
            }
        )
    return summaries, warnings


def execution_summary(job, usage, tasks, duration_ms, models):
    workflow = job.workflow or bool(tasks)
    summary = {
        "title": job.title or job.prompt.strip().splitlines()[0][:120],
        "kind": "workflow" if workflow else "agent",
        "configurations": [],
        "tokens": None,
        "duration_ms": None if workflow else duration_ms,
        "agent_count": None if workflow else 1,
        "workflows": [],
    }
    if workflow:
        summary["token_scope"] = "workflow_agents"
    warnings = []
    try:
        if workflow:
            workflows, warnings = workflow_summaries(job, tasks)
            summary.update(
                workflows=workflows,
                tokens=total(workflow["tokens"] for workflow in workflows),
                duration_ms=span(workflows, "started_at", "duration_ms"),
                agent_count=total(workflow["agent_count"] for workflow in workflows),
            )
        else:
            summary["configurations"] = configurations(
                [{"model": model, "effort": job.effort} for model in models]
            )
            summary["tokens"] = tokens(usage)
    except Exception as exc:
        # presentation failures must leave a usable summary with unknown metrics
        warnings.append(f"Could not prepare execution summary: {exc}")
    return summary, warnings


def save_answer(job, answer):
    directory = Path(job.config_dir) / "claude-agent" / "answers" / job.session_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4()}.md"
    output = path.open("x", encoding="utf-8", newline="")
    try:
        with output:
            output.write(answer)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return str(path)
