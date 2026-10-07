import argparse
from contextlib import redirect_stderr
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import claude_agent_sdk as sdk

from sdk_messages import (
    SESSION,
    result_message,
    stream_event,
    workflow_started,
    workflow_finished,
)

with mock.patch.object(
    sys, "path", [str(Path(__file__).resolve().parents[1] / "scripts"), *sys.path]
):
    from _activity import Activity
    from _result import finalize
    from _sdk import Approvals, Clock, Results
    import claude_task


class ActivityTests(unittest.TestCase):
    def test_only_meaningful_messages_count_as_activity(self):
        activity = Activity(sdk)
        for message, active in (
            (stream_event({"type": "ping"}), False),
            (
                stream_event(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": ""},
                    }
                ),
                False,
            ),
            (
                stream_event(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "hello"},
                    }
                ),
                True,
            ),
            (
                stream_event(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "thinking_delta", "thinking": ""},
                    }
                ),
                True,
            ),
            (sdk.SystemMessage(subtype="init", data={}), True),
            (sdk.SystemMessage(subtype="compact_boundary", data={}), True),
            (sdk.SystemMessage(subtype="status", data={}), False),
            (sdk.AssistantMessage(content=[], model="main"), True),
            (sdk.UserMessage(content="tool result"), True),
            (workflow_started(), True),
            (workflow_finished(), True),
            (result_message(), True),
        ):
            with self.subTest(message=message):
                self.assertEqual(activity.observe(message), active)

    def test_task_progress_ignores_elapsed_time_and_repeated_state(self):
        activity = Activity(sdk)
        for tokens, uses, duration, active in (
            (1, 0, 10, True),
            (1, 0, 20, False),
            (2, 0, 30, True),
            (2, 1, 40, True),
        ):
            message = sdk.TaskProgressMessage(
                subtype="task_progress",
                data={},
                task_id="task",
                description="working",
                uuid="progress",
                session_id=SESSION,
                usage={
                    "total_tokens": tokens,
                    "tool_uses": uses,
                    "duration_ms": duration,
                },
            )
            with self.subTest(usage=message.usage):
                self.assertEqual(activity.observe(message), active)
        for patch, active in (
            ({"status": "running"}, True),
            ({"status": "running"}, False),
            ({"end_time": 10}, False),
            ({"status": "completed", "result": "done"}, True),
        ):
            with self.subTest(patch=patch):
                self.assertEqual(
                    activity.observe(
                        sdk.TaskUpdatedMessage(
                            subtype="task_updated",
                            data={},
                            task_id="task",
                            patch=patch,
                        )
                    ),
                    active,
                )


class AdapterRulesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.job = {
            "base": {"session_id": SESSION},
            "config_dir": str(self.root),
            "cwd": str(self.root),
            "workflow": False,
            "title": "Review",
            "prompt": "Review this project",
            "effort": "high",
        }

    def observed(self, messages):
        results = Results(self.job, sdk)
        with mock.patch("_sdk.emit"):
            for message in messages:
                results.accept(message)
        return results.outcome(Approvals(self.job, Clock(), sdk))

    def test_result_status_and_export_follow_adapter_rules(self):
        success = result_message(result="answer")
        for outcome, overrides, status, code in (
            (self.observed([success]), {}, "completed", 0),
            (self.observed([]), {}, "failed", 1),
            (
                self.observed([result_message(is_error=True, result="error")]),
                {},
                "failed",
                1,
            ),
            (
                self.observed(
                    [result_message(permission_denials=[{"tool_name": "Bash"}])]
                ),
                {},
                "needs_permission",
                3,
            ),
            (None, {"reason": "dependency_missing"}, "dependency_missing", 2),
            ({"approval_ending": ["denied", "closed"]}, {}, "denied", 3),
            ({"approval_ending": ["timed_out", "expired"]}, {}, "timed_out", 124),
            (self.observed([success]), {"reason": "cancelled"}, "cancelled", 130),
            (self.observed([success]), {"reason": "timed_out"}, "timed_out", 124),
        ):
            with self.subTest(status=status, outcome=outcome):
                event, exit_code = finalize(self.job, outcome, **overrides)
                self.assertEqual((event["status"], exit_code), (status, code))
                if status != "completed":
                    self.assertIsNone(event["answer_file"])
                if outcome is None:
                    self.assertIsNone(event["summary"]["tokens"])

    def test_workflow_requires_completion_and_a_later_synthesis(self):
        self.job["workflow"] = True
        start, finish, answer = (
            workflow_started(),
            workflow_finished(),
            result_message(result="synthesis"),
        )
        for messages, status in (
            ([answer], "workflow_not_started"),
            ([start, answer], "incomplete"),
            ([start, answer, finish], "incomplete"),
            ([start, workflow_finished(status="failed"), answer], "incomplete"),
            ([start, finish, answer], "completed"),
        ):
            with self.subTest(messages=messages):
                event, _ = finalize(self.job, self.observed(messages))
                self.assertEqual(event["status"], status)
                self.assertIsNone(event["answer_file"])

    def test_result_metadata_uses_main_agent_and_checks_session(self):
        observation = self.observed(
            [
                sdk.AssistantMessage(content=[], model="main"),
                sdk.AssistantMessage(
                    content=[], model="child", parent_tool_use_id="tool"
                ),
                sdk.AssistantMessage(content=[], model="<synthetic>"),
                result_message(model_usage={"main": {}, "auxiliary": {}}),
            ]
        )
        event, _ = finalize(self.job, observation)
        self.assertEqual(
            event["summary"]["configurations"], [{"model": "main", "effort": "high"}]
        )
        with self.assertRaisesRegex(ValueError, "different session"):
            self.observed([result_message(session_id="other")])

    def test_presentation_failures_do_not_lose_success_or_raw_answer(self):
        answer = "# Ответ\r\n\n`x < y`\n"
        outcome = self.observed([result_message(result=answer)])
        for summary_fails, export_fails in ((True, False), (False, True), (True, True)):
            with self.subTest(summary=summary_fails, export=export_fails):
                import _result

                with (
                    mock.patch.object(
                        _result,
                        "execution_summary",
                        wraps=_result.execution_summary,
                        **(
                            {"side_effect": RuntimeError("summary unavailable")}
                            if summary_fails
                            else {}
                        ),
                    ),
                    mock.patch.object(
                        _result,
                        "save_answer",
                        wraps=_result.save_answer,
                        **(
                            {"side_effect": OSError("export unavailable")}
                            if export_fails
                            else {}
                        ),
                    ),
                ):
                    event, exit_code = finalize(self.job, outcome)
                self.assertEqual((event["status"], exit_code), ("completed", 0))
                self.assertEqual(event["result"], answer)
                self.assertTrue(event["presentation_warnings"])
                if export_fails:
                    self.assertIsNone(event["answer_file"])
                else:
                    self.assertEqual(
                        Path(event["answer_file"]).read_bytes(), answer.encode()
                    )
                if summary_fails:
                    self.assertIsNone(event["summary"]["tokens"])

    def test_question_validation_preserves_question_and_rejects_invalid_answers(self):
        approvals = Approvals(self.job, Clock(), sdk)
        question = {
            "questions": [
                {"question": "Format?"},
                {"question": "Sections?", "multiSelect": True},
            ]
        }
        valid = {"Format?": "Custom text", "Sections?": ["Summary", "Details"]}
        self.assertEqual(
            approvals.answer(question, {"answers": valid, "questions": []}),
            {**question, "answers": valid},
        )
        self.assertEqual(
            approvals.answer(question, {"response": "Use defaults"}),
            {**question, "response": "Use defaults"},
        )
        for reply in (
            {},
            {"answers": {"Format?": "JSON"}},
            {"answers": {**valid, "extra": "x"}},
            {"answers": {**valid, "Format?": ["JSON"]}},
            {"answers": {**valid, "Sections?": []}},
            {"answers": {**valid, "Sections?": [True]}},
            {"response": " "},
            {"response": "general", "answers": valid},
        ):
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                approvals.answer(question, reply)

    def test_cli_context_bounds_and_timeout_alias(self):
        for value in ("100000", "1000000"):
            self.assertEqual(claude_task.context_window_tokens(value), int(value))
        for value in ("99999", "1000001", "0", "-1", "text"):
            with (
                self.subTest(value=value),
                self.assertRaises((argparse.ArgumentTypeError, ValueError)),
            ):
                claude_task.context_window_tokens(value)
        with mock.patch.object(
            sys, "argv", ["claude_task.py", "--cwd", ".", "--timeout", "12"]
        ):
            self.assertEqual(claude_task.parse_args().idle_timeout, 12)
        with (
            mock.patch.object(
                sys, "argv", ["claude_task.py", "--cwd", ".", "--context-window", "0"]
            ),
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as error,
        ):
            claude_task.parse_args()
        self.assertEqual(error.exception.code, 2)
