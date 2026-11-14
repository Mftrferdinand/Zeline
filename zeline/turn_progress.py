"""Turn-local circuit breaker for repeated timed-out filesystem discovery."""
from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath
from threading import Lock


class SlowSearchGuard:
    """Helper successes do not erase failed discovery; productive work is exempt."""

    def __init__(self, limit: int = 3):
        self.limit = max(1, int(limit))
        self.failures: dict[str, int] = {}
        self.blocked_attempted = False
        self._lock = Lock()

    @staticmethod
    def family(name, args):
        if name != 'run_shell' or args.get('background'):
            return None
        try:
            lexer = shlex.shlex(str(args.get('command', '')), posix=True, punctuation_chars=';&|')
            lexer.whitespace_split = True
            parts = list(lexer)
        except ValueError:
            return None
        groups, group = [], []
        for part in parts + [';']:
            if part in {';', '&&', '||', '|', '&'}:
                if group:
                    groups.append(group)
                group = []
            else:
                group.append(part)
        for group in groups:
            index = 0
            if PurePosixPath(group[0]).name == 'timeout':
                index = 1
                while index < len(group) and group[index].startswith('-'):
                    flag = group[index]
                    index += 2 if flag in {'-s', '-k', '--signal', '--kill-after'} else 1
                if index >= len(group) or not re.fullmatch(r'\d+(?:\.\d+)?[smhd]?', group[index]):
                    continue
                index += 1
            if index < len(group):
                command = PurePosixPath(group[index]).name
                if command in {'grep', 'rg', 'find'}:
                    return command
        return None

    def run(self, name, args, invoke):
        family = self.family(name, args)
        if family is None:
            return invoke()
        with self._lock:
            if self.failures.get(family, 0) >= self.limit:
                self.blocked_attempted = True
                return (f'ERROR: repeated slow {family} search blocked; not executed. '
                        'Previous searches timed out. Use a narrower source or a different method; '
                        'completed tool results remain available.')
        result = invoke()
        text = str(result)
        timed_out = bool(re.match(r'^exit=(?:124|137)(?:\n|$)', text)) or (
            text.startswith('ERROR') and 'timed out' in text.lower())
        with self._lock:
            if timed_out:
                self.failures[family] = self.failures.get(family, 0) + 1
                if self.failures[family] >= self.limit:
                    return (text + '\nHOST: this search method has timed out repeatedly. '
                            'Do not repeat it. Switch source/scope or report the blocker honestly.')
            elif re.match(r'^exit=0(?:\n|$)', text):
                self.failures.pop(family, None)
        return result
