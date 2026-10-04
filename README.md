# FPGA Ontology

A tiny, free, local-first ontology workbench for FPGA and hardware bring-up, inspired by Palantir Foundry's basic model:

**Data sources → object types → objects + relationships → actions.**

Independent project; not affiliated with Palantir. The frontend uses **Palantir's official Blueprint.js 6 React component library** (`@blueprintjs/core` and `@blueprintjs/icons`). No cloud account or paid API is needed. The compiled frontend is included, so Python 3.10+ and a browser are enough to run it offline.

## Run

```sh
python3 server.py
```

Open **http://127.0.0.1:8000**. Click **Try example workspace**, or load the FPGA lab example with `python3 examples/fpga_lab/load.py` and restart. Data survives restarts in `data/workbench.db`.

## The basics

| Foundry-style concept | Here |
| --- | --- |
| Data connection / dataset | CSV, JSON, or an HTTP GET endpoint returning an array |
| Ontology object type | Named collection such as Machine, Customer, or Order |
| Object | Stable ID and JSON properties |
| Link | Named, directional connection between two objects |
| Action | Reusable property update, HTTP POST to another system, or a typed **record** action that writes an EvidenceRecord |
| Lineage / history | Source attribution, retained source snapshot, and change events |

1. **Import data:** choose a source name, object type, and ID field. Paste data or select a file. A missing object type is created automatically.
2. **Objects:** search records, select an ID, and use the **Properties**, **Relationships**, and **Actions** tabs in the details panel. Link to another object—even one of a different type.
3. **Ontology:** inspect types, inferred property shapes, and all connections.
4. **Actions:** create an operation, then run it from an object's details panel.
5. **Activity:** inspect imports, before/after changes, and HTTP action responses.

## More

- [FPGA lab example](examples/fpga_lab/README.md): boards, kernels, builds, gates, and six gated record actions.
- [Guide](docs/guide.md): data formats, connecting systems, record actions, the local API, scope.
- [Development](docs/development.md): tests, frontend build, file layout.

## License

MIT; see [LICENSE](LICENSE).
