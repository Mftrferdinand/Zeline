"""Bounded byte capture with a single, explicitly owned pipe reader."""
import os
import tempfile
import threading
import time

OUTPUT_BUDGET = 50_000
SPOOL_BUDGET = 16 * 1024 * 1024
CHUNK_SIZE = 8192
PARENT_EXIT_GRACE = 0.25


class ProcessOutput:
    """Drain without a queue; disk spool preserves bytes for later promotion."""

    def __init__(self, process):
        self.process = process
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0
        self.spool_bytes = 0
        self.spool_budget = SPOOL_BUDGET
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.done = threading.Event()
        self.spool = None
        self.sink = None
        self.error = None
        try:
            self.spool = tempfile.TemporaryFile(mode='w+b')
            self.fd = process.stdout.fileno()
            if os.name != 'nt':
                os.set_blocking(self.fd, False)
            self.thread = threading.Thread(target=self._drain, daemon=True,
                                           name=f'zeline-output-{process.pid}')
            self.thread.start()
        except BaseException:
            # The caller still owns process termination, but no partially
            # constructed collector may retain its file descriptors.
            for handle in (self.spool, process.stdout):
                if handle is not None:
                    try:
                        handle.close()
                    except Exception:
                        pass
            raise

    def _available(self):
        if os.name != 'nt':
            return True
        # Anonymous Windows pipes cannot use select(). Peek before os.read so
        # cancellation never depends on a descendant closing its write handle.
        import ctypes
        import msvcrt
        count = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.PeekNamedPipe(
            ctypes.c_void_p(msvcrt.get_osfhandle(self.fd)), None, 0,
            None, ctypes.byref(count), None)
        if not ok:
            # A closed writer is ordinary EOF, not a capture failure.
            error = ctypes.windll.kernel32.GetLastError()
            if error in (109, 232):  # ERROR_BROKEN_PIPE / ERROR_NO_DATA
                return None
            raise OSError(error, 'cannot inspect process output pipe')
        return count.value > 0

    def _append(self, data):
        with self.lock:
            if self.sink is not None:
                self.sink.write(data)
                return
            remaining = max(0, self.spool_budget - self.spool_bytes)
            retained = data[:remaining]
            if retained:
                self.spool.write(retained)
                self.spool_bytes += len(retained)
            self.total += len(data)
            take = min(OUTPUT_BUDGET * 2 // 5 - len(self.head), len(data))
            self.head.extend(data[:take])
            self.tail.extend(data[take:])
            tail_limit = OUTPUT_BUDGET - OUTPUT_BUDGET * 2 // 5
            if len(self.tail) > tail_limit:
                del self.tail[:-tail_limit]

    def _drain(self):
        exited_at = None
        try:
            while not self.stop.is_set():
                if self.process.poll() is not None:
                    if exited_at is None:
                        exited_at = time.monotonic()
                    if time.monotonic() - exited_at >= PARENT_EXIT_GRACE:
                        break
                try:
                    available = self._available()
                    if available is None:
                        break
                    if not available:
                        self.stop.wait(.01)
                        continue
                    data = os.read(self.fd, CHUNK_SIZE)
                except BlockingIOError:
                    self.stop.wait(.01)
                    continue
                if not data:
                    break
                self._append(data)
        except Exception as exc:
            self.error = exc
        finally:
            # A failed flush/close must neither skip the other handle nor leave
            # observers waiting forever for completion.
            try:
                self.process.stdout.close()
            except Exception as exc:
                if self.error is None:
                    self.error = exc
            with self.lock:
                try:
                    if self.sink is not None:
                        self.sink.close()
                except Exception as exc:
                    if self.error is None:
                        self.error = exc
                finally:
                    # Publication and handoff must use the same lock.
                    self.done.set()

    def render(self):
        with self.lock:
            if self.error is not None:
                raise OSError('process output capture failed') from self.error
            if self.total <= OUTPUT_BUDGET:
                text = (bytes(self.head) + bytes(self.tail)).decode('utf-8', errors='replace')
                if len(text.encode('utf-8')) <= OUTPUT_BUDGET:
                    return text
            head = bytes(self.head).decode('utf-8', errors='replace').encode('utf-8')
            tail = bytes(self.tail).decode('utf-8', errors='replace').encode('utf-8')
            # Trim only at valid UTF-8 boundaries after replacement expansion.
            if self.total <= OUTPUT_BUDGET and len(head) + len(tail) <= OUTPUT_BUDGET:
                data = head + tail
                return data.decode('utf-8')
            else:
                notice = f'\n[OUTPUT TRUNCATED: {self.total} total bytes]\n'.encode()
                budget = max(0, OUTPUT_BUDGET - len(notice))
                left = budget * 2 // 5
                right = budget - left
                start = head[:left].decode('utf-8', errors='ignore')
                end = tail[-right:].decode('utf-8', errors='ignore') if right else ''
                return start + notice.decode('ascii') + end

    def finish(self):
        self.thread.join(PARENT_EXIT_GRACE + .15)
        self.stop.set()
        self.thread.join(.15)
        with self.lock:
            self.spool.close()

    def promote(self, handle):
        # The same lock serializes the spool copy and all subsequent writes:
        # there is no reader handoff and no overlap at the prefix boundary.
        with self.lock:
            if self.error is not None:
                raise OSError('process output capture failed') from self.error
            self.spool.seek(0)
            while True:
                data = self.spool.read(CHUNK_SIZE)
                if not data:
                    break
                handle.write(data)
            if self.total > self.spool_bytes:
                # Preserve a recent bounded tail as well as the disk prefix.
                # Above the budget, omission is explicit rather than silently
                # claiming that the complete foreground log was preserved.
                recent = (bytes(self.head) + bytes(self.tail)
                          if self.total <= OUTPUT_BUDGET else bytes(self.tail))
                recent_start = self.total - len(recent)
                replay_start = max(self.spool_bytes, recent_start)
                omitted = replay_start - self.spool_bytes
                handle.write(f'\n[OUTPUT TRUNCATED BEFORE HANDOFF: {omitted} bytes omitted]\n'.encode())
                handle.write(recent[replay_start - recent_start:])
            self.spool.close()
            self.sink = handle
            if self.done.is_set():
                handle.close()
