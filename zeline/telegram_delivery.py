"""Per-turn serialized complete-bubble transport, independent of agent callbacks."""
from collections import deque
import threading
import json
import os
import tempfile
import uuid
import time

_SUPPORTS_FCHMOD = hasattr(os, "fchmod")
_SUPPORTS_DIRECTORY_FSYNC = os.name != "nt"


class FinalReceipt:
    """Atomic per-part journal. IN_FLIGHT on disk means UNKNOWN after a crash.

    Never automatically replay a recovered journal: Telegram has no idempotency
    key, and neither a timeout nor a process crash proves non-delivery.
    """
    def __init__(self, directory, chat_id, parts):
        self.directory = directory / 'telegram-outbox'
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == 'nt':
            from .session_recovery import _windows_private
            info = self.directory.lstat()
            if (getattr(info, 'st_file_attributes', 0) & 0x400
                    or not _windows_private(self.directory, create=True)):
                raise PermissionError('Final receipt directory is not private')
        self.path = self.directory / (uuid.uuid4().hex + '.json')
        self.record = {'version': 1, 'chat_id': chat_id, 'parts': [
            {'text': part, 'state': 'PENDING'} for part in parts]}
        if not parts:
            self.record['error'] = 'EMPTY_FINAL'
        self.save()

    def save(self):
        fd, name = tempfile.mkstemp(prefix='.receipt-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                if os.name == 'nt':
                    from .session_recovery import _windows_private
                    if not _windows_private(name, create=True):
                        raise PermissionError('Final receipt file is not private')
                elif _SUPPORTS_FCHMOD:
                    os.fchmod(stream.fileno(), 0o600)
                else:
                    os.chmod(name, 0o600)
                json.dump(self.record, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            # Windows does not expose POSIX directory descriptors/fsync.
            # File fsync + atomic replacement still apply on every platform.
            if _SUPPORTS_DIRECTORY_FSYNC:
                directory = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def deliver(self, send, cancelled):
        if not self.record['parts']:
            return False
        self.deadline = time.monotonic() + 45
        for index, part in enumerate(self.record['parts']):
            if part['state'] == 'ACKED':
                continue
            if part['state'] not in {'PENDING', 'RETRYABLE'}:
                return False
            for attempt in range(3):
                if cancelled():
                    part['state'] = 'CANCELLED'
                    self.save()
                    return False
                if time.monotonic() >= self.deadline:
                    return False
                part['state'] = 'UNKNOWN'
                self.save()  # write-ahead: crash/timeout never proves non-delivery
                try:
                    payload = send(index, part['text'])
                except Exception:
                    payload = None
                if (isinstance(payload, dict) and payload.get('ok') is True
                        and isinstance(payload.get('result'), dict)
                        and payload['result'].get('message_id')):
                    part.update(state='ACKED', message_id=payload['result']['message_id'])
                    self.save()
                    break
                if not isinstance(payload, dict) or payload.get('ok') is not False:
                    self.save()
                    return False  # UNKNOWN: no automatic replay
                code = payload.get('error_code')
                if isinstance(code, int) and 500 <= code < 600:
                    # Server/upstream failure does not prove non-acceptance.
                    part.update(state='UNKNOWN', error_code=code)
                    self.save()
                    return False
                retryable = code == 429 or payload.get('_known_unsent') is True
                part.update(state='RETRYABLE' if retryable else 'FAILED', error_code=code)
                try:
                    delay = float((payload.get('parameters') or {}).get('retry_after', 0.5*(attempt+1)))
                except (TypeError, ValueError):
                    delay = 1
                part['retry_after'] = delay if 0 <= delay <= 86400 else None
                self.save()
                if not retryable or attempt == 2 or not 0 <= delay <= 30:
                    return False
                until = time.monotonic()+delay
                if until >= self.deadline:
                    return False
                while time.monotonic() < until:
                    if cancelled():
                        part['state'] = 'CANCELLED'
                        self.save()
                        return False
                    threading.Event().wait(min(.05, max(0, until-time.monotonic())))
        return True


class TurnTransport:
    def __init__(self, cancelled=lambda: False):
        self.cancelled = cancelled
        self.condition = threading.Condition()
        self.jobs = deque()
        self.closed = False
        self.worker = threading.Thread(target=self._run, daemon=True, name='zeline-telegram-delivery')
        self.worker.start()

    def submit(self, action, *, key=None, final=False):
        event = threading.Event()
        result = []
        with self.condition:
            if self.closed:
                event.set()
                return event, result
            if key is not None:
                # Only replace adjacent UI refreshes; never cross narration/tool boundaries.
                if self.jobs and self.jobs[-1][0] == key:
                    _, _, old_event, _ = self.jobs.pop()
                    old_event.set()
            if final:
                self.closed = True
                # Pending typing/refresh is expendable; activity/narration is ordered.
                self.jobs = deque(j for j in self.jobs if j[0] not in ('typing', 'tick', 'waiting'))
            self.jobs.append((key, action, event, result))
            self.condition.notify()
        return event, result

    def close(self):
        with self.condition:
            self.closed = True
            while self.jobs:
                _, _, event, _ = self.jobs.popleft()
                event.set()
            self.condition.notify_all()

    def _run(self):
        while True:
            with self.condition:
                while not self.jobs:
                    if self.closed:
                        return
                    self.condition.wait()
                _, action, event, result = self.jobs.popleft()
            try:
                if not self.cancelled():
                    result.append(action())
            except Exception:
                result.append(False)
            finally:
                event.set()
