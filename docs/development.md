# Development

## Verify

```sh
python3 -m unittest -v
node --check web/static/app.js
```

Tests use temporary databases and a local mock HTTP system. They cover imports, rollback, persistence, relationships, actions, HTTP refresh/write-back, error logging, and origin checks.

An optional browser regression suite uses Playwright with a temporary database and mock HTTP system:

```sh
python3 -m pip install playwright
python3 -m playwright install chromium
python3 tests/browser_smoke.py
```

It covers Blueprint rendering at desktop/mobile widths, dialogs, file and HTTP imports, refresh, property editing, links, local and HTTP actions, validation, persistence, and checks that browser assets never use a CDN. Set `SCREENSHOT_DIR` to retain its screenshots.
## Frontend development

The browser app lives in `web/`: React and the official Blueprint components in `web/src/`, and the prebuilt bundle, Blueprint CSS, SVG icons, and dependency license notices in `web/static/`. No Node server or internet connection is needed at runtime.

To change the frontend, install Node.js 20+ and run:

```sh
cd web
npm ci
npm run build
```

`npm run watch` rebuilds on source changes; refresh the browser to see them. Continue running `python3 server.py` for the app and API. Dependency versions are pinned in `web/package-lock.json`.

The UI uses Blueprint's Navbar, Buttons, Tree, Breadcrumbs, HTMLTable, Tabs, Dialog, form controls, NonIdealState, and OverlayToaster. It is a custom local workbench built with Blueprint, not Palantir's proprietary Foundry application frontend.
## Files

- `server.py`: SQLite model, import logic, actions, and HTTP server.
- `web/`: browser app. `src/` holds the React source and layout styles, `static/` the prebuilt bundle and dependency notices, `build.mjs` and `package.json` the reproducible build.
- `examples/fpga_lab/`: FPGA lab example data and its idempotent loader with six record actions.
- `tests/`: standard-library integration tests for the server and the example loader, plus the optional Playwright browser checks.

Licensed under MIT; see `LICENSE`.
