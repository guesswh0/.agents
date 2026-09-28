# User input

Use `--prompt-file` and keep the process's stdin open. In Codex, launch `exec_command` with `tty: true` and retain the process handle. Native permission rules determine which tool calls need confirmation.

## Tool permissions

On `approval_required`, present the requested action from `tool_name` and `tool_input`. After the user's decision, send one JSON line to the same process with `write_stdin`:

```json
{"type":"approval","request_id":"ID_FROM_EVENT","input_sha256":"HASH_FROM_EVENT","decision":"approve"}
```

Use `"decision":"deny"` to refuse; an optional `message` explains the refusal to Claude. Approval executes the reviewed input unchanged. A denied tool request lets Claude adjust its approach. Refusing a Workflow closes the approval channel for that invocation.

## Questions

On `question_required`, present `tool_input.questions` and return the user's answers:

```json
{"type":"answer","request_id":"ID_FROM_EVENT","input_sha256":"HASH_FROM_EVENT","answers":{"Exact question text":"Selected label or free text"}}
```

Answer every question. Multi-select answers may use an array of labels. For a general reply instead, send `response` text in place of `answers`. To skip, send `"decision":"skip"` without answers. Skipping returns a denial to Claude without closing the channel.

## Keep the request pending

Every response must end with a newline and match the pending request's ID and hash. Invalid responses produce `control_error` and leave the request waiting. Each response applies once. Accepted responses emit `approval_accepted` or `answer_accepted`; continue reading until the final `result` event.

Keep the process handle and pending request in the current chat or context handoff. Closing stdin ends the exchange with `denied`; exceeding `--input-timeout` returns `timed_out`. Both close the channel, including queued requests. The default is 3600 seconds; `--approval-timeout` remains an alias. Waiting for user input does not consume the execution timeout.
