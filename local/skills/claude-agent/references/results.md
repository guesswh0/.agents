# Results

Use the final event's `summary` and `status` to show a compact Markdown summary in the chat, in the user's language. Render the summary directly, without HTML wrappers: this chat displays `<details>` and `<summary>` as literal tags. Keep the task's substantive answer separate from these execution statistics.

## Single agent

Show the task title, status, model and effort, tokens, duration, and an absolute Markdown link to `answer_file`, labeled “Claude's answer”. For example:

**Review error handling**

Completed · Opus · high · 24.3k tokens · 1m 12s · Claude's answer

The adapter saves the successful final `result` text verbatim as UTF-8 Markdown. It adds no headings, commentary, or formatting. Each invocation gets a new file under the Claude configuration directory's `claude-agent/answers/<session-id>/`, including resumed sessions. Link only a returned `answer_file`; if it is null, omit the link. An export failure leaves the original `result` available and adds a `presentation_warnings` entry.

For several separate agent runs, use one row per run, keeping each run's task, model, effort, status, metrics, and answer link together, even within the same phase.

## Native Claude workflow

Use phase-only aggregation when the adapter reports `summary.kind: workflow`, not merely because an external pipeline has phases. Show the task title and status, then one row per phase and an overall total:

| Phase | Agents | Model · effort | Tokens | Time |
| --- | ---: | --- | ---: | ---: |
| Analysis | 102 | Opus / Sonnet · high | 2.4m | 8m 20s |
| Verification | 37 | Opus · high | 1.1m | 6m |
| **Total** | **139** | | **3.5m** | **14m 20s** |

These are illustrative values. Use `summary.workflows[].phases` for phase rows and `summary` for the total. When several workflows ran, prefix phase names with their workflow title. A null phase title means “Unphased”. Preserve distinct model/effort pairs when configurations differ. Keep individual agents and their answers out of the default summary. Workflow runs do not export answer files or generate reports.

## Metric boundaries

- Display unavailable values as `—`, including unspecified effort. An explicit coordinator effort does not establish a workflow agent's effort. Mention relevant `presentation_warnings` briefly.
- Single-agent tokens use the SDK result's input, output, cache-read input, and cache-creation input counters. Duration measures the adapter's execution, excluding waits for user input. Models come from observed main-agent messages, falling back to the SDK's reported models when no main-agent message is available.
- Workflow metrics come from Claude's saved workflow JSON, matched to task IDs observed in this invocation. Previous workflows in a resumed session are excluded. Label totals as **workflow agents**: native `totalTokens` covers workflow agents; coordinator usage is not added to it. The source files are internal Claude data and may change; missing data stays unknown.
- Phase time is the span from the earliest agent start to the latest finish. Workflow time uses native start and duration; for several workflows it is their overall span. Parallel durations are not added. Workflow orchestration gaps can make its duration longer than the phase times.
- Show the actual final status. Partial or missing statistics do not establish successful completion.
