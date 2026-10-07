from dataclasses import replace
import sys
from unittest import mock
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid


with mock.patch.object(
    sys, "path", [str(Path(__file__).resolve().parents[1] / "scripts"), *sys.path]
):
    from _contracts import Job


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "_presentation.py"
SPEC = importlib.util.spec_from_file_location("presentation", SOURCE)
presentation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(presentation)


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = Job(
            session_id=str(uuid.uuid4()),
            cwd=str(self.root),
            config_dir=str(self.root),
            prompt="Review this project",
            cli_path="claude",
            python=sys.executable,
            title="Check architecture",
            effort="high",
            workflow=True,
        )
        self.directory = (
            self.root
            / "projects"
            / "any-project-key"
            / self.job.session_id
            / "workflows"
        )
        self.directory.mkdir(parents=True)

    def document(self, task="current", **overrides):
        document = {
            "taskId": task,
            "workflowName": "audit",
            "startTime": 1000,
            "durationMs": 900,
            "agentCount": 3,
            "totalTokens": 600,
            "workflowProgress": [
                {"type": "workflow_phase", "index": 1, "title": "Check"},
                {"type": "workflow_phase", "index": 2, "title": "Verify"},
                {
                    "type": "workflow_agent",
                    "phaseIndex": 1,
                    "model": "model-a",
                    "tokens": 100,
                    "startedAt": 1000,
                    "durationMs": 400,
                },
                {
                    "type": "workflow_agent",
                    "phaseIndex": 1,
                    "model": "model-b",
                    "tokens": 200,
                    "startedAt": 1050,
                    "durationMs": 500,
                },
                {
                    "type": "workflow_agent",
                    "phaseIndex": 2,
                    "model": "model-a",
                    "effort": "low",
                    "tokens": 300,
                    "startedAt": 1600,
                    "durationMs": 300,
                },
            ],
            **overrides,
        }
        (self.directory / f"{task}.json").write_text(json.dumps(document))
        return document

    def summary(self, tasks=None):
        return presentation.execution_summary(
            self.job,
            {"input_tokens": 9000, "output_tokens": 1000},
            tasks
            if tasks is not None
            else {"current": {"status": "completed", "description": "audit"}},
            2000,
            ["coordinator"],
        )

    def test_phases_use_parallel_wall_time_and_native_token_totals(self):
        self.document()
        self.document("previous", totalTokens=999999)
        summary, warnings = self.summary()
        self.assertFalse(warnings)
        self.assertEqual(summary["tokens"], 600)
        self.assertEqual(summary["duration_ms"], 900)
        self.assertEqual(summary["agent_count"], 3)
        self.assertEqual(summary["token_scope"], "workflow_agents")
        phases = summary["workflows"][0]["phases"]
        self.assertEqual([phase["tokens"] for phase in phases], [300, 300])
        self.assertEqual([phase["duration_ms"] for phase in phases], [550, 300])
        self.assertEqual(
            phases[0]["configurations"],
            [
                {"model": "model-a", "effort": None},
                {"model": "model-b", "effort": None},
            ],
        )
        self.assertEqual(
            phases[1]["configurations"], [{"model": "model-a", "effort": "low"}]
        )
        self.assertNotIn("agents", phases[0])

    def test_multiple_workflows_do_not_sum_overlapping_durations(self):
        self.document()
        self.document("second", startTime=1200, durationMs=800)
        summary, warnings = self.summary(
            {
                key: {"status": "completed", "description": key}
                for key in ("current", "second")
            }
        )
        self.assertFalse(warnings)
        self.assertEqual(summary["duration_ms"], 1000)
        self.assertEqual(summary["tokens"], 1200)

    def test_missing_or_broken_native_data_stays_unknown(self):
        (self.directory / "broken.json").write_text("{")
        (self.directory / "invalid.json").write_text('{"taskId": []}')
        summary, warnings = self.summary()
        self.assertIsNone(summary["tokens"])
        self.assertIsNone(summary["duration_ms"])
        self.assertIsNone(summary["agent_count"])
        self.assertTrue(warnings)
        document = self.document()
        del document["workflowProgress"][2]["tokens"]
        del document["workflowProgress"][2]["durationMs"]
        phases = presentation.phase_summaries(document)
        self.assertIsNone(phases[0]["tokens"])
        self.assertIsNone(phases[0]["duration_ms"])

    def test_unphased_agents_are_kept(self):
        document = self.document()
        del document["workflowProgress"][2]["phaseIndex"]
        phases = presentation.phase_summaries(document)
        self.assertEqual(sum(phase["agent_count"] for phase in phases), 3)
        self.assertIsNone(phases[-1]["title"])

    def test_single_agent_cache_usage_unknown_fields_and_model_filtering(self):
        self.job = replace(self.job, workflow=False)
        usage = {
            "input_tokens": 10,
            "output_tokens": 3,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 7,
        }
        summary, _ = presentation.execution_summary(
            self.job, usage, {}, 40, ["actual", "<synthetic>"]
        )
        self.assertEqual(summary["tokens"], 40)
        self.assertEqual(
            summary["configurations"], [{"model": "actual", "effort": "high"}]
        )
        for usage in (
            None,
            {},
            {"input_tokens": 0},
            {"input_tokens": True, "output_tokens": 0},
        ):
            self.assertIsNone(presentation.tokens(usage))
        self.assertEqual(
            presentation.tokens({"input_tokens": 0, "output_tokens": 0}), 0
        )

    def test_answer_is_saved_without_reformatting(self):
        answer = "# Ответ\r\n\r\nКод: `x < y`\n\n"
        path = Path(presentation.save_answer(self.job, answer))
        self.assertEqual(path.read_bytes(), answer.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
