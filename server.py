#!/usr/bin/env python3
"""Local Foundry-inspired workbench. Python standard library only."""
import argparse
import csv
import io
import json
import re
import mimetypes
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('WORKBENCH_DB') or ROOT / 'data' / 'workbench.db')
# Extra public hostnames (host[:port]) the server accepts, for a deployment behind a domain. Loopback is always accepted.
ALLOWED_HOSTS = {h.strip() for h in os.environ.get('ALLOWED_HOSTS', '').split(',') if h.strip()}
MAX_BYTES = 5 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex[:16]


def connect():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS types (name TEXT PRIMARY KEY, description TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, name TEXT NOT NULL,
          type TEXT NOT NULL REFERENCES types(name), config TEXT NOT NULL, rows TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS objects (type TEXT REFERENCES types(name), id TEXT,
          data TEXT NOT NULL, source TEXT REFERENCES sources(id), updated TEXT NOT NULL, PRIMARY KEY(type,id));
        CREATE TABLE IF NOT EXISTS links (id TEXT PRIMARY KEY, label TEXT NOT NULL,
          from_type TEXT, from_id TEXT, to_type TEXT, to_id TEXT,
          FOREIGN KEY(from_type,from_id) REFERENCES objects(type,id),
          FOREIGN KEY(to_type,to_id) REFERENCES objects(type,id),
          UNIQUE(label,from_type,from_id,to_type,to_id));
        CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, name TEXT NOT NULL,
          type TEXT NOT NULL REFERENCES types(name), kind TEXT NOT NULL, config TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT,
          time TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
        ''')


def event(db, kind, detail):
    db.execute('INSERT INTO events(time,kind,detail) VALUES(?,?,?)', (now(), kind, json.dumps(detail)))


def required(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{key} must be a non-empty string')
    if len(value) > 250:
        raise ValueError(f'{key} is too long')
    return value.strip()


def request_json(url, method='GET', payload=None):
    if urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('Use an http:// or https:// endpoint')
    body = json.dumps(payload).encode() if payload is not None else None
    req = Request(url, data=body, method=method, headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    with urlopen(req, timeout=10) as response:
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Response exceeds 5 MB')
        return json.loads(raw) if raw else None


EVIDENCE_TYPE = 'EvidenceRecord'
PARAM_TYPES = ('string', 'text', 'number', 'enum', 'object', 'uri', 'date')
CRITERIA_KINDS = ('max_from_property', 'required_if', 'transition', 'requires_latest_evidence', 'target_property')
URI_RE = re.compile(r'^[a-z][a-z0-9+.-]*:\S+$', re.I)
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def validate_record_config(config):
    """Check an action type definition: typed parameters, submission criteria, evidence kind."""
    required(config, 'evidence_kind')
    params = config.get('parameters')
    if not isinstance(params, list) or not params:
        raise ValueError('Record actions need a non-empty parameters list')
    names = set()
    for param in params:
        if not isinstance(param, dict):
            raise ValueError('Each parameter must be an object')
        name = required(param, 'name')
        if name in names or name in ('kind', 'action', 'target_type', 'target_id', 'recorded_at'):
            raise ValueError(f'Parameter name {name} is duplicated or reserved')
        names.add(name)
        if param.get('type', 'string') not in PARAM_TYPES:
            raise ValueError(f'Parameter {name}: type must be one of {", ".join(PARAM_TYPES)}')
        if param.get('type') == 'enum' and not (isinstance(param.get('values'), list) and param['values']):
            raise ValueError(f'Parameter {name}: enum parameters need a values list')
        if param.get('type') == 'object':
            required(param, 'object_type')
    for rule in config.get('criteria', []):
        if not isinstance(rule, dict) or rule.get('kind') not in CRITERIA_KINDS:
            raise ValueError(f'Each criterion needs a kind from {", ".join(CRITERIA_KINDS)}')
        if rule['kind'] in ('max_from_property', 'required_if', 'transition') and rule.get('param') not in names:
            raise ValueError(f'Criterion {rule["kind"]} refers to an unknown parameter')


def coerce_params(db, config, raw):
    """Apply parameter types to submitted values; return clean values and referenced objects."""
    if not isinstance(raw, dict):
        raise ValueError('Action parameters must be a JSON object')
    values, refs = {}, []
    for param in config['parameters']:
        name, kind = param['name'], param.get('type', 'string')
        title = param.get('label', name)
        value = raw.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            if param.get('required'):
                raise ValueError(f'{title} is required')
            continue
        if kind == 'number':
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError(f'{title} must be a number')
        else:
            value = str(value).strip()
            if len(value) > 4000:
                raise ValueError(f'{title} is too long')
            if kind == 'enum' and value not in param['values']:
                raise ValueError(f'{title} must be one of {", ".join(param["values"])}')
            if kind == 'uri' and not URI_RE.match(value):
                raise ValueError(f'{title} must be a URI such as https://... or file:///...')
            if kind == 'date' and not DATE_RE.match(value):
                raise ValueError(f'{title} must be a date like 2026-10-03')
            if kind == 'object':
                if not db.execute('SELECT 1 FROM objects WHERE type=? AND id=?', (param['object_type'], value)).fetchone():
                    raise ValueError(f'{title}: no {param["object_type"]} with ID {value}')
                refs.append((param.get('link') or f'ref_{name}', param['object_type'], value))
        values[name] = value
    return values, refs


def evidence_for(db, target_type, target_id, kind=None):
    """Evidence records linked to an object, newest first. Evidence is the writeback layer."""
    rows = db.execute('SELECT o.id, o.data FROM links l JOIN objects o ON o.type=l.from_type AND o.id=l.from_id '
                      "WHERE l.label='evidences' AND l.from_type=? AND l.to_type=? AND l.to_id=? ORDER BY o.id DESC",
                      (EVIDENCE_TYPE, target_type, target_id)).fetchall()
    records = [{'id': r['id'], **json.loads(r['data'])} for r in rows]
    return [r for r in records if kind is None or r.get('kind') == kind]


def check_criteria(db, config, target, props, values):
    """Submission criteria: every rule must hold or the action is refused with no side effect."""
    for rule in config.get('criteria', []):
        kind, message = rule['kind'], rule.get('message')
        if kind == 'target_property':
            if props.get(rule['property']) != rule.get('equals'):
                raise ValueError(message or f'This action applies only when {rule["property"]} is {rule.get("equals")}')
        elif kind == 'max_from_property':
            value, limit = values.get(rule['param']), props.get(rule['property'])
            try:
                limit = float(limit)
            except (TypeError, ValueError):
                continue
            if value is not None and value > limit:
                raise ValueError(message or f'{rule["param"]} {value:g} exceeds {rule["property"]} {limit:g}')
        elif kind == 'required_if':
            for key, expected in rule.get('when', {}).items():
                allowed = expected if isinstance(expected, list) else [expected]
                if values.get(key) in allowed and values.get(rule['param']) in (None, ''):
                    raise ValueError(message or f'{rule["param"]} is required when {key} is {values.get(key)}')
        elif kind == 'transition':
            latest = evidence_for(db, target['type'], target['id'], config['evidence_kind'])
            current = latest[0].get(rule['param'], '') if latest else ''
            allowed = rule.get('allowed', {}).get(current, [])
            if values.get(rule['param']) not in allowed:
                raise ValueError(message or f'Cannot move from {current or "the initial state"} to {values.get(rule["param"])}; allowed: {", ".join(allowed) or "none"}')
        elif kind == 'requires_latest_evidence':
            other_id = props.get(rule['target_from_property'])
            if not other_id:
                continue
            latest = evidence_for(db, rule.get('target_type', target['type']), str(other_id), rule['evidence_kind'])
            if not latest or latest[0].get(rule['param']) != rule.get('equals'):
                raise ValueError(message or f'{other_id} must first have {rule["evidence_kind"]} evidence with {rule["param"]} = {rule.get("equals")}')


def apply_record(db, action, obj, raw_params):
    """Apply an action the ontology way: validate typed parameters, check submission criteria,
    then atomically create one immutable EvidenceRecord plus its links. The imported target
    object is never mutated, so a source refresh can never erase what was recorded."""
    config, props = json.loads(action['config']), json.loads(obj['data'])
    values, refs = coerce_params(db, config, raw_params or {})
    check_criteria(db, config, obj, props, values)
    db.execute('INSERT OR IGNORE INTO types VALUES(?,?)', (EVIDENCE_TYPE, 'Immutable records written by actions. The latest record of each kind is the current state of its target.'))
    count = db.execute('SELECT COUNT(*) FROM objects WHERE type=?', (EVIDENCE_TYPE,)).fetchone()[0]
    evidence_id = f'EV-{count + 1:05d}'
    while db.execute('SELECT 1 FROM objects WHERE type=? AND id=?', (EVIDENCE_TYPE, evidence_id)).fetchone():
        count += 1
        evidence_id = f'EV-{count + 1:05d}'
    record = {'kind': config['evidence_kind'], 'action': action['name'], 'target_type': obj['type'], 'target_id': obj['id'],
              'recorded_at': now(), **values}
    db.execute('INSERT INTO objects VALUES(?,?,?,NULL,?)', (EVIDENCE_TYPE, evidence_id, json.dumps(record), now()))
    db.execute('INSERT INTO links VALUES(?,?,?,?,?,?)', (uid(), 'evidences', EVIDENCE_TYPE, evidence_id, obj['type'], obj['id']))
    for label, ref_type, ref_id in refs:
        db.execute('INSERT OR IGNORE INTO links VALUES(?,?,?,?,?,?)', (uid(), label, EVIDENCE_TYPE, evidence_id, ref_type, ref_id))
    return {'evidence': evidence_id, 'record': record, 'links': [['evidences', obj['type'], obj['id']]] + [list(r) for r in refs]}


def import_source(db, data, source_id=None):
    name, type_name = required(data, 'name'), required(data, 'type')
    kind = data.get('kind', 'json')
    if kind == 'http':
        rows = request_json(required(data, 'url'))
        config = {'kind': kind, 'url': data['url'], 'id_field': data.get('id_field', 'id')}
    elif kind == 'csv':
        rows = list(csv.DictReader(io.StringIO(data.get('content', ''))))
        config = {'kind': kind, 'id_field': data.get('id_field', 'id')}
    elif kind == 'json':
        rows = data.get('rows')
        if rows is None:
            rows = json.loads(data.get('content', '[]'))
        config = {'kind': kind, 'id_field': data.get('id_field', 'id')}
    else:
        raise ValueError('Source kind must be csv, json, or http')
    if not isinstance(rows, list) or not rows or any(not isinstance(r, dict) for r in rows):
        raise ValueError('Data must be a non-empty array of objects (or CSV with headers)')
    if len(rows) > 10000:
        raise ValueError('This mini version supports at most 10,000 rows per import')
    id_field = config['id_field']
    if not isinstance(id_field, str) or not id_field:
        raise ValueError('Set an ID field')
    ids = []
    for row in rows:
        value = row.get(id_field)
        if value is None or isinstance(value, (dict, list, bool)) or not str(value).strip():
            raise ValueError(f'Every row needs a non-empty {id_field} value')
        ids.append(str(value))
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate IDs in source; import cancelled')
    source_id = source_id or uid()
    db.execute('INSERT OR IGNORE INTO types VALUES(?,?)', (type_name, ''))
    db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET rows=excluded.rows,updated=excluded.updated',
               (source_id, name, type_name, json.dumps(config), json.dumps(rows), now()))
    for object_id, row in zip(ids, rows):
        db.execute('INSERT INTO objects VALUES(?,?,?,?,?) ON CONFLICT(type,id) DO UPDATE SET data=excluded.data,source=excluded.source,updated=excluded.updated',
                   (type_name, object_id, json.dumps(row), source_id, now()))
    event(db, 'import', {'source': name, 'type': type_name, 'rows': len(rows)})
    return {'id': source_id, 'count': len(rows)}


def state(db):
    result = {}
    for table in ('types', 'objects', 'links', 'sources', 'actions'):
        result[table] = [dict(row) for row in db.execute(f'SELECT * FROM {table}')]
    for obj in result['objects']:
        obj['data'] = json.loads(obj['data'])
    for source in result['sources']:
        source['config'] = json.loads(source['config'])
        source['count'] = len(json.loads(source.pop('rows')))
    for action in result['actions']:
        action['config'] = json.loads(action['config'])
    result['events'] = [dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100')]
    for entry in result['events']:
        entry['detail'] = json.loads(entry['detail'])
    return result


def mutate(db, path, data):
    if path == '/api/types':
        name = required(data, 'name')
        db.execute('INSERT INTO types VALUES(?,?)', (name, str(data.get('description', ''))))
        event(db, 'type created', {'type': name})
    elif path == '/api/import':
        return import_source(db, data)
    elif path == '/api/refresh':
        source = db.execute('SELECT * FROM sources WHERE id=?', (required(data, 'id'),)).fetchone()
        if not source:
            raise ValueError('Source not found')
        config = json.loads(source['config'])
        if config['kind'] != 'http':
            raise ValueError('Only HTTP sources can refresh; re-import files instead')
        return import_source(db, {**config, 'name': source['name'], 'type': source['type']}, source['id'])
    elif path == '/api/objects':
        type_name, object_id = required(data, 'type'), required(data, 'id')
        props = data.get('data')
        if not isinstance(props, dict):
            raise ValueError('Properties must be a JSON object')
        before = db.execute('SELECT data FROM objects WHERE type=? AND id=?', (type_name, object_id)).fetchone()
        db.execute('INSERT INTO objects VALUES(?,?,?,NULL,?) ON CONFLICT(type,id) DO UPDATE SET data=excluded.data,updated=excluded.updated',
                   (type_name, object_id, json.dumps(props), now()))
        event(db, 'object saved', {'type': type_name, 'id': object_id, 'before': json.loads(before[0]) if before else None, 'after': props})
    elif path == '/api/links':
        fields = [required(data, k) for k in ('label', 'from_type', 'from_id', 'to_type', 'to_id')]
        db.execute('INSERT INTO links VALUES(?,?,?,?,?,?)', (uid(), *fields))
        event(db, 'link created', data)
    elif path == '/api/actions':
        name, type_name, kind = (required(data, k) for k in ('name', 'type', 'kind'))
        config = data.get('config', {})
        if not isinstance(config, dict):
            raise ValueError('Configuration must be an object')
        if kind == 'update':
            if not isinstance(config.get('patch'), dict):
                raise ValueError('Update actions need a patch object')
        elif kind == 'http':
            url = required(config, 'url')
            if urlsplit(url).scheme not in ('http', 'https'):
                raise ValueError('Use an HTTP endpoint')
        elif kind == 'record':
            validate_record_config(config)
        else:
            raise ValueError('Action kind must be update, http, or record')
        db.execute('INSERT INTO actions VALUES(?,?,?,?,?)', (uid(), name, type_name, kind, json.dumps(config)))
        event(db, 'action created', {'name': name, 'type': type_name})
    elif path == '/api/run':
        action = db.execute('SELECT * FROM actions WHERE id=?', (required(data, 'action'),)).fetchone()
        if not action:
            raise ValueError('Action not found')
        object_id = required(data, 'id')
        obj = db.execute('SELECT * FROM objects WHERE type=? AND id=?', (action['type'], object_id)).fetchone()
        if not obj:
            raise ValueError('Object not found')
        props, config = json.loads(obj['data']), json.loads(action['config'])
        if action['kind'] == 'update':
            after = {**props, **config['patch']}
            db.execute('UPDATE objects SET data=?,updated=? WHERE type=? AND id=?', (json.dumps(after), now(), obj['type'], object_id))
            detail = {'before': props, 'after': after}
        elif action['kind'] == 'record':
            detail = apply_record(db, action, obj, data.get('params'))
        else:
            try:
                response = request_json(config['url'], 'POST', {'type': obj['type'], 'id': object_id, 'properties': props})
                detail = {'url': config['url'], 'response': response}
            except Exception as exc:
                event(db, 'action failed', {'action': action['name'], 'id': object_id, 'error': str(exc)})
                return {'ok': False, 'error': f'HTTP action failed: {exc}. Check the receiving system before retrying.'}
        event(db, 'action run', {'action': action['name'], 'type': obj['type'], 'id': object_id, **detail})
        return {'ok': True, 'result': detail}
    elif path == '/api/demo':
        if db.execute('SELECT COUNT(*) FROM types').fetchone()[0]:
            raise ValueError('Demo can only be loaded into an empty workspace')
        import_source(db, {'name': 'Example machines', 'type': 'Machine', 'rows': [
            {'id': 'M-101', 'name': 'CNC Mill 01', 'status': 'Running', 'site': 'North plant', 'temperature': 64},
            {'id': 'M-102', 'name': 'Assembly Arm 02', 'status': 'Needs inspection', 'site': 'North plant', 'temperature': 82},
            {'id': 'M-103', 'name': 'Laser Cutter 03', 'status': 'Idle', 'site': 'South plant', 'temperature': 24}]})
        import_source(db, {'name': 'Example work orders', 'type': 'WorkOrder', 'rows': [
            {'id': 'WO-201', 'name': 'Inspect arm bearing', 'status': 'Open', 'priority': 'High', 'owner': 'Alex'},
            {'id': 'WO-202', 'name': 'Replace cooling filter', 'status': 'Open', 'priority': 'Normal', 'owner': 'Sam'}]})
        mutate(db, '/api/links', {'label': 'maintains', 'from_type': 'WorkOrder', 'from_id': 'WO-201', 'to_type': 'Machine', 'to_id': 'M-102'})
        mutate(db, '/api/links', {'label': 'maintains', 'from_type': 'WorkOrder', 'from_id': 'WO-202', 'to_type': 'Machine', 'to_id': 'M-101'})
        mutate(db, '/api/actions', {'name': 'Complete work order', 'type': 'WorkOrder', 'kind': 'update', 'config': {'patch': {'status': 'Complete'}}})
        mutate(db, '/api/actions', {'name': 'Schedule inspection', 'type': 'Machine', 'kind': 'update', 'config': {'patch': {'status': 'Inspection scheduled'}}})
    else:
        raise ValueError('Unknown API route')
    return {'ok': True}


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type='application/json'):
        raw = json.dumps(body).encode() if content_type == 'application/json' else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        # Blueprint uses inline styles for overlay positioning, tabs, and icons.
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def allowed(self):
        expected = f'127.0.0.1:{self.server.server_port}'
        alternate = f'localhost:{self.server.server_port}'
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        hosts = {expected, alternate, *ALLOWED_HOSTS}
        origins = {f'http://{expected}', f'http://{alternate}', *(f'{scheme}://{h}' for h in ALLOWED_HOSTS for scheme in ('https', 'http'))}
        return host in hosts and (not origin or origin in origins)

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {'error': 'Local requests only'})
        path = unquote(urlsplit(self.path).path)
        if path == '/api/state':
            with connect() as db:
                return self.send(200, state(db))
        files = {'/': 'index.html', '/app.js': 'app.js', '/app.css': 'app.css',
                 '/app.js.LEGAL.txt': 'app.js.LEGAL.txt'}
        if path not in files:
            return self.send(404, {'error': 'Not found'})
        file = ROOT / 'web' / 'static' / files[path]
        self.send(200, file.read_bytes(), mimetypes.guess_type(file)[0] or 'text/plain')

    def do_POST(self):
        if not self.allowed():
            return self.send(403, {'error': 'Local requests only'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 1 or size > MAX_BYTES:
                raise ValueError('Request body must be between 1 byte and 5 MB')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object')
            with connect() as db:
                result = mutate(db, urlsplit(self.path).path, data)
            self.send(200, result)
        except (ValueError, sqlite3.IntegrityError) as exc:
            self.send(400, {'error': str(exc)})
        except Exception as exc:
            self.log_error('%s', exc)
            self.send(502, {'error': str(exc)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8000)))
    parser.add_argument('--db', type=Path, default=DB)
    parser.add_argument('--host', default=os.environ.get('HOST', '127.0.0.1'), help='Bind address; use 0.0.0.0 behind a hosting platform and set ALLOWED_HOSTS')
    args = parser.parse_args()
    DB = args.db.resolve()
    init_db()
    print(f'Local workbench: http://{args.host}:{args.port}\nDatabase: {DB}\nAllowed hosts: {", ".join(sorted(ALLOWED_HOSTS)) or "loopback only"}', flush=True)
    try:
        ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
