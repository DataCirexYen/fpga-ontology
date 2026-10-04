#!/usr/bin/env python3
"""Load the FPGA lab example into a workbench database.

A small bring-up ontology for an FPGA lab: hosts, boards, kernels, builds, release gates,
requirements, and work-order steps, plus six record actions that write evidence instead of
mutating the objects they describe. The CSV and JSON files beside this script are the system of
record; running the script again re-imports them (upsert by type + ID), adds missing links, and
never touches EvidenceRecord objects.

    python3 examples/fpga_lab/load.py               # into data/workbench.db
    python3 examples/fpga_lab/load.py --db lab.db
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import server  # noqa: E402

TYPES = {
    'Host': 'A build or lab machine that carries boards and runs the host driver.',
    'Board': 'One physical FPGA card or SoM, identified by its lab ID. Power readings arrive as evidence.',
    'Kernel': 'An accelerator design (RTL or HLS) with its golden model. Type-level, not a build.',
    'Build': 'One synthesis of a kernel for a board with a specific tool and strategy. Results arrive as evidence.',
    'Gate': 'A release gate G0 to G4: the time axis of bring-up. Gate results arrive as evidence.',
    'Requirement': 'A binding rule or an open release hold that governs builds and boards.',
    'WorkOrderStep': 'One numbered physical step of the bring-up work order. PROCEED / STOP / ROLL BACK arrive as evidence.',
    server.EVIDENCE_TYPE: 'Immutable records written by actions. The latest record of each kind is the current state of its target.',
}

stats = Counter()


def read_csv(path):
    with open(path, newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def numeric(rows, *keys):
    for row in rows:
        for key in keys:
            if row.get(key) not in (None, ''):
                row[key] = float(row[key]) if '.' in row[key] else int(row[key])
    return rows


def ensure_type(db, name, description):
    db.execute('INSERT OR IGNORE INTO types VALUES(?,?)', (name, description))
    db.execute("UPDATE types SET description=? WHERE name=? AND description=''", (description, name))


def load_source(db, name, type_name, rows):
    existing = db.execute('SELECT id FROM sources WHERE name=? AND type=?', (name, type_name)).fetchone()
    result = server.import_source(db, {'name': name, 'type': type_name, 'kind': 'json', 'rows': rows}, existing['id'] if existing else None)
    stats[f'objects:{type_name}'] += result['count']


def link(db, label, from_type, from_id, to_type, to_id):
    for type_name, object_id in ((from_type, from_id), (to_type, to_id)):
        if not db.execute('SELECT 1 FROM objects WHERE type=? AND id=?', (type_name, object_id)).fetchone():
            raise SystemExit(f'link {label}: {from_type}/{from_id} -> {to_type}/{to_id} refers to a missing {type_name}')
    cursor = db.execute('INSERT OR IGNORE INTO links VALUES(?,?,?,?,?,?)', (server.uid(), label, from_type, from_id, to_type, to_id))
    stats['links'] += cursor.rowcount


def define_action(db, name, type_name, config):
    server.validate_record_config(config)
    row = db.execute('SELECT id FROM actions WHERE name=? AND type=?', (name, type_name)).fetchone()
    if row:
        db.execute('UPDATE actions SET kind=?, config=? WHERE id=?', ('record', json.dumps(config), row['id']))
    else:
        server.mutate(db, '/api/actions', {'name': name, 'type': type_name, 'kind': 'record', 'config': config})
    stats['actions'] += 1


def load_objects(db):
    hosts = read_json(HERE / 'hosts.json')
    boards = numeric(read_csv(HERE / 'boards.csv'), 'power_cap_w')
    kernels = numeric(read_csv(HERE / 'kernels.csv'), 'clock_mhz')
    builds = numeric(read_csv(HERE / 'builds.csv'), 'target_mhz', 'luts', 'dsps', 'brams')
    gates = read_json(HERE / 'gates.json')
    requirements = read_csv(HERE / 'requirements.csv')
    steps = numeric(read_csv(HERE / 'steps.csv'), 'step')
    load_source(db, 'Lab hosts', 'Host', hosts)
    load_source(db, 'Board inventory', 'Board', boards)
    load_source(db, 'Kernel catalog', 'Kernel', kernels)
    load_source(db, 'Build log', 'Build', builds)
    load_source(db, 'Release gates', 'Gate', gates)
    load_source(db, 'Requirements', 'Requirement', requirements)
    load_source(db, 'Bring-up work order', 'WorkOrderStep', steps)
    return boards, builds, gates, requirements, steps


def load_links(db, boards, builds, gates, requirements, steps):
    for board in boards:
        link(db, 'installed_in', 'Board', board['id'], 'Host', board['host'])
    for build in builds:
        link(db, 'builds', 'Build', build['id'], 'Kernel', build['kernel'])
        link(db, 'targets', 'Build', build['id'], 'Board', build['board'])
    for gate in gates:
        if gate.get('requires'):
            link(db, 'requires', 'Gate', gate['id'], 'Gate', gate['requires'])
    for requirement in requirements:
        if requirement['applies_to'] == 'all':
            for build in builds:
                link(db, 'governed_by', 'Build', build['id'], 'Requirement', requirement['id'])
        else:
            link(db, 'governed_by', 'Board', requirement['applies_to'], 'Requirement', requirement['id'])
    for step in steps:
        link(db, 'touches', 'WorkOrderStep', step['id'], 'Board', step['board'])
        link(db, 'released_by', 'WorkOrderStep', step['id'], 'Gate', step['release_gate'])


def define_actions(db):
    by = {'name': 'recorded_by', 'label': 'Recorded by', 'type': 'string', 'required': True}
    define_action(db, 'Record synthesis result', 'Build', {
        'evidence_kind': 'synthesis',
        'parameters': [
            {'name': 'result', 'label': 'Result', 'type': 'enum', 'values': ['TIMING_MET', 'TIMING_FAILED', 'BUILD_FAILED'], 'required': True},
            {'name': 'fmax_mhz', 'label': 'Achieved Fmax (MHz)', 'type': 'number'},
            {'name': 'wns_ns', 'label': 'Worst negative slack (ns)', 'type': 'number'},
            {'name': 'report_uri', 'label': 'Timing report URI', 'type': 'uri', 'required': True},
            {'name': 'notes', 'label': 'Notes', 'type': 'text'},
            by],
        'criteria': [
            {'kind': 'required_if', 'param': 'notes', 'when': {'result': ['TIMING_FAILED', 'BUILD_FAILED']},
             'message': 'A failed build needs notes: the failing path or error, and what will change in the next run.'}],
    })
    define_action(db, 'Record regression run', 'Build', {
        'evidence_kind': 'regression',
        'parameters': [
            {'name': 'verdict', 'label': 'Verdict', 'type': 'enum', 'values': ['BIT_EXACT', 'MISMATCH'], 'required': True},
            {'name': 'vectors', 'label': 'Vectors compared', 'type': 'number', 'required': True},
            {'name': 'log_uri', 'label': 'Regression log URI', 'type': 'uri', 'required': True},
            {'name': 'notes', 'label': 'Notes', 'type': 'text'},
            by],
        'criteria': [
            {'kind': 'required_if', 'param': 'notes', 'when': {'verdict': 'MISMATCH'},
             'message': 'A mismatch needs notes: first differing vector, expected and actual values.'}],
    })
    define_action(db, 'Record power measurement', 'Board', {
        'evidence_kind': 'power',
        'parameters': [
            {'name': 'watts', 'label': 'Board power (W)', 'type': 'number', 'required': True},
            {'name': 'at_clock_mhz', 'label': 'Kernel clock during measurement (MHz)', 'type': 'number'},
            {'name': 'build', 'label': 'Build running', 'type': 'object', 'object_type': 'Build', 'link': 'measured_with'},
            {'name': 'meter', 'label': 'Meter or method', 'type': 'string'},
            by],
        'criteria': [
            {'kind': 'max_from_property', 'param': 'watts', 'property': 'power_cap_w',
             'message': 'Reading exceeds the board power cap. Stop the kernel and record the excursion before any retry.'}],
    })
    define_action(db, 'Set release-hold status', 'Requirement', {
        'evidence_kind': 'release',
        'parameters': [
            {'name': 'status', 'label': 'Status', 'type': 'enum', 'values': ['HOLD', 'RELEASED'], 'required': True},
            {'name': 'evidence_uri', 'label': 'Release evidence URI', 'type': 'uri'},
            {'name': 'notes', 'label': 'Notes', 'type': 'text'},
            by],
        'criteria': [
            {'kind': 'target_property', 'property': 'kind', 'equals': 'release-hold',
             'message': 'Only release holds can be released. Binding rules do not change through this action.'},
            {'kind': 'required_if', 'param': 'evidence_uri', 'when': {'status': 'RELEASED'},
             'message': 'A hold is released only against evidence.'}],
    })
    define_action(db, 'Bring-up step control', 'WorkOrderStep', {
        'evidence_kind': 'control',
        'parameters': [
            {'name': 'decision', 'label': 'Decision', 'type': 'enum', 'values': ['PROCEED', 'STOP', 'ROLL BACK'], 'required': True},
            {'name': 'observation', 'label': 'Observation', 'type': 'text', 'required': True},
            {'name': 'log_uri', 'label': 'Console log or photo URI', 'type': 'uri'},
            by],
        'criteria': [
            {'kind': 'transition', 'param': 'decision',
             'allowed': {'': ['PROCEED', 'STOP'], 'PROCEED': ['STOP'], 'STOP': ['PROCEED', 'ROLL BACK'], 'ROLL BACK': ['PROCEED', 'STOP']},
             'message': 'That decision is not allowed from the step\'s current state. A completed step can only be stopped; a stopped step proceeds or rolls back.'}],
    })
    define_action(db, 'Record gate result', 'Gate', {
        'evidence_kind': 'gate',
        'parameters': [
            {'name': 'result', 'label': 'Result', 'type': 'enum', 'values': ['PASSED', 'FAILED'], 'required': True},
            {'name': 'evidence_uri', 'label': 'Evidence URI', 'type': 'uri', 'required': True},
            {'name': 'build', 'label': 'Build under review', 'type': 'object', 'object_type': 'Build', 'link': 'reviewed'},
            by],
        'criteria': [
            {'kind': 'requires_latest_evidence', 'target_from_property': 'requires', 'target_type': 'Gate', 'evidence_kind': 'gate',
             'param': 'result', 'equals': 'PASSED', 'message': 'The preceding gate must have PASSED before this gate can be recorded.'}],
    })


def run(db_path):
    server.DB = Path(db_path).resolve()
    server.init_db()
    with server.connect() as db:
        for name, description in TYPES.items():
            ensure_type(db, name, description)
        load_links(db, *load_objects(db))
        define_actions(db)
        server.event(db, 'import', {'source': 'examples/fpga_lab/load.py', 'type': 'ontology',
                                    'rows': sum(v for k, v in stats.items() if k.startswith('objects:'))})
        counts = {t: db.execute('SELECT COUNT(*) FROM objects WHERE type=?', (t,)).fetchone()[0] for t in TYPES}
        links = db.execute('SELECT COUNT(*) FROM links').fetchone()[0]
    return counts, links


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', type=Path, default=server.DB)
    args = parser.parse_args()
    counts, links = run(args.db)
    print(f'Database: {server.DB}')
    for type_name, count in counts.items():
        print(f'  {type_name:<16} {count:>5}')
    print(f'  {"links":<16} {links:>5}   ({stats["links"]} new)')
    print(f'  {"actions":<16} {stats["actions"]:>5}')


if __name__ == '__main__':
    main()
