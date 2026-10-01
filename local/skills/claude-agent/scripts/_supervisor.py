import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

from _output import EventOutput


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


def supervise(lease_fd, job, output):
    reader, writer = os.pipe()
    process = None
    final = False
    reason = None
    deadline = None
    buffer = b""
    stdout_open = True
    last_activity = time.monotonic()
    input_wait = False
    timeout_error = f"No Claude activity for {job['idle_timeout']:g} seconds."
    with selectors.DefaultSelector() as selector:
        selector.register(lease_fd, selectors.EVENT_READ, "lease")
        try:
            process = subprocess.Popen(
                [job["python"], str(Path(__file__).with_name("_sdk.py")), str(reader)],
                pass_fds=(reader,),
                process_group=0,
                stdout=subprocess.PIPE,
            )
            os.close(reader)
            reader = None
            with os.fdopen(writer, "w", encoding="utf-8") as request:
                writer = None
                json.dump(job, request, ensure_ascii=False)
            selector.register(process.stdout, selectors.EVENT_READ, "output")

            def stop(status):
                nonlocal deadline, reason
                if deadline is None:
                    reason = status
                    deadline = time.monotonic() + 3
                    signal_group(process.pid, signal.SIGTERM)

            while True:
                output.check()
                for key, _ in selector.select(0.1):
                    if key.data == "lease":
                        if not os.read(lease_fd, 1):
                            selector.unregister(lease_fd)
                            stop("cancelled")
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
                            if kind == "_activity":
                                last_activity = time.monotonic()
                            elif kind == "_input_wait":
                                input_wait = True
                            elif kind == "_input_resume":
                                input_wait = False
                                last_activity = time.monotonic()
                            elif kind == "_shutdown":
                                stop(event["status"])
                            elif not final:
                                if kind == "result" and reason == "timed_out":
                                    event.update(
                                        status="timed_out", error=timeout_error
                                    )
                                output.send(event)
                                final = kind == "result"
                if (
                    not final
                    and not input_wait
                    and time.monotonic() - last_activity >= job["idle_timeout"]
                ):
                    stop("timed_out")
                exited = process.poll() is not None
                if exited and group_exists(process.pid):
                    stop(reason or "failed")
                if deadline is not None and time.monotonic() >= deadline:
                    signal_group(process.pid, signal.SIGKILL)
                if exited and not stdout_open:
                    if not group_exists(process.pid) or (
                        deadline is not None and time.monotonic() >= deadline
                    ):
                        break
            if not final:
                output.send(
                    {
                        **job["base"],
                        "type": "result",
                        "status": reason or "failed",
                        "error": timeout_error
                        if reason == "timed_out"
                        else "SDK worker ended before its final result",
                    }
                )
            if reason == "timed_out":
                return 124
            return (
                process.returncode
                if final
                else {"cancelled": 130, "timed_out": 124}.get(reason, 1)
            )
        finally:
            if process is not None:
                signal_group(process.pid, signal.SIGKILL)
                process.wait()
                process.stdout.close()
            for fd in (reader, writer):
                if fd is not None:
                    os.close(fd)


def main():
    lease_fd, job_fd = map(int, sys.argv[1:])
    with os.fdopen(job_fd, encoding="utf-8") as request:
        job = json.load(request)
    output = EventOutput(sys.stdout.fileno())
    # the launcher alone owns the lease writer, so even sigkill closes it
    try:
        return supervise(lease_fd, job, output)
    except BrokenPipeError:
        return 130
    except Exception as exc:
        output.send(
            {**job["base"], "type": "result", "status": "failed", "error": str(exc)}
        )
        return 1
    finally:
        os.close(lease_fd)
        output.close()


if __name__ == "__main__":
    sys.exit(main())
