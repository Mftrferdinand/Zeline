"""Per-invocation private Unix IPC for execute_code (no persistent service)."""
import contextlib
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time

ALLOWED_TOOLS = frozenset({'read_file', 'write_file', 'search_files', 'patch_file', 'run_shell'})
_MAX_REQUEST = 2_000_000


@contextlib.contextmanager
def snippet_bridge(executor, code, seconds):
    """Yield bootstrap and captured controls; always stop dispatch and remove IPC."""
    context = executor.capture_context()
    registered = frozenset(d.name for d in executor._enabled_native_defs()) & ALLOWED_TOOLS
    stopped = threading.Event()
    deadline = time.monotonic() + seconds
    original_stop = context.get('_should_stop')

    def should_stop():
        return stopped.is_set() or time.monotonic() >= deadline or bool(original_stop and original_stop())

    nested = {**context, '_should_stop': should_stop,
              '_nested_tool_dispatch': True, '_nested_tool_allowed': registered,
              # A nested shell must not be promoted past its owning snippet.
              '_interrupt_reason': lambda: 'stop'}
    with tempfile.TemporaryDirectory(prefix='zeline-code-') as directory:
        os.chmod(directory, 0o700)
        path = str(Path(directory) / 'tools.sock')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(path)
            os.chmod(path, 0o600)
            listener.listen(4)
            listener.settimeout(.05)

            def serve():
                while not should_stop():
                    try:
                        connection, _ = listener.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    with connection:
                        connection.settimeout(.05)
                        payload = bytearray()
                        try:
                            while b'\n' not in payload and len(payload) <= _MAX_REQUEST and not should_stop():
                                try:
                                    part = connection.recv(65536)
                                except socket.timeout:
                                    continue
                                if not part:
                                    break
                                payload.extend(part)
                            if should_stop():
                                continue
                            request = json.loads(payload)
                            name, args = request.get('tool'), request.get('args')
                            if name not in ALLOWED_TOOLS:
                                output = 'ERROR: nested tool not permitted.'
                            elif name not in registered:
                                output = 'ERROR: nested tool is not registered or disabled for this profile.'
                            elif not isinstance(args, dict):
                                output = 'ERROR: tool arguments must be an object.'
                            else:
                                output = executor.run(name, args, _context=nested)
                            success = not str(output).startswith(('ERROR', 'CANCELLED'))
                            if name == 'run_shell' and str(output).startswith('exit='):
                                success = str(output).splitlines()[0] == 'exit=0'
                            response = {'success': success, 'tool': name, 'output': output}
                        except Exception as exc:
                            response = {'success': False, 'tool': None,
                                        'output': 'ERROR: bridge request failed (' + type(exc).__name__ + ').'}
                        try:
                            connection.sendall((json.dumps(response) + '\n').encode())
                        except OSError:
                            pass

            worker = threading.Thread(target=serve, name='zeline-code-tools', daemon=True)
            source = Path(__file__).with_name('code_tools_client.py').read_text(encoding='utf-8')
            bootstrap = (
                'import types as _zt_types, sys as _zt_sys\n'
                '_zt_module = _zt_types.ModuleType("zeline_tools")\n'
                f'_zt_module.__dict__["_bridge_path"] = {path!r}\n'
                f'exec(compile({source!r}, "<zeline_tools>", "exec"), _zt_module.__dict__)\n'
                '_zt_sys.modules["zeline_tools"] = _zt_module\n'
                f'exec(compile({code!r}, "<execute_code>", "exec"))\n'
            )
            worker.start()
            try:
                yield bootstrap
            finally:
                stopped.set()
                listener.close()
                worker.join()
