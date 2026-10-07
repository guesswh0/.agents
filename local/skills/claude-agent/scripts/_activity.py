class Activity:
    def __init__(self, sdk):
        self.sdk = sdk
        self.tasks = {}

    def is_type(self, message, name):
        return isinstance(message, getattr(self.sdk, name, ()))

    def changed(self, key, values):
        previous = self.tasks.setdefault(key, {})
        changed = any(previous.get(k) != v for k, v in values.items())
        previous.update(values)
        return changed

    def observe(self, message):
        if self.is_type(message, "StreamEvent"):
            event = message.event
            if event.get("type") == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "thinking_delta":
                    return True
                return any(
                    delta.get(key)
                    for key in ("text", "thinking", "partial_json", "signature")
                )
            return event.get("type") in {
                "message_start",
                "content_block_start",
                "content_block_stop",
                "message_delta",
                "message_stop",
            }
        if self.is_type(message, "TaskProgressMessage"):
            usage = message.usage or {}
            return self.changed(
                ("usage", message.task_id),
                {
                    key: usage[key]
                    for key in ("total_tokens", "tool_uses")
                    if key in usage
                },
            )
        if self.is_type(message, "TaskUpdatedMessage"):
            patch = message.patch
            values = {
                key: patch[key] for key in ("status", "result", "error") if key in patch
            }
            return self.changed(("state", message.task_id), values)
        if self.is_type(message, "SystemMessage"):
            if message.subtype in {"init", "compact_boundary"}:
                return True
        return any(
            self.is_type(message, name)
            for name in (
                "AssistantMessage",
                "UserMessage",
                "ResultMessage",
                "TaskStartedMessage",
                "TaskNotificationMessage",
            )
        )
