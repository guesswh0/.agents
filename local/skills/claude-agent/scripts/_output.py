import json
import os
import tempfile
import threading


class EventOutput:
    def __init__(self, fd):
        self.fd = fd
        self.spool = tempfile.TemporaryFile()
        self.condition = threading.Condition()
        self.available = 0
        self.finished = False
        self.abandoned = False
        self.error = None
        self.thread = threading.Thread(target=self.forward, daemon=True)
        self.thread.start()

    def check(self):
        with self.condition:
            if self.error is not None:
                raise self.error

    def send(self, event):
        data = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        with self.condition:
            if self.error is not None:
                raise self.error
            self.spool.write(data)
            self.spool.flush()
            self.available += len(data)
            self.condition.notify()

    def forward(self):
        position = 0
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(
                        lambda: (
                            self.abandoned or self.finished or self.available > position
                        )
                    )
                    if self.abandoned:
                        return
                    size = min(65536, self.available - position)
                    if not size and self.finished:
                        return
                data = os.pread(self.spool.fileno(), size, position)
                if not data:
                    raise OSError("Could not read buffered output")
                while data:
                    # host writes must not hold the watchdog's lock
                    written = os.write(self.fd, data)
                    if not written:
                        raise BrokenPipeError("Host output closed")
                    position += written
                    data = data[written:]
                    with self.condition:
                        if self.abandoned:
                            return
        except OSError as exc:
            with self.condition:
                self.error = exc
        finally:
            self.spool.close()

    def close(self, timeout=3):
        with self.condition:
            self.finished = True
            self.condition.notify()
        self.thread.join(timeout)
        with self.condition:
            self.abandoned = True
            self.condition.notify()
