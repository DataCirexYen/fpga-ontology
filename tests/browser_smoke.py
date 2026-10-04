"""Optional browser regression test: python3 tests/browser_smoke.py.

Requires Playwright and Chromium; uses temporary data and local mock endpoints.
Set SCREENSHOT_DIR to retain desktop/mobile screenshots.
"""
import json
import os
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from test_server import SystemHandler


def main():
    with tempfile.TemporaryDirectory() as temp:
        server.DB = Path(temp) / 'browser.db'
        server.init_db()
        app = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        system = ThreadingHTTPServer(('127.0.0.1', 0), SystemHandler)
        for http in (app, system):
            threading.Thread(target=http.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{app.server_port}'
        endpoint = f'http://127.0.0.1:{system.server_port}'
        screenshots = Path(os.environ.get('SCREENSHOT_DIR', temp))
        screenshots.mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
                errors, remote_requests = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
                page.on('request', lambda req: remote_requests.append(req.url) if not req.url.startswith(base + '/') else None)

                def button(name):
                    return page.get_by_role('button', name=name, exact=True)

                def field(name, value):
                    page.locator(f'[name="{name}"]').fill(value)

                def save(name='Save'):
                    button(name).click()
                    expect(page.locator('.bp6-dialog')).to_have_count(0)

                def nav(view):
                    page.locator(f'[data-view="{view}"]').click()

                def tree(name):
                    page.locator('.bp6-tree-node-content').filter(has_text=name).click()

                page.goto(base)
                expect(button('Switch to light mode')).to_be_visible()
                button('Switch to light mode').click()
                expect(page.locator('html')).not_to_have_class('bp6-dark')
                page.reload()
                expect(button('Switch to dark mode')).to_be_visible()
                button('Switch to dark mode').click()
                page.reload()
                expect(page.locator('html')).to_have_class('bp6-dark')
                expect(button('Try example workspace')).to_be_visible()
                button('Try example workspace').click()
                expect(button('M-101')).to_be_visible()
                expect(page.locator('.bp6-html-table').first).to_be_visible()
                expect(page.locator('.bp6-navbar svg').first).to_be_visible()
                # Let the success notification clear before visual inspection.
                expect(page.locator('.bp6-toast')).to_have_count(0, timeout=7000)

                for width in (1440, 390):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    for view in ('objects', 'sources', 'ontology', 'actions', 'activity'):
                        nav(view)
                        page.screenshot(path=str(screenshots / f'blueprint-{view}-{width}.png'), full_page=True, animations='disabled')
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (view, width, 'page overflow')

                page.set_viewport_size({'width': 1440, 'height': 1000})
                nav('objects')
                page.get_by_role('tab', name='Actions', exact=True).click()
                button('Schedule inspection').click()
                page.screenshot(path=str(screenshots / 'blueprint-action-dialog.png'), animations='disabled')
                save('Run action')
                page.get_by_role('tab', name='Properties', exact=True).click()
                expect(page.locator('#detail')).to_contain_text('Inspection scheduled')

                button('Edit properties').click()
                field('data', '{"name":"Edited machine","status":"Ready"}')
                save()
                expect(page.locator('#detail h2')).to_have_text('Edited machine')
                page.get_by_role('textbox', name='Search objects').fill('no-match')
                expect(page.get_by_text('No matching objects', exact=True)).to_be_visible()
                button('Clear search').click()
                expect(button('M-101')).to_be_visible()

                button('Import data').click()
                field('name', 'Browser CSV')
                field('type', 'Customer')
                page.locator('[name=kind]').select_option('csv')
                page.locator('#source-file').set_input_files({'name': 'customers.csv', 'mimeType': 'text/csv', 'buffer': b'id,name\nC1,Acme'})
                expect(page.locator('[name=content]')).to_have_value('id,name\nC1,Acme')
                save('Import')
                expect(button('C1')).to_be_visible()
                page.get_by_role('tab', name='Relationships', exact=True).click()
                button('Add relationship').click()
                field('label', 'owns')
                save()
                page.locator('[data-link]').click()
                expect(page.locator('#title')).to_have_text('Machine')

                button('New object').click()
                field('id', 'M-new')
                field('data', 'invalid json')
                button('Save').click()
                expect(page.locator('#form-error')).to_be_visible()
                field('data', '{"name":"New machine"}')
                save()
                expect(page.locator('#detail h2')).to_have_text('New machine')

                nav('ontology')
                button('New object type').click()
                field('name', 'EmptyType')
                save()
                expect(page.get_by_text('No records', exact=True)).to_be_visible()
                nav('actions')
                button('New action').click()
                field('name', 'Set active')
                page.locator('[name=type]').select_option('Machine')
                field('config', '{"patch":{"status":"Active"}}')
                save()
                tree('Machine')
                page.get_by_role('tab', name='Actions', exact=True).click()
                button('Set active').click()
                save('Run action')
                page.get_by_role('tab', name='Properties', exact=True).click()
                expect(page.locator('#detail')).to_contain_text('Active')

                SystemHandler.rows = [{'id': 'S1', 'name': 'System asset', 'status': 'Ready'}]
                button('Import data').click()
                field('name', 'Local system')
                field('type', 'SystemAsset')
                page.locator('[name=kind]').select_option('http')
                field('url', endpoint)
                save('Import')
                expect(button('S1')).to_be_visible()
                SystemHandler.rows[0]['status'] = 'Refreshed'
                nav('sources')
                button('Refresh').click()
                expect(page.get_by_text('Source refreshed', exact=True)).to_be_visible()
                tree('SystemAsset')
                expect(page.locator('#detail')).to_contain_text('Refreshed')
                nav('actions')
                button('New action').click()
                field('name', 'Send to system')
                page.locator('[name=kind]').select_option('http')
                field('config', json.dumps({'url': endpoint}))
                save()
                tree('SystemAsset')
                page.get_by_role('tab', name='Actions', exact=True).click()
                button('Send to system').click()
                save('Run action')
                assert SystemHandler.received[-1]['properties']['status'] == 'Refreshed'

                # Blueprint dialog dismissal and mobile form containment.
                page.set_viewport_size({'width': 390, 'height': 844})
                button('Import data').click()
                expect(page.locator('[name=name]')).to_be_focused()
                page.screenshot(path=str(screenshots / 'blueprint-import-mobile.png'), full_page=True, animations='disabled')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.keyboard.press('Escape')
                expect(page.locator('.bp6-dialog')).to_have_count(0)
                page.reload()
                expect(page.locator('.bp6-tree-node-content').filter(has_text='SystemAsset')).to_be_visible()
                assert not errors, errors
                assert not remote_requests, remote_requests
                print('PASS: Blueprint desktop/mobile views, dialogs, file/HTTP imports, refresh, edit, links, local/HTTP actions, validation, persistence, and fully local assets')
                browser.close()
        finally:
            for http in (app, system):
                http.shutdown()
                http.server_close()


if __name__ == '__main__':
    main()
