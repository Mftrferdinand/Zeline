"""Source bootstrapped as zeline_tools only inside an execute_code child.

All responses use {success, tool, output}; output preserves the native receipt.
This is an IPC client, not an executor or an OS sandbox.
"""
import json as _json
import socket as _socket

__all__ = ['read_file', 'write_file', 'search_files', 'patch', 'terminal']

_bridge_path: str | tuple  # Injected by the per-invocation bootstrap.


def _call(name, args):
    address = globals()['_bridge_path']
    family = getattr(_socket, 'AF_UNIX', _socket.AF_INET) if isinstance(address, str) else _socket.AF_INET
    with _socket.socket(family, _socket.SOCK_STREAM) as connection:
        connection.connect(address)
        with connection.makefile('rwb') as stream:
            stream.write((_json.dumps({'token': globals()['_bridge_token'], 'tool': name, 'args': args}) + '\n').encode())
            stream.flush()
            return _json.loads(stream.readline())


def read_file(path, offset=1, limit=500, byte_offset=None):
    args = dict(path=path, offset=offset, limit=limit)
    if byte_offset is not None:
        args['byte_offset'] = byte_offset
    return _call('read_file', args)


def write_file(path, content):
    return _call('write_file', dict(path=path, content=content))


def search_files(pattern='', target='content', path='.', limit=50, offset=0,
                 query=None, file_glob='*', regex=False):
    # The helper's pattern is a query; the native pattern is a filename glob.
    if path not in ('', '.'):
        file_glob = path.rstrip('/') + '/' + file_glob
    return _call('search_files', dict(query=pattern if query is None else query,
                                     pattern=file_glob, target=target, limit=limit,
                                     offset=offset, regex=regex))


def patch(path, old_string, new_string):
    return _call('patch_file', dict(path=path, old_text=old_string, new_text=new_string))


def terminal(command, timeout=None, background=False):
    return _call('run_shell', dict(command=command, timeout=timeout, background=background))
