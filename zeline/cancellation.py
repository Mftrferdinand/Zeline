"""Cancellable IO handoff. Abandoned daemon workers never access agent state.

Closing a local connection cannot guarantee cancellation of remote generation.
Arbitrary Python callbacks remain cooperative, not forcibly thread-killable.
"""
import queue
import threading


class Cancelled(Exception):
    pass


def close_async(response):
    def close():
        try:
            response.close()
        except Exception:
            pass
    threading.Thread(target=close, daemon=True, name='zeline-close').start()


def wait_io(call, cancelled, dispose=None):
    """Only the caller consumes the result; late IO may only dispose resources."""
    ready = queue.Queue(maxsize=1)
    lock = threading.Lock()
    abandoned = False

    def worker():
        try:
            result = (True, call())
        except BaseException as exc:
            result = (False, exc)
        with lock:
            if abandoned:
                if result[0] and dispose:
                    dispose(result[1])
            else:
                ready.put_nowait(result)

    threading.Thread(target=worker, daemon=True, name='zeline-io').start()
    while True:
        if cancelled():
            with lock:
                abandoned = True
                try:
                    ok, value = ready.get_nowait()
                except queue.Empty:
                    pass
                else:
                    if ok and dispose:
                        dispose(value)
            raise Cancelled()
        try:
            ok, value = ready.get(timeout=.02)
        except queue.Empty:
            continue
        if ok:
            return value
        raise value


def iter_io(iterator, cancelled):
    """One bounded reader per response, never one thread per streamed token."""
    ready = queue.Queue(maxsize=16)
    done = threading.Event()

    def publish(item):
        while not done.is_set():
            try:
                ready.put(item, timeout=0.02)
                return True
            except queue.Full:
                pass
        return False

    def reader():
        try:
            for item in iterator:
                if not publish((True, item)):
                    return
        except BaseException as exc:
            publish((False, exc))
        finally:
            publish((None, None))

    threading.Thread(target=reader, daemon=True, name='zeline-stream-reader').start()
    try:
        while True:
            if cancelled():
                raise Cancelled()
            try:
                ok, item = ready.get(timeout=0.02)
            except queue.Empty:
                continue
            if ok is None:
                return
            if not ok:
                raise item
            yield item
    finally:
        done.set()
