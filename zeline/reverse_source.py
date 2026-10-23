"""Bounded, inert syntax inventory; never imports or executes inspected source.

JavaScript requires tree-sitter==0.26.0 and tree-sitter-javascript==0.25.0.
Offsets are UTF-8 byte offsets (end exclusive); lines are one-based. Node and
finding caps limit inventory traversal. Parsing runs only in a serialized,
resource-limited temporary worker; byte/node caps are not memory limits.
Python syntax support follows the host interpreter;
JavaScript syntax support follows the installed grammar, not semantic validity.
"""
import ast
import base64
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

MAX_SOURCE_BYTES = 1_048_576
MAX_NODES = 50_000
MAX_FINDINGS = 1_000
MAX_LABEL_BYTES = 256
SOURCE_MEMORY_BYTES = 128 * 1024 * 1024
SOURCE_TIMEOUT_SECONDS = 5.0
_SOURCE_LOCK = threading.Lock()


def inspect_source(data: bytes, language: str, *, should_stop=None) -> dict:
    """Parse only in a bounded, serialized, temporary first-party worker.

    POSIX uses RLIMIT_AS; Windows uses a per-process memory job. Limit setup
    failure is an error, never an in-process fallback. Targets remain inert data.
    """
    if language not in ('python', 'javascript'):
        raise ValueError('Unsupported source language; use python or javascript')
    if not isinstance(data, bytes):
        raise ValueError('Source must be bytes')
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError(f'Source byte limit exceeded ({MAX_SOURCE_BYTES}); input not truncated')
    deadline = time.monotonic() + SOURCE_TIMEOUT_SECONDS

    def check():
        if should_stop is not None and should_stop():
            raise InterruptedError('source parsing cancelled')
        if time.monotonic() >= deadline:
            raise ValueError('source parser time limit exceeded')

    check()
    while not _SOURCE_LOCK.acquire(timeout=0.05):
        check()
    process = None
    try:
        check()
        payload = json.dumps({'data': base64.b64encode(data).decode('ascii'), 'language': language,
                              'limits': [MAX_SOURCE_BYTES, MAX_NODES, MAX_FINDINGS,
                                         MAX_LABEL_BYTES]}).encode('ascii')
        from .source_worker_limits import worker_environment
        process = subprocess.Popen(
            [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
             '--worker', str(SOURCE_MEMORY_BYTES)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            env=worker_environment(),
        )
        pending = payload
        while True:
            check()
            try:
                output, error = process.communicate(input=pending, timeout=min(0.05, max(0.001, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                pending = None
        check()
        if process.returncode or len(output) > 2 * 1024 * 1024:
            raise ValueError('source parser resource limit or worker failure')
        try:
            response = json.loads(output)
        except (ValueError, UnicodeError) as exc:
            raise ValueError('source parser resource limit or invalid worker response') from exc
        if not response.get('ok'):
            message = str(response.get('error', 'source worker failed'))[:400]
            if response.get('kind') == 'RuntimeError':
                raise RuntimeError(message)
            raise ValueError(message)
        result = response['result']
        result['limitations'].append('Parsed in a serialized, memory/time-limited temporary worker; not an OS sandbox.')
        return result
    finally:
        try:
            if process is not None:
                if process.poll() is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                process.communicate(timeout=1)
        finally:
            _SOURCE_LOCK.release()


def _inspect_source_local(data: bytes, language: str) -> dict:
    """Return imports/definitions or raise ValueError for invalid/oversize input.

    Oversize input is rejected, never parsed as a silently truncated prefix.
    Missing JS parser dependencies raise an actionable RuntimeError.
    """
    if language not in ('python', 'javascript'):
        raise ValueError('Unsupported source language; use python or javascript')
    if not isinstance(data, bytes):
        raise ValueError('Source must be bytes')
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError(f'Source byte limit exceeded ({MAX_SOURCE_BYTES}); input not truncated')
    try:
        text = data.decode('utf-8-sig' if language == 'python' else 'utf-8', errors='strict')
    except UnicodeDecodeError as exc:
        raise ValueError('Source must be valid UTF-8') from exc
    result = dict(language=language, parser='ast' if language == 'python' else 'tree-sitter-javascript',
                  imports=[], definitions=[], limitations=[
                      'Syntax inventory only; no semantic resolution, execution, or call graph.',
                      'Byte cap bounds parser input; node cap applies after parsing.'])
    if language == 'javascript':
        result['limitations'].append('CommonJS require and dynamic imports are not resolved; '
                                     'module strings are lexical, not escape-decoded.')
        _javascript(data, result)
    else:
        try:
            tree = ast.parse(text)
        except (SyntaxError, RecursionError, ValueError) as exc:
            raise ValueError('Invalid or excessively nested Python syntax') from exc
        _python(data, tree, result)
    return result


def _note(result, message):
    if message not in result['limitations']:
        result['limitations'].append(message)


def _label(value, result):
    if value is None:
        return None
    encoded = value.encode('utf-8')
    if len(encoded) > MAX_LABEL_BYTES:
        _note(result, f'Labels truncated at {MAX_LABEL_BYTES} UTF-8 bytes.')
        return encoded[:MAX_LABEL_BYTES].decode('utf-8', errors='ignore')
    return value


def _add(result, category, **item):
    if len(result['imports']) + len(result['definitions']) >= MAX_FINDINGS:
        _note(result, f'Findings truncated at {MAX_FINDINGS}.')
        return False
    result[category].append({key: _label(value, result) if isinstance(value, str) else value
                             for key, value in item.items()})
    return True


def _python(data, tree, result):
    starts = [3 if data.startswith(b'\xef\xbb\xbf') else 0]
    for index, byte in enumerate(data):
        if byte == 10 or (byte == 13 and data[index + 1:index + 2] != b'\n'):
            starts.append(index + 1)
    # Iterator stack avoids materializing a second full list of AST nodes.
    stack = [iter((tree,))]
    visited = 0
    while stack:
        node = next(stack[-1], None)
        if node is None:
            stack.pop()
            continue
        visited += 1
        if visited > MAX_NODES:
            _note(result, f'Node traversal truncated at {MAX_NODES} nodes.')
            break
        location = {}
        if hasattr(node, 'lineno'):
            location = dict(line=node.lineno, end_line=node.end_lineno,
                            start_byte=starts[node.lineno - 1] + node.col_offset,
                            end_byte=starts[node.end_lineno - 1] + node.end_col_offset)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not _add(result, 'definitions', name=node.name, kind=type(node).__name__, **location):
                break
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                module = ('.' * node.level + (node.module or '')) if isinstance(node, ast.ImportFrom) else alias.name
                if not _add(result, 'imports', name=alias.name, alias=alias.asname,
                            module=module, kind=type(node).__name__, **location):
                    return
        stack.append(ast.iter_child_nodes(node))


def _javascript(data, result):
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_javascript
    except ImportError as exc:
        raise RuntimeError('JavaScript analysis requires tree-sitter==0.26.0 and '
                           'tree-sitter-javascript==0.25.0') from exc
    root = Parser(Language(tree_sitter_javascript.language())).parse(data).root_node
    if root.has_error:
        raise ValueError('Malformed JavaScript syntax (tree-sitter error or missing token)')

    def text(node):
        if node is None:
            return None
        # Avoid copying huge source spans solely to build a label.
        if node.end_byte - node.start_byte > MAX_LABEL_BYTES:
            _note(result, f'Labels truncated at {MAX_LABEL_BYTES} UTF-8 bytes.')
        return data[node.start_byte:min(node.end_byte, node.start_byte + MAX_LABEL_BYTES)].decode(
            'utf-8', errors='ignore')

    # Avoid allocating native Point wrappers per node. Their construction is a
    # crashing CI path on CPython 3.10; byte spans already provide exact LF rows.
    # Packed offsets cost at most four bytes per input byte (source <=1 MiB),
    # rather than rescanning a long prefix for each of up to MAX_NODES nodes.
    from array import array
    from bisect import bisect_left
    newlines = array('I', (index for index, value in enumerate(data) if value == 10))
    cursor = root.walk()
    visited = 0
    while True:
        node = cursor.node
        visited += 1
        if visited > MAX_NODES:
            _note(result, f'Node traversal truncated at {MAX_NODES} nodes.')
            break
        start, end = node.start_byte, node.end_byte
        if not 0 <= start <= end <= len(data):
            raise ValueError('source parser returned an invalid byte span')
        location = dict(line=bisect_left(newlines, start) + 1,
                        end_line=bisect_left(newlines, end) + 1,
                        start_byte=start, end_byte=end)
        if node.type in ('import_statement', 'export_statement'):
            source = node.child_by_field_name('source')
            if source:
                # Read string contents directly, preserving lexical escapes.
                end = min(source.end_byte - 1, source.start_byte + 1 + MAX_LABEL_BYTES)
                if source.end_byte - source.start_byte - 2 > MAX_LABEL_BYTES:
                    _note(result, f'Labels truncated at {MAX_LABEL_BYTES} UTF-8 bytes.')
                module = data[source.start_byte + 1:end].decode('utf-8', errors='ignore')
                if not _add(result, 'imports', name=module, module=module, kind=node.type, **location):
                    break
            if node.type == 'export_statement':
                if not _add(result, 'definitions', name='export', kind='export', **location):
                    break
        name = node.child_by_field_name('name')
        if node.type in ('function_declaration', 'generator_function_declaration',
                         'class_declaration', 'method_definition'):
            if not _add(result, 'definitions', name=text(name), kind=node.type, **location):
                break
        elif node.type == 'variable_declarator':
            value = node.child_by_field_name('value')
            if value and value.type in ('arrow_function', 'function_expression',
                                        'generator_function', 'class'):
                if not _add(result, 'definitions', name=text(name), kind=value.type, **location):
                    break
        if cursor.goto_first_child():
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return


def _worker_main():
    import faulthandler
    import traceback  # Trusted stdlib startup; diagnostics must not import after OOM.
    faulthandler.enable()
    # Import only a trusted adjacent helper. -I excludes cwd/PYTHONPATH and the
    # script directory; never add the artifact workspace to the import path.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        '_zeline_source_limits', Path(__file__).with_name('source_worker_limits.py'))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    try:
        job = helper.set_memory_limit(min(int(sys.argv[2]), SOURCE_MEMORY_BYTES))
    except Exception:
        print(json.dumps({'ok': False, 'kind': 'RuntimeError',
                          'error': 'source parser memory isolation unavailable; no unsafe fallback'}))
        return
    try:
        request = sys.stdin.buffer.read(2 * MAX_SOURCE_BYTES + 4097)
        if len(request) > 2 * MAX_SOURCE_BYTES + 4096:
            raise ValueError('source worker request limit exceeded')
        request = json.loads(request)
        for key, value in zip(('MAX_SOURCE_BYTES', 'MAX_NODES', 'MAX_FINDINGS', 'MAX_LABEL_BYTES'), request['limits']):
            if type(value) is not int or value < 1:
                raise ValueError('invalid source worker limits')
            globals()[key] = min(globals()[key], value)
        data = base64.b64decode(request['data'], validate=True)
        result = _inspect_source_local(data, request['language'])
        response = {'ok': True, 'result': result}
    except MemoryError:
        traceback.print_exc(limit=6)
        response = {'ok': False, 'kind': 'MemoryError',
                    'error': 'source parser memory resource limit exceeded'}
    except Exception as exc:
        response = {'ok': False, 'kind': type(exc).__name__, 'error': str(exc)[:400]}
    print(json.dumps(response, ensure_ascii=True))
    # Keep the Windows job handle referenced through result flushing.
    _ = job


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        _worker_main()
    else:
        raise SystemExit('source analyzer is an internal static worker')
