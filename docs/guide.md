# Guide

Everything beyond the [README](../README.md): data formats, connecting systems, record actions, the local API, and scope.

## Data formats

JSON files and HTTP data sources use an array of objects:

```json
[
  {"id": "M-001", "name": "Pump", "status": "Running"},
  {"id": "M-002", "name": "Valve", "status": "Idle"}
]
```

CSV needs a header and a unique ID column:

```csv
id,name,status
M-001,Pump,Running
M-002,Valve,Idle
```

Each import retains a source snapshot and upserts objects by **(type, ID)**. A matching object gets its properties replaced and its source attribution updated. Missing rows are kept. Manual edits and actions update ontology objects, not the retained source snapshot. Refreshing a source can overwrite these local edits. CSV values remain strings; JSON preserves value types. Properties are inferred, not schema-enforced.

Limits: 5 MB per request/HTTP response, 10,000 rows per import. The browser loads the workspace into memory, so this is intended for small datasets.
## Connect a system

For ingestion, choose **HTTP JSON endpoint**, enter the URL, and import. Use **Data sources → Refresh** to pull the latest records. Refresh is manual.

For a write-back operation, create an action with **POST object to a system** and this configuration:

```json
{"url": "http://127.0.0.1:9000/action"}
```

Running it sends:

```json
{
  "type": "Machine",
  "id": "M-001",
  "properties": {"id": "M-001", "name": "Pump", "status": "Running"}
}
```

The endpoint should return JSON or an empty success response. There is a 10-second timeout and no automatic retry. A timeout/error does not prove the remote operation was not performed; check the receiving system before retrying. HTTP operations cannot be atomically committed together with SQLite changes.

For a local action, choose **Update local properties**:

```json
{"patch": {"status": "Inspection scheduled"}}
```

This performs a shallow merge and records before/after properties.
## Record actions

A `record` action is the closest thing here to a Foundry action type. It declares typed **parameters**, optional **submission criteria**, and one fixed effect: on success it creates one immutable `EvidenceRecord` object, links it to the target with `evidences`, and links it to any object-typed parameter. The target object is never mutated, so a source refresh can never erase what was recorded. The current state of an object is the latest record of each evidence kind, shown in the object's **Evidence** tab.

```json
{
  "evidence_kind": "acceptance",
  "parameters": [
    {"name": "gate", "label": "Gate", "type": "object", "object_type": "Gate", "link": "at_gate", "required": true},
    {"name": "verdict", "type": "enum", "values": ["ACCEPTED", "QUARANTINED"], "required": true},
    {"name": "evidence_uri", "type": "uri", "required": true},
    {"name": "notes", "type": "text"}
  ],
  "criteria": [
    {"kind": "required_if", "param": "notes", "when": {"verdict": "QUARANTINED"}, "message": "Say what mismatched."}
  ]
}
```

Parameter types: `string`, `text`, `number`, `enum` (`values`), `object` (`object_type`, optional `link` label), `uri`, `date`. Criteria kinds: `max_from_property` (a numeric parameter may not exceed a property of the target), `required_if` (a parameter is required when another has a value), `transition` (a state machine over the latest record of this evidence kind), `requires_latest_evidence` (an object named by a target property must already carry a given record), and `target_property` (the action applies only to targets with a property value). A refused action has no side effect and is not logged; an applied one is logged as `action run` with its evidence ID.

Run one with `POST /api/run` and a `params` object:

```sh
curl http://127.0.0.1:8000/api/run -H 'Content-Type: application/json' \
  -d '{"action":"<action id>","id":"RB-A","params":{"serial":"FS725-1234","recorded_by":"owner"}}'
```
## Local API

The interface uses the same API available to your scripts:

```sh
curl http://127.0.0.1:8000/api/state

curl http://127.0.0.1:8000/api/import \
  -H 'Content-Type: application/json' \
  -d '{"name":"My assets","type":"Asset","rows":[{"id":"A1","name":"Pump"}]}'

curl http://127.0.0.1:8000/api/objects \
  -H 'Content-Type: application/json' \
  -d '{"type":"Asset","id":"A1","data":{"name":"Pump","status":"Ready"}}'
```

`GET /api/state` returns types, objects, links, source metadata, actions, and the latest 100 events. All stored events remain in SQLite.

POST endpoints (JSON request bodies):

| Endpoint | Fields |
| --- | --- |
| `/api/types` | `name`, optional `description` |
| `/api/import` | `name`, `type`, `kind` (`json`, `csv`, `http`), optional `id_field` (default `id`), plus `rows`/`content`/`url` |
| `/api/refresh` | source `id` |
| `/api/objects` | `type`, `id`, `data` (full property replacement) |
| `/api/links` | `label`, `from_type`, `from_id`, `to_type`, `to_id` |
| `/api/actions` | `name`, `type`, `kind` (`update`, `http`, or `record`), `config` |
| `/api/run` | `action` (action ID), `id` (object ID), optional `params` object for record actions |
| `/api/demo` | empty object; requires empty workspace |
## Scope and storage

This is a single-user foundation, not a Foundry replacement. It has no login, enterprise permissions, distributed compute, pipeline designer, versioned ontology schemas, deletion UI, or undo. It binds to loopback only and rejects foreign browser origins/hostnames. Do not expose it as a public service.

Back up by stopping the app and copying `data/workbench.db`. To start a separate workspace, use `--db` with a new path.
