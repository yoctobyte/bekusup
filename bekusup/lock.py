import fcntl
import os
import sys

class RunLock:
    def __init__(self, lock_file="/tmp/bekusup.lock"):
        self.lock_file = lock_file
        self.lock_fd = None

    def acquire(self):
        self.lock_fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            # Non-blocking exclusive lock
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            
            # Write PID to the file
            os.ftruncate(self.lock_fd, 0)
            os.write(self.lock_fd, f"{os.getpid()}\n".encode('utf-8'))
        except BlockingIOError:
            print(f"Error: Another bekusup instance is already running (lock: {self.lock_file}).", file=sys.stderr)
            sys.exit(1)

    def release(self):
        if self.lock_fd is not None:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
            try:
                os.remove(self.lock_file)
            except OSError:
                pass
            self.lock_fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
