import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "_output.py"
SPEC = importlib.util.spec_from_file_location("event_output", SOURCE)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class OutputTests(unittest.TestCase):
    def test_regular_file_preserves_unicode_and_event_order(self):
        events = [
            {"type": "progress", "text": "Кедр" * 100000},
            {"type": "result", "status": "completed"},
        ]
        with tempfile.TemporaryFile() as target:
            output = module.EventOutput(target.fileno())
            try:
                for event in events:
                    output.send(event)
            finally:
                output.close()
            output.check()
            self.assertFalse(output.thread.is_alive())
            target.seek(0)
            self.assertEqual([json.loads(line) for line in target], events)

    def test_unread_pipe_cannot_block_output_shutdown(self):
        reader, writer = os.pipe()
        output = module.EventOutput(writer)
        try:
            output.send({"text": "x" * 1000000})
            output.close(timeout=0.05)
            self.assertTrue(output.abandoned)
        finally:
            os.close(reader)
            output.thread.join(2)
            os.close(writer)
        self.assertFalse(output.thread.is_alive())
        self.assertTrue(output.spool.closed)

    def test_closed_reader_reports_broken_pipe(self):
        reader, writer = os.pipe()
        os.close(reader)
        output = module.EventOutput(writer)
        try:
            output.send({"type": "result"})
            output.close()
            with self.assertRaises(BrokenPipeError):
                output.check()
        finally:
            os.close(writer)


if __name__ == "__main__":
    unittest.main()
