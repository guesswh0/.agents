from dataclasses import asdict
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

from _contracts import Job, Outcome, WorkerEvent
from _output import EventOutput
from _result import finalize


def signal_group(pid, signum):
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass


def group_exists(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


class Shutdown:
    def __init__(self, pid):
        self.pid = pid
        self.reason = None
        self.deadline = None

    def request(self, reason):
        # once cleanup starts, later signals must not change its cause
        if self.deadline is None:
            self.reason = reason
            self.cleanup()

    def cleanup(self):
        if self.deadline is None:
            self.deadline = time.monotonic() + 3
            signal_group(self.pid, signal.SIGTERM)

    def force_if_due(self):
        if self.deadline is None or time.monotonic() < self.deadline:
            return False
        signal_group(self.pid, signal.SIGKILL)
        return True


def supervise(lease_fd, job, output):
    reader, writer = os.pipe()
    process = None
    outcome = None
    error = None
    shutdown = None
    buffer = b""
    stdout_open = True
    last_activity = time.monotonic()
    input_wait = False
    timeout_error = f"No Claude activity for {job.idle_timeout:g} seconds."
    with selectors.DefaultSelector() as selector:
        selector.register(lease_fd, selectors.EVENT_READ, "lease")
        try:
            process = subprocess.Popen(
                [job.python, str(Path(__file__).with_name("_sdk.py")), str(reader)],
                pass_fds=(reader,),
                process_group=0,
                stdout=subprocess.PIPE,
            )
            shutdown = Shutdown(process.pid)
            os.close(reader)
            reader = None
            with os.fdopen(writer, "w", encoding="utf-8") as request:
                writer = None
                json.dump(asdict(job), request, ensure_ascii=False)
            selector.register(process.stdout, selectors.EVENT_READ, "output")

            while True:
                output.check()
                for key, _ in selector.select(0.1):
                    if key.data == "lease":
                        if not os.read(lease_fd, 1):
                            selector.unregister(lease_fd)
                            shutdown.request("cancelled")
                    else:
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            selector.unregister(process.stdout)
                            stdout_open = False
                        buffer += chunk
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            event = json.loads(line)
                            kind = event.get("type")
                            if kind == WorkerEvent.ACTIVITY:
                                last_activity = time.monotonic()
                            elif kind == WorkerEvent.INPUT_WAIT:
                                input_wait = True
                            elif kind == WorkerEvent.INPUT_RESUME:
                                input_wait = False
                                last_activity = time.monotonic()
                            elif kind == WorkerEvent.SHUTDOWN:
                                shutdown.request(event["status"])
                            elif outcome is None:
                                if kind == WorkerEvent.OUTCOME:
                                    outcome = Outcome.from_event(event)
                                else:
                                    output.send(event)
                if (
                    not input_wait
                    and time.monotonic() - last_activity >= job.idle_timeout
                ):
                    shutdown.request("timed_out")
                exited = process.poll() is not None
                if exited and group_exists(process.pid):
                    if outcome is None:
                        shutdown.request("failed")
                    else:
                        shutdown.cleanup()
                forced = shutdown.force_if_due()
                if (
                    exited
                    and not stdout_open
                    and (forced or not group_exists(process.pid))
                ):
                    break
            if outcome is None:
                error = "SDK worker ended before its final result"
            elif process.returncode and shutdown.reason is None:
                error = f"SDK worker exited with code {process.returncode}"
        except BrokenPipeError:
            raise
        except Exception as exc:
            error = str(exc)
        finally:
            if process is not None:
                signal_group(process.pid, signal.SIGKILL)
                process.wait()
                process.stdout.close()
            for fd in (reader, writer):
                if fd is not None:
                    os.close(fd)
    reason = shutdown.reason if shutdown is not None else None
    if reason == "timed_out":
        error = timeout_error
    return finalize(job, outcome, reason, error)


def main():
    lease_fd, job_fd = map(int, sys.argv[1:])
    with os.fdopen(job_fd, encoding="utf-8") as request:
        job = Job(**json.load(request))
    output = EventOutput(sys.stdout.fileno())
    # the launcher alone owns the lease writer, so even sigkill closes it
    try:
        event, exit_code = supervise(lease_fd, job, output)
        output.send(event)
        return exit_code
    except BrokenPipeError:
        return 130
    except Exception as exc:
        event, exit_code = finalize(job, error=str(exc))
        output.send(event)
        return exit_code
    finally:
        os.close(lease_fd)
        output.close()


if __name__ == "__main__":
    sys.exit(main())
