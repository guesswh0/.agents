import json
import importlib.util
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "claude_task.py"
FAKE_SDK = Path(__file__).resolve().parent / "fake_sdk"


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project with spaces"
        self.project.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "claude"
        fake.write_text(
            "#!"
            + sys.executable
            + '\nimport os,signal,time\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nPath("child.pid").write_text(str(os.getpid()))\ntime.sleep(60)\n'
        )
        fake.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "CLAUDE_CONFIG_DIR": str(self.root / "config"),
            "PYTHONPATH": str(FAKE_SDK),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.brief = self.root / "brief.txt"
        self.brief.write_text("Review this project")
        self.command = [
            sys.executable,
            "-B",
            str(ADAPTER),
            "--cwd",
            str(self.project),
            "--prompt-file",
            str(self.brief),
        ]
        self.processes = []
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=6)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=6)
        for name in ["child.pid", "sdk.pid"]:
            path = self.project / name
            if path.exists():
                try:
                    os.kill(int(path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def start(self, *args, case="success", workflow_permission="ask", requests=None):
        process = subprocess.Popen(
            [*self.command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                **self.env,
                "FAKE_SDK_CASE": case,
                "FAKE_WORKFLOW_PERMISSION": workflow_permission,
                "FAKE_REQUESTS": json.dumps(requests),
            },
            start_new_session=True,
            bufsize=0,
        )
        self.processes.append(process)
        return process

    def next_event(self, process, desired, timeout=8):
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(max(0, deadline - time.monotonic())):
                    break
                line = process.stdout.readline()
                if not line:
                    break
                event = json.loads(line)
                if event["type"] == desired:
                    return event
        self.fail(f"no {desired} event")

    def decision(self, process, event, decision="approve", **overrides):
        message = {
            "type": "approval",
            "request_id": event["request_id"],
            "input_sha256": event["input_sha256"],
            "decision": decision,
            **overrides,
        }
        process.stdin.write((json.dumps(message) + "\n").encode())
        process.stdin.flush()

    def finish(self, process, timeout=8):
        output, errors = process.communicate(timeout=timeout)
        events = [json.loads(line) for line in output.splitlines()]
        self.assertFalse(any(event["type"].startswith("_") for event in events))
        results = [event for event in events if event["type"] == "result"]
        self.assertEqual(len(results), 1, (events, errors.decode()))
        self.assertEqual(
            sum(
                event["type"] in ("approval_required", "question_required")
                for event in events
            ),
            0,
            events,
        )
        self.assertNotIn(b"Traceback", errors)
        return results[0]

    def wait_for_file(self, name):
        path = self.project / name
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(path.exists())
        return int(path.read_text())

    def assert_stopped(self, pid, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        self.fail(f"process {pid} is still running")

    def test_normal_result_and_literal_prompt(self):
        prompt = "Кедр $(touch injected) `touch injected`"
        self.brief.write_text(prompt)
        result = self.finish(self.start("--model", "opus", "--effort", "low"))
        self.assertEqual(result["status"], "completed")
        returned = json.loads(result["result"])
        self.assertEqual(returned["prompt"], prompt)
        self.assertFalse((self.project / "injected").exists())
        options = returned["options"]
        self.assertEqual(options["cwd"], str(self.project))
        self.assertEqual(options["setting_sources"], ["user", "project", "local"])
        self.assertEqual(
            options["system_prompt"], {"type": "preset", "preset": "claude_code"}
        )
        self.assertIsNone(options["permission_mode"])
        self.assertEqual(options["model"], "opus")
        self.assertEqual(options["effort"], "low")

    def test_model_and_effort_only_override_when_requested(self):
        cases = [
            ([], None, None),
            (["--model", "sonnet"], "sonnet", None),
            (["--effort", "high"], None, "high"),
        ]
        for arguments, model, effort in cases:
            with self.subTest(arguments=arguments):
                result = self.finish(self.start(*arguments))
                self.assertEqual(result["status"], "completed")
                options = json.loads(result["result"])["options"]
                self.assertEqual(options["model"], model)
                self.assertEqual(options["effort"], effort)
                self.assertEqual(result["requested_model"], model)
                self.assertEqual(result["effort"], effort)

    def test_resume_keeps_id(self):
        session = str(uuid.uuid4())
        result = self.finish(self.start("--resume", session))
        options = json.loads(result["result"])["options"]
        self.assertEqual(result["session_id"], session)
        self.assertEqual(options["resume"], session)
        self.assertIsNone(options["session_id"])

    def test_autocompact_is_session_scoped_without_a_context_override(self):
        config = Path(self.env["CLAUDE_CONFIG_DIR"])
        config.mkdir()
        settings = config / "settings.json"
        original = '{"autoCompactEnabled": false, "env": {"DISABLE_COMPACT": "1"}}\n'
        settings.write_text(original)
        self.env.update(DISABLE_COMPACT="1", DISABLE_AUTO_COMPACT="1")
        for arguments in ([], ["--resume", str(uuid.uuid4())]):
            with self.subTest(arguments=arguments):
                result = self.finish(self.start(*arguments))
                self.assertEqual(result["status"], "completed")
                options = json.loads(result["result"])["options"]
                expected_env = {"DISABLE_COMPACT": "0", "DISABLE_AUTO_COMPACT": "0"}
                self.assertEqual(
                    json.loads(options["settings"]),
                    {
                        "autoCompactEnabled": True,
                        "env": expected_env,
                    },
                )
                self.assertEqual(
                    options["env"], {"CLAUDE_CONFIG_DIR": str(config), **expected_env}
                )
                self.assertEqual(settings.read_text(), original)
                self.assertEqual(self.env["DISABLE_COMPACT"], "1")
                self.assertEqual(
                    options["setting_sources"], ["user", "project", "local"]
                )

    def test_answer_export_is_verbatim_and_resume_preserves_previous_answer(self):
        session = str(uuid.uuid4())
        first = self.finish(self.start("--resume", session, "--title", "Проверка API"))
        path = Path(first["answer_file"])
        self.assertEqual(path.read_bytes(), first["result"].encode("utf-8"))
        self.assertTrue(path.is_relative_to(Path(self.env["CLAUDE_CONFIG_DIR"])))
        self.assertEqual(first["summary"]["title"], "Проверка API")
        self.assertEqual(first["summary"]["tokens"], 190)
        self.assertGreaterEqual(first["summary"]["duration_ms"], 0)
        self.brief.write_text("A different task")
        second = self.finish(self.start("--resume", session))
        self.assertNotEqual(second["answer_file"], first["answer_file"])
        self.assertEqual(path.read_bytes(), first["result"].encode("utf-8"))
        self.assertEqual(
            Path(second["answer_file"]).read_bytes(), second["result"].encode("utf-8")
        )

    def test_context_window_override_does_not_persist_on_resume(self):
        config = Path(self.env["CLAUDE_CONFIG_DIR"])
        config.mkdir()
        settings = config / "settings.json"
        original = '{"autoCompactEnabled": false, "autoCompactWindow": 500000}\n'
        settings.write_text(original)
        self.env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "500000"
        first = self.finish(self.start("--context-window", "253123"))
        options = json.loads(first["result"])["options"]
        self.assertEqual(options["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "253123")
        self.assertEqual(
            json.loads(options["settings"])["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"],
            "253123",
        )
        second = self.finish(self.start("--resume", first["session_id"]))
        options = json.loads(second["result"])["options"]
        self.assertNotIn("CLAUDE_CODE_AUTO_COMPACT_WINDOW", options["env"])
        self.assertNotIn(
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW", json.loads(options["settings"])["env"]
        )
        self.assertEqual(settings.read_text(), original)
        self.assertEqual(self.env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "500000")

    def test_context_window_supported_bounds_and_workflows(self):
        for window in ("100000", "1000000"):
            with self.subTest(window=window):
                result = self.finish(
                    self.start(
                        "--workflow",
                        "--context-window",
                        window,
                        case="workflow_metrics",
                        workflow_permission="allow",
                    )
                )
                self.assertEqual(result["status"], "completed")
                options = json.loads((self.project / "sdk-options.json").read_text())
                self.assertEqual(
                    options["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], window
                )
                self.assertTrue(json.loads(options["settings"])["autoCompactEnabled"])

    def test_invalid_context_window_does_not_start_sdk(self):
        for value in ("99999", "1000001", "0", "-1", "text"):
            with self.subTest(value=value):
                process = self.start("--context-window", value)
                output, errors = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 2)
                self.assertIn(b"--context-window", errors)
                self.assertEqual(output, b"")
                self.assertFalse((self.project / "sdk.pid").exists())

    def test_answer_export_failure_preserves_success_and_answer(self):
        config = Path(self.env["CLAUDE_CONFIG_DIR"])
        config.mkdir()
        (config / "claude-agent").write_text("not a directory")
        result = self.finish(self.start())
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result["answer_file"])
        self.assertTrue(result["result"])
        self.assertTrue(result["presentation_warnings"])

    def test_failed_agent_does_not_export_successful_answer(self):
        result = self.finish(self.start(case="api_error"))
        self.assertIsNone(result["answer_file"])

    def test_unset_config_directory_stays_unset_for_native_auth(self):
        spec = importlib.util.spec_from_file_location("entrypoint", ADAPTER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        environment = {
            key: value for key, value in self.env.items() if key != "CLAUDE_CONFIG_DIR"
        }
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch.object(module.Path, "home", return_value=self.root),
            mock.patch.object(sys, "argv", [str(ADAPTER), *self.command[3:]]),
        ):
            job = module.prepare(module.parse_args())
        self.assertEqual(job["claude_env"], {})

    def test_tool_profiles_and_shell_rules(self):
        result = self.finish(
            self.start("--access", "edit", "--allow-command", "git diff *")
        )
        options = json.loads(result["result"])["options"]
        self.assertIn("Write", options["tools"])
        self.assertIn("Bash(git diff *)", options["allowed_tools"])
        self.assertNotIn("Bash", options["allowed_tools"])
        self.assertNotIn("Workflow", options["tools"])

    def test_none_profile_and_mode_override(self):
        result = self.finish(
            self.start("--access", "none", "--permission-mode", "auto")
        )
        options = json.loads(result["result"])["options"]
        self.assertEqual(options["tools"], ["AskUserQuestion"])
        self.assertNotIn("AskUserQuestion", options["allowed_tools"])
        self.assertEqual(options["permission_mode"], "auto")

    def test_errors_are_not_success(self):
        for case in ["api_error", "wrong_session", "no_result"]:
            with self.subTest(case=case):
                self.assertEqual(self.finish(self.start(case=case))["status"], "failed")
        self.assertEqual(
            self.finish(self.start(case="denied"))["status"], "needs_permission"
        )

    def test_empty_brief_does_not_start_sdk(self):
        self.brief.write_text(" \n")
        self.assertEqual(self.finish(self.start())["status"], "invalid_input")
        self.assertFalse((self.project / "sdk.pid").exists())

    def test_history_preflight_does_not_start_sdk(self):
        Path(self.env["CLAUDE_CONFIG_DIR"]).write_text("not a directory")
        self.assertEqual(self.finish(self.start())["status"], "history_unwritable")
        self.assertFalse((self.project / "sdk.pid").exists())

    def test_native_allowed_workflow_needs_no_adapter_approval(self):
        for mode in (None, "auto", "dontAsk"):
            with self.subTest(mode=mode):
                arguments = ["--permission-mode", mode] if mode else []
                result = self.finish(
                    self.start(
                        "--workflow",
                        *arguments,
                        case="workflow",
                        workflow_permission="allow",
                    )
                )
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["result"], "finished")
                self.assertIsNone(result["answer_file"])
                self.assertIsNone(result["summary"]["tokens"])
                self.assertTrue(result["presentation_warnings"])
                self.assertEqual(result["workflows"]["task-0"]["status"], "completed")
                options = json.loads((self.project / "sdk-options.json").read_text())
                self.assertEqual(options["permission_mode"], mode)
                self.assertEqual(
                    options["setting_sources"], ["user", "project", "local"]
                )

    def test_native_denied_workflow_never_launches_or_prompts(self):
        for permission, mode in (("deny", None), ("deny", "auto"), ("ask", "dontAsk")):
            with self.subTest(permission=permission, mode=mode):
                arguments = ["--permission-mode", mode] if mode else []
                result = self.finish(
                    self.start(
                        "--workflow",
                        *arguments,
                        case="workflow",
                        workflow_permission=permission,
                    )
                )
                self.assertEqual(result["status"], "needs_permission")
                self.assertEqual(result["workflows"], {})
                self.assertEqual(
                    result["permission_denials"], [{"tool_name": "Workflow"}]
                )
                self.assertFalse((self.project / "approved-0.json").exists())

    def test_workflow_summary_reads_native_metrics_without_exporting_answers(self):
        result = self.finish(
            self.start(
                "--workflow",
                "--effort",
                "high",
                case="workflow_metrics",
                workflow_permission="allow",
            )
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["summary"]["tokens"], 123)
        self.assertEqual(result["summary"]["duration_ms"], 500)
        phase = result["summary"]["workflows"][0]["phases"][0]
        self.assertEqual(phase["title"], "Check")
        self.assertEqual(phase["duration_ms"], 400)
        self.assertEqual(phase["configurations"], [{"model": "worker", "effort": None}])
        self.assertFalse(result["presentation_warnings"])
        self.assertIsNone(result["answer_file"])
        self.assertFalse(
            (Path(self.env["CLAUDE_CONFIG_DIR"]) / "claude-agent").exists()
        )

    def test_native_ask_waits_for_exact_approval_and_final_synthesis(self):
        process = self.start("--workflow", case="workflow")
        event = self.next_event(process, "approval_required")
        self.assertFalse((self.project / "approved-0.json").exists())
        self.decision(process, event, request_id="wrong")
        self.next_event(process, "control_error")
        self.assertFalse((self.project / "approved-0.json").exists())
        self.decision(process, event, input_sha256="wrong")
        self.next_event(process, "control_error")
        self.decision(process, event)
        result = self.finish(process)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"], "finished")
        self.assertEqual(result["workflows"]["task-0"]["status"], "completed")

    def test_native_ask_in_auto_mode_still_waits(self):
        process = self.start("--workflow", "--permission-mode", "auto", case="workflow")
        event = self.next_event(process, "approval_required")
        self.assertFalse((self.project / "approved-0.json").exists())
        self.decision(process, event)
        self.assertEqual(self.finish(process)["status"], "completed")

    def test_workflow_denial_never_launches(self):
        process = self.start("--workflow", case="workflow")
        event = self.next_event(process, "approval_required")
        self.decision(process, event, "deny")
        result = self.finish(process)
        self.assertEqual(result["status"], "denied")
        self.assertFalse((self.project / "approved-0.json").exists())

    def test_closed_approval_input_never_launches(self):
        process = self.start("--workflow", case="workflow")
        self.next_event(process, "approval_required")
        result = self.finish(process)
        self.assertEqual(result["status"], "denied")
        self.assertFalse((self.project / "approved-0.json").exists())

    def test_approval_cannot_be_reused(self):
        process = self.start("--workflow", case="workflow_twice")
        first = self.next_event(process, "approval_required")
        self.decision(process, first)
        second = self.next_event(process, "approval_required")
        self.assertNotEqual(first["request_id"], second["request_id"])
        self.decision(process, first)
        self.next_event(process, "control_error")
        self.assertFalse((self.project / "approved-1.json").exists())
        self.decision(process, second)
        self.assertEqual(self.finish(process)["status"], "completed")

    def test_script_path_is_frozen_before_approval(self):
        script = self.project / "workflow.js"
        script.write_text("return 'original'")
        process = self.start("--workflow", case="workflow_file")
        event = self.next_event(process, "approval_required")
        script.write_text("return 'changed'")
        self.decision(process, event)
        self.assertEqual(self.finish(process)["status"], "completed")
        approved = json.loads((self.project / "approved-0.json").read_text())
        self.assertEqual(approved["script"], "return 'original'")
        self.assertNotIn("scriptPath", approved)

    def test_approval_wait_does_not_consume_execution_timeout(self):
        process = self.start("--workflow", "--timeout", "0.25", case="workflow")
        event = self.next_event(process, "approval_required")
        time.sleep(0.45)
        self.assertIsNone(process.poll())
        self.decision(process, event)
        self.assertEqual(self.finish(process)["status"], "completed")

    def test_approval_wait_expires_without_launching(self):
        process = self.start(
            "--workflow", "--approval-timeout", "0.15", case="workflow"
        )
        self.next_event(process, "approval_required")
        time.sleep(0.25)
        result = self.finish(process)
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(
            result["permission_denials"][0]["reason"], "User input wait expired."
        )
        self.assertFalse((self.project / "approved-0.json").exists())

    def test_closed_approval_channel_rejects_retries(self):
        for ending in ("deny", "eof", "timeout"):
            with self.subTest(ending=ending):
                process = self.start(
                    "--workflow", "--approval-timeout", "0.3", case="workflow_retry"
                )
                event = self.next_event(process, "approval_required")
                if ending == "deny":
                    self.decision(process, event, "deny")
                elif ending == "timeout":
                    process.wait(timeout=3)
                result = self.finish(process)
                self.assertEqual(
                    result["status"], "timed_out" if ending == "timeout" else "denied"
                )
                self.assertEqual(process.returncode, 124 if ending == "timeout" else 3)
                self.assertEqual(len(result["permission_denials"]), 2)
                self.assertEqual(
                    result["permission_denials"][0]["reason"],
                    result["permission_denials"][1]["reason"],
                )
                self.assertFalse((self.project / "approved-1.json").exists())

    def test_queued_requests_observe_closed_approval_channel(self):
        for ending in ("deny", "eof", "timeout"):
            with self.subTest(ending=ending):
                ready = self.project / "concurrent-ready"
                ready.unlink(missing_ok=True)
                process = self.start(
                    "--workflow",
                    "--approval-timeout",
                    "0.5",
                    case="workflow_concurrent",
                )
                event = self.next_event(process, "approval_required")
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists())
                if ending == "deny":
                    self.decision(process, event, "deny")
                elif ending == "timeout":
                    process.wait(timeout=3)
                result = self.finish(process)
                self.assertEqual(
                    result["status"], "timed_out" if ending == "timeout" else "denied"
                )
                self.assertEqual(process.returncode, 124 if ending == "timeout" else 3)
                self.assertEqual(
                    json.loads((self.project / "decisions.json").read_text()),
                    ["deny", "deny"],
                )

    def test_pending_failed_and_unsynthesized_workflows_are_incomplete(self):
        for case in ["workflow_pending", "workflow_failed", "workflow_no_synthesis"]:
            with self.subTest(case=case):
                process = self.start("--workflow", case=case)
                event = self.next_event(process, "approval_required")
                self.decision(process, event)
                self.assertEqual(self.finish(process)["status"], "incomplete")

    def test_plain_answer_does_not_satisfy_workflow_request(self):
        self.assertEqual(
            self.finish(self.start("--workflow"))["status"], "workflow_not_started"
        )

    def question(self):
        return {
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "question": "Which format?",
                        "header": "Format",
                        "options": [{"label": "Text"}, {"label": "JSON"}],
                        "multiSelect": False,
                    },
                    {
                        "question": "Which sections?",
                        "header": "Sections",
                        "options": [{"label": "Summary"}, {"label": "Details"}],
                        "multiSelect": True,
                    },
                ],
            },
        }

    def reply(self, process, event, **fields):
        message = {
            "type": "answer",
            "request_id": event["request_id"],
            "input_sha256": event["input_sha256"],
            **fields,
        }
        process.stdin.write((json.dumps(message) + "\n").encode())
        process.stdin.flush()

    def handled(self, index):
        return json.loads((self.project / f"handled-{index}.json").read_text())

    def test_native_tool_requests_are_relayed_without_workflow(self):
        requests = [
            {
                "name": "Read",
                "input": {"file_path": "data.txt", "scriptPath": "literal"},
            },
            {"name": "Write", "input": {"file_path": "result.txt", "content": "value"}},
            {"name": "Bash", "input": {"command": "git diff --stat"}},
            {"name": "mcp__example__lookup", "input": {"id": "item-1"}},
        ]
        process = self.start(
            "--access",
            "edit",
            "--allow-command",
            "git diff *",
            case="interaction",
            requests=requests,
        )
        for index, request in enumerate(requests):
            event = self.next_event(process, "approval_required")
            self.assertEqual(event["tool_name"], request["name"])
            self.assertEqual(event["tool_use_id"], f"call-{index}")
            self.assertEqual(event["tool_input"], request["input"])
            self.decision(process, event, updated_input={"injected": True})
        self.assertEqual(self.finish(process)["status"], "completed")
        for index, request in enumerate(requests):
            self.assertEqual(self.handled(index)["updated_input"], request["input"])

    def test_tool_denial_does_not_close_other_requests(self):
        request = {"name": "Read", "input": {"file_path": "data.txt"}}
        process = self.start(case="interaction", requests=[request, request])
        first = self.next_event(process, "approval_required")
        self.decision(process, first, "deny", message="Use the public copy instead")
        second = self.next_event(process, "approval_required")
        self.assertNotEqual(first["request_id"], second["request_id"])
        self.decision(process, first)
        self.next_event(process, "control_error")
        self.decision(process, second)
        self.assertEqual(self.finish(process)["status"], "needs_permission")
        self.assertEqual(self.handled(0)["behavior"], "deny")
        self.assertEqual(self.handled(0)["message"], "Use the public copy instead")
        self.assertEqual(self.handled(1)["behavior"], "allow")

    def test_questions_preserve_input_and_accept_free_text_and_multi_select(self):
        request = self.question()
        answers = {
            "Which format?": "Custom Markdown",
            "Which sections?": ["Summary", "Details"],
        }
        process = self.start("--access", "none", case="interaction", requests=[request])
        event = self.next_event(process, "question_required")
        self.assertEqual(event["tool_name"], "AskUserQuestion")
        self.assertEqual(event["tool_input"], request["input"])
        self.reply(
            process, event, answers=answers, questions=[{"question": "injected"}]
        )
        self.assertEqual(self.finish(process)["status"], "completed")
        self.assertEqual(
            self.handled(0)["updated_input"], {**request["input"], "answers": answers}
        )

    def test_questions_accept_general_response(self):
        request = self.question()
        process = self.start(case="interaction", requests=[request])
        event = self.next_event(process, "question_required")
        self.reply(process, event, response="Use the existing format")
        self.assertEqual(self.finish(process)["status"], "completed")
        self.assertEqual(
            self.handled(0)["updated_input"],
            {**request["input"], "response": "Use the existing format"},
        )

    def test_invalid_question_replies_leave_request_waiting(self):
        process = self.start(case="interaction", requests=[self.question()])
        event = self.next_event(process, "question_required")
        valid = {"Which format?": "JSON", "Which sections?": "Summary, Details"}
        invalid = [
            {"request_id": "wrong", "answers": valid},
            {"input_sha256": "wrong", "answers": valid},
            {"type": "approval", "decision": "approve"},
            {},
            {"answers": {"Which format?": "JSON"}},
            {"answers": {**valid, "extra": "answer"}},
            {"answers": {**valid, "Which format?": ["JSON"]}},
            {"answers": {**valid, "Which sections?": []}},
            {"answers": {**valid, "Which sections?": [True]}},
            {"response": " "},
            {"response": "general", "answers": valid},
        ]
        for fields in invalid:
            with self.subTest(fields=fields):
                self.reply(process, event, **fields)
                self.next_event(process, "control_error")
                self.assertFalse((self.project / "handled-0.json").exists())
        self.reply(process, event, answers=valid)
        self.assertEqual(self.finish(process)["status"], "completed")

    def test_skipping_question_keeps_channel_open(self):
        request = self.question()
        process = self.start(case="interaction", requests=[request, request])
        first = self.next_event(process, "question_required")
        self.reply(process, first, decision="skip")
        second = self.next_event(process, "question_required")
        self.reply(process, second, response="Use the defaults")
        self.assertEqual(self.finish(process)["status"], "needs_permission")
        self.assertEqual(self.handled(0)["behavior"], "deny")
        self.assertEqual(self.handled(1)["behavior"], "allow")

    def test_questions_and_permissions_share_one_channel(self):
        requests = [
            self.question(),
            {"name": "Read", "input": {"file_path": "data.txt"}},
        ]
        process = self.start(case="interaction", requests=requests)
        question = self.next_event(process, "question_required")
        self.reply(process, question, response="Read the current data")
        permission = self.next_event(process, "approval_required")
        self.reply(process, question, response="Read the current data")
        self.next_event(process, "control_error")
        self.decision(process, permission)
        self.assertEqual(self.finish(process)["status"], "completed")

    def test_input_channel_closure_and_timeout_reject_later_requests(self):
        for request, event_type in (
            (self.question(), "question_required"),
            ({"name": "Read", "input": {"file_path": "data.txt"}}, "approval_required"),
        ):
            for ending in ("eof", "timeout"):
                with self.subTest(tool=request["name"], ending=ending):
                    process = self.start(
                        "--input-timeout",
                        "0.2",
                        case="interaction",
                        requests=[request, request],
                    )
                    self.next_event(process, event_type)
                    if ending == "timeout":
                        process.wait(timeout=3)
                    result = self.finish(process)
                    self.assertEqual(
                        result["status"],
                        "timed_out" if ending == "timeout" else "denied",
                    )
                    self.assertEqual(self.handled(0)["behavior"], "deny")
                    self.assertEqual(self.handled(1)["behavior"], "deny")

    def test_parent_signals_stop_sdk_and_claude(self):
        for signum in [signal.SIGTERM, signal.SIGHUP, signal.SIGKILL]:
            with self.subTest(signal=signum):
                for name in ["sdk.pid", "child.pid"]:
                    (self.project / name).unlink(missing_ok=True)
                process = self.start(case="stubborn")
                child = self.wait_for_file("child.pid")
                sdk = self.wait_for_file("sdk.pid")
                if signum == signal.SIGKILL:
                    os.killpg(process.pid, signum)
                else:
                    process.send_signal(signum)
                result = self.finish(process)
                self.assertEqual(result["status"], "cancelled")
                self.assert_stopped(child)
                self.assert_stopped(sdk)

    def test_signal_during_timeout_cleanup_keeps_json_and_stops_children(self):
        process = self.start("--timeout", "1", case="stubborn")
        child = self.wait_for_file("child.pid")
        time.sleep(1.2)
        process.send_signal(signal.SIGTERM)
        result = self.finish(process)
        self.assertEqual(result["status"], "timed_out")
        self.assert_stopped(child)

    def test_active_stream_outlives_timeout(self):
        for flag in ("--idle-timeout", "--timeout"):
            with self.subTest(flag=flag):
                result = self.finish(self.start(flag, "2", case="active_stream"))
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["result"], "finished after sustained activity")
                self.assertGreaterEqual(result["summary"]["duration_ms"], 3000)

    def test_watchdog_runs_while_host_stdout_is_blocked(self):
        process = self.start("--workflow", "--idle-timeout", "1", case="blocked_host")
        child = self.wait_for_file("child.pid")
        sdk = self.wait_for_file("sdk.pid")
        self.assert_stopped(sdk, timeout=6)
        self.assert_stopped(child)
        result = self.finish(process)
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(process.returncode, 124)

    def test_parent_loss_stops_children_while_host_stdout_is_blocked(self):
        for signum in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=signum):
                for name in ("sdk.pid", "child.pid"):
                    (self.project / name).unlink(missing_ok=True)
                process = self.start(
                    "--workflow", "--idle-timeout", "60", case="blocked_host"
                )
                child = self.wait_for_file("child.pid")
                sdk = self.wait_for_file("sdk.pid")
                process.send_signal(signum)
                self.assert_stopped(sdk, timeout=5)
                self.assert_stopped(child)
                self.assertEqual(self.finish(process)["status"], "cancelled")

    def test_timeout_starts_from_last_activity(self):
        process = self.start("--idle-timeout", "0.4", case="active_then_idle")
        result = self.finish(process)
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(process.returncode, 124)
        self.assertIn("No Claude activity", result["error"])
        self.assertGreaterEqual(result["summary"]["duration_ms"], 1400)

    def test_ping_events_do_not_keep_a_stalled_run_alive(self):
        result = self.finish(
            self.start("--idle-timeout", "0.4", case="empty_heartbeats")
        )
        self.assertEqual(result["status"], "timed_out")

    def test_workflow_requires_changing_progress(self):
        for case, status in (
            ("progress_active", "completed"),
            ("progress_stalled", "timed_out"),
        ):
            with self.subTest(case=case):
                result = self.finish(
                    self.start("--workflow", "--idle-timeout", "0.4", case=case)
                )
                self.assertEqual(result["status"], status)

    def test_watchdog_stops_a_blocked_sdk_event_loop(self):
        process = self.start("--idle-timeout", "0.4", case="blocked_event_loop")
        pid = self.wait_for_file("sdk.pid")
        result = self.finish(process)
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(process.returncode, 124)
        self.assert_stopped(pid)

    def test_answer_resets_the_idle_budget_after_user_wait(self):
        process = self.start(
            "--idle-timeout", "0.4", case="slow_question", requests=[self.question()]
        )
        event = self.next_event(process, "question_required")
        time.sleep(0.5)
        self.assertIsNone(process.poll())
        self.reply(process, event, response="Use the defaults")
        self.assertEqual(self.finish(process)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
