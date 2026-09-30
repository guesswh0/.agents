# Dynamic workflows

Add `--workflow` and use a brief that explicitly asks Claude to use a dynamic workflow.

```sh
python3 /absolute/path/to/claude-agent/scripts/claude_task.py \
  --cwd /absolute/path/to/project \
  --access read --workflow \
  --prompt-file /absolute/path/to/work/workflow-task.txt
```

The adapter follows Claude's configured permission mode and rules. Handle permission requests and questions through the common [User input](input.md) channel.

When a Workflow call requires confirmation, inspect `tool_input.script` and the remaining input to present its actual pipeline. The call stays suspended until approved. Saved scripts are read before approval and passed inline afterward, so the executed script matches the reviewed input.

## Completion and interruption

Expect `workflow_started`, periodic `progress`, and `workflow_finished`. If native permissions require confirmation, `approval_accepted` comes before execution. Continue reading until `type: result`; a workflow completion event alone does not include the coordinator's final synthesis.

A plain answer without an observed workflow is `workflow_not_started`. A failed, unfinished, or unsynthesized workflow is `incomplete`. No automatic retry is performed by the adapter.

The final event includes a phase summary from Claude's saved workflow data. Present it using [Results](results.md); individual agent answers are not exported.

Use the host's normal process interruption to cancel. If the calling process disappears, the supervisor stops the SDK worker and its process group. To continue after interruption, inspect any changes and use Claude's saved session ID; the adapter has no task registry to recover pending approval requests.
