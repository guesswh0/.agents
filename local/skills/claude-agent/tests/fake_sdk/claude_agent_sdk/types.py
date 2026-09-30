from types import SimpleNamespace


class ClaudeAgentOptions(SimpleNamespace):
    pass


class PermissionResultAllow(SimpleNamespace):
    behavior = "allow"


class PermissionResultDeny(SimpleNamespace):
    behavior = "deny"


class ResultMessage(SimpleNamespace):
    def __init__(self, **fields):
        super().__init__(
            **{
                "is_error": False,
                "subtype": "success",
                "model_usage": {"claude-test": {}},
                "permission_denials": [],
                "errors": [],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 30,
                    "cache_creation_input_tokens": 40,
                },
                **fields,
            }
        )


class TaskStartedMessage(SimpleNamespace):
    pass


class TaskNotificationMessage(SimpleNamespace):
    pass


class TaskProgressMessage(SimpleNamespace):
    pass
