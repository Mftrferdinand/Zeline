"""Private, identity-scoped task checkpoints. Stored data never executes tools."""
from __future__ import annotations

import hashlib
import html
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class TaskContextStore:
    MAX_TASKS = 128
    MAX_ANCHORS = 2048
    MAX_OWNERS = 256
    MAX_EVENTS = 24
    STATUSES = {'active', 'paused', 'blocked', 'completed', 'cancelled'}

    def __init__(self, path=None):
        from zeline import config
        self.path = Path(path) if path is not None else config.DATA_DIR / 'task_context.sqlite3'
        if any(part.is_symlink() for part in (self.path, *self.path.parents)):
            raise ValueError('Task checkpoint path may not contain symlinks')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS task_context(owner TEXT, id TEXT, record TEXT NOT NULL, PRIMARY KEY(owner,id))')
            db.execute('CREATE TABLE IF NOT EXISTS task_focus(owner TEXT PRIMARY KEY, id TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS task_anchors(owner TEXT, message_id INTEGER, task_id TEXT, sequence INTEGER PRIMARY KEY AUTOINCREMENT, UNIQUE(owner,message_id))')

    @staticmethod
    def _owner(identity):
        if not isinstance(identity, str) or not identity or len(identity) > 512:
            raise ValueError('Invalid task owner')
        return hashlib.sha256(identity.encode()).hexdigest()

    @contextmanager
    def _connection(self):
        for path in (self.path, *self.path.parents):
            if path.is_symlink():
                raise ValueError('Task store must not use symlinks')
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        db = sqlite3.connect(str(self.path), timeout=5)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db, owner, task_id):
        row = db.execute('SELECT record FROM task_context WHERE owner=? AND id=?', (owner, task_id)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _save(db, owner, record):
        db.execute('INSERT OR REPLACE INTO task_context VALUES(?,?,?)',
                   (owner, record['id'], json.dumps(record, ensure_ascii=False)))

    @staticmethod
    def _unfinished_count(db, owner):
        # Stream existing records: terminal history stays addressable without
        # requiring a schema migration or SQLite's optional JSON1 extension.
        return sum(json.loads(row[0])['status'] not in {'completed', 'cancelled'}
                   for row in db.execute('SELECT record FROM task_context WHERE owner=?', (owner,)))

    def get(self, identity, task_id):
        with self._connection() as db:
            return self._get(db, self._owner(identity), task_id)

    def focus(self, identity):
        owner = self._owner(identity)
        with self._connection() as db:
            row = db.execute('SELECT id FROM task_focus WHERE owner=?', (owner,)).fetchone()
            return self._get(db, owner, row[0]) if row else None

    @staticmethod
    def _message_id(value):
        if type(value) is not int or value <= 0 or value > 2**63 - 1:
            raise ValueError('Invalid task message ID')
        return value

    def _bind(self, db, owner, task_id, message_id):
        message_id = self._message_id(message_id)
        db.execute('INSERT INTO task_anchors(owner,message_id,task_id) VALUES(?,?,?) '
                   'ON CONFLICT(owner,message_id) DO UPDATE SET task_id=excluded.task_id',
                   (owner, message_id, task_id))
        db.execute('DELETE FROM task_anchors WHERE owner=? AND sequence NOT IN '
                   '(SELECT sequence FROM task_anchors WHERE owner=? ORDER BY sequence DESC LIMIT ?)',
                   (owner, owner, self.MAX_ANCHORS))

    def bind_message(self, identity, task_id, message_id):
        owner = self._owner(identity)
        with self._connection() as db:
            if self._get(db, owner, task_id) is None:
                raise ValueError('Task not found for this owner')
            self._bind(db, owner, task_id, message_id)

    def begin(self, identity, text, *, message_id=None, reply_to_message_id=None):
        import re
        owner = self._owner(identity)
        now = time.time()
        normalized = text.casefold().strip(' .!?\n\t')
        bare = bool(re.fullmatch(
            r'(?:lanjut(?:in|kan)?|gas(?:kan)?|terus(?:in|kan)?|continue|resume|proceed|next)'
            r'(?:\s+(?:aja|dong|please|pls|tadi|lagi|terakhir|it|on))*', normalized))
        with self._connection() as db:
            row = db.execute('SELECT id FROM task_focus WHERE owner=?', (owner,)).fetchone()
            previous = self._get(db, owner, row[0]) if row else None
            selected = None
            named = re.fullmatch(r'(?:lanjut(?:in|kan)?|resume|continue)\s+(.+)', normalized) if not bare else None
            if named:
                terms = re.findall(r'\w+', named.group(1))
                rows = db.execute('SELECT record FROM task_context WHERE owner=?', (owner,)).fetchall()
                matches = [json.loads(row[0]) for row in rows]
                matches = [item for item in matches if terms and all(
                    term in (item['title'] + ' ' + item['request'] + ' ' + item['id']).casefold()
                    for term in terms)]
                if len(matches) != 1:
                    return {'task': None, 'reason': 'ambiguous' if matches else 'no_task',
                            'candidates': [{'id': item['id'], 'title': item['title']} for item in matches[:8]]}
                selected = matches[0]
            elif reply_to_message_id is not None:
                reply_id = self._message_id(reply_to_message_id)
                anchor = db.execute('SELECT task_id FROM task_anchors WHERE owner=? AND message_id=?',
                                    (owner, reply_id)).fetchone()
                selected = self._get(db, owner, anchor[0]) if anchor else None
                if selected is None:
                    return {'task': None, 'reason': 'missing_anchor'}
            elif bare:
                if previous is None or previous['status'] in {'completed', 'cancelled'}:
                    return {'task': None, 'reason': 'no_task'}
                selected = previous
            if previous and previous['status'] == 'active' and (selected is None or selected['id'] != previous['id']):
                previous.update(status='paused', updated_at=now)
                self._save(db, owner, previous)
            if selected is None:
                if self._unfinished_count(db, owner) >= self.MAX_TASKS:
                    raise ValueError('Task capacity reached; complete or dismiss old tasks before starting new work')
                exists = db.execute('SELECT 1 FROM task_context WHERE owner=? LIMIT 1', (owner,)).fetchone()
                if not exists and db.execute('SELECT count(DISTINCT owner) FROM task_context').fetchone()[0] >= self.MAX_OWNERS:
                    raise ValueError('Task owner capacity reached')
                selected = dict(id=uuid.uuid4().hex, title=self._text(text.splitlines()[0], 160),
                    request=self._text(text, 16000), status='active', summary='', blocker='',
                    next_step='', artifacts=[], tool_events=[], created_at=now, updated_at=now)
                reason = 'new'
            else:
                if selected['status'] in {'completed', 'cancelled'} and self._unfinished_count(db, owner) >= self.MAX_TASKS:
                    raise ValueError('Task capacity reached; complete or dismiss old tasks before resuming work')
                selected.update(status='active', updated_at=now)
                reason = 'resume'
            self._save(db, owner, selected)
            db.execute('INSERT OR REPLACE INTO task_focus VALUES(?,?)', (owner, selected['id']))
            if message_id is not None:
                self._bind(db, owner, selected['id'], message_id)
            return {'task': selected, 'reason': reason}

    def clear(self, identity):
        owner = self._owner(identity)
        with self._connection() as db:
            row = db.execute('SELECT id FROM task_focus WHERE owner=?', (owner,)).fetchone()
            record = self._get(db, owner, row[0]) if row else None
            if record and record['status'] == 'active':
                record.update(status='paused', updated_at=time.time())
                self._save(db, owner, record)
            db.execute('DELETE FROM task_focus WHERE owner=?', (owner,))

    def pause(self, identity, task_id, reason='stopped'):
        owner = self._owner(identity)
        with self._connection() as db:
            record = self._get(db, owner, task_id)
            if record is None:
                return None
            if record['status'] == 'active':
                record.update(status='paused', updated_at=time.time())
                self._save(db, owner, record)
            return record

    def capture(self, identity, task_id, messages, seen, turn_id):
        from zeline.outcomes import tool_outcome
        start = next((i for i in range(len(messages)-1, -1, -1)
                      if messages[i].get('role') == 'user'), len(messages))
        tail = messages[start:]
        calls = [call for item in tail for call in (item.get('tool_calls') or [])][-48:]
        results = {item.get('tool_call_id'): item.get('content', '') for item in tail
                   if item.get('role') == 'tool'}
        for call in calls:
            call_id = call.get('id')
            if not call_id:
                continue
            function = call.get('function') or {}
            if function.get('name') == 'task_checkpoint':
                continue
            try:
                args = json.loads(function.get('arguments') or '{}')
            except (TypeError, ValueError):
                args = {}
            if not isinstance(args, dict):
                args = {}
            path = next((args[key] for key in ('path', 'file_path', 'filename', 'output_path', 'workdir', 'cwd')
                         if isinstance(args.get(key), str)), '')
            result = results.get(call_id)
            status = tool_outcome(result) if call_id in results else 'unknown'
            preview = '' if result is None else str(result)
            if len(preview) > 1000:
                preview = preview[:750] + '\n[preview; inspect archive for full result]\n' + preview[-180:]
            event = dict(id=str(turn_id) + ':' + str(call_id), tool=str(function.get('name', '')),
                         status=status, result=preview, path=path)
            signature = json.dumps(event, ensure_ascii=False)
            if seen.get(event['id']) == signature:
                continue
            self.checkpoint(identity, task_id, tool_event=event)
            seen[event['id']] = signature
            while len(seen) > 128:
                seen.pop(next(iter(seen)))

    @staticmethod
    def _text(value, limit):
        if not isinstance(value, str):
            raise ValueError('Checkpoint text must be a string')
        from zeline.lessons import _redact_text
        from zeline import config
        value = value[:20000]
        key = getattr(config, 'API_KEY', '')
        if key and len(key) >= 8:
            value = value.replace(key, '[REDACTED]')
        return _redact_text(value)[:limit]

    def checkpoint(self, identity, task_id, *, summary=None, blocker=None,
                   next_step=None, artifacts=None, status=None, tool_event=None):
        owner = self._owner(identity)
        if status is not None and status not in self.STATUSES:
            raise ValueError('Invalid task status')
        fields = {}
        for name, value, size in [('summary', summary, 3000), ('blocker', blocker, 1000),
                                  ('next_step', next_step, 1500)]:
            if value is not None:
                fields[name] = self._text(value, size)
        if artifacts is not None:
            if not isinstance(artifacts, list):
                raise ValueError('Artifacts must be a list')
            fields['artifacts'] = [self._text(value, 300) for value in artifacts[:16]]
        event = None
        if tool_event is not None:
            if not isinstance(tool_event, dict):
                raise ValueError('Invalid tool evidence')
            event = {key: self._text(value, 1000 if key == 'result' else 300)
                     for key, value in tool_event.items()
                     if key in {'id', 'tool', 'status', 'result', 'path'}}
        with self._connection() as db:
            record = self._get(db, owner, task_id)
            if record is None:
                raise ValueError('Task not found for this owner')
            if (record['status'] in {'completed', 'cancelled'}
                    and status is not None and status not in {'completed', 'cancelled'}
                    and self._unfinished_count(db, owner) >= self.MAX_TASKS):
                raise ValueError('Task capacity reached; complete or dismiss old tasks before resuming work')
            record.update(fields)
            if status is not None:
                record['status'] = status
            if event:
                events = record['tool_events']
                if event.get('id'):
                    events = [item for item in events if item.get('id') != event['id']]
                record['tool_events'] = [*events, event][-self.MAX_EVENTS:]
                if event.get('path') and event['path'] not in record['artifacts']:
                    record['artifacts'] = [*record['artifacts'], event['path']][-16:]
            record['updated_at'] = time.time()
            self._save(db, owner, record)
            return record

    @staticmethod
    def render(record):
        # Put the frontier before any long request or historical tool output.
        view = {key: record.get(key) for key in
                ('id', 'title', 'status', 'next_step', 'blocker', 'summary', 'artifacts')}
        view['tool_events'] = record.get('tool_events', [])[-8:]
        view['request_excerpt'] = record.get('request', '')[:1500]
        view['updated_at'] = record.get('updated_at')
        rendered = html.escape(json.dumps(view, ensure_ascii=False), quote=False)
        return rendered if len(rendered) <= 12000 else rendered[:11960] + '\n[checkpoint preview truncated]'
