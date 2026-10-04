import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


class SystemHandler(BaseHTTPRequestHandler):
    rows = [{'id': 'A1', 'name': 'Pump', 'status': 'Ready'}]
    received = []

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(self.rows).encode())

    def do_POST(self):
        self.received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"accepted":true}')

    def log_message(self, *args):
        pass


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.external = ThreadingHTTPServer(('127.0.0.1', 0), SystemHandler)
        threading.Thread(target=cls.external.serve_forever, daemon=True).start()
        cls.endpoint = f'http://127.0.0.1:{cls.external.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.external.shutdown()
        cls.external.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = server.DB
        server.DB = Path(self.temp.name) / 'test.db'
        server.init_db()

    def tearDown(self):
        server.DB = self.old_db
        self.temp.cleanup()

    def call(self, path, data):
        with server.connect() as db:
            return server.mutate(db, '/api/' + path, data)

    def snapshot(self):
        with server.connect() as db:
            return server.state(db)

    def test_demo_links_actions_and_persistence(self):
        self.call('demo', {})
        data = self.snapshot()
        self.assertEqual(len(data['objects']), 5)
        self.assertEqual(len(data['links']), 2)
        action = next(a for a in data['actions'] if a['type'] == 'WorkOrder')
        self.call('run', {'action': action['id'], 'id': 'WO-201'})
        server.init_db()
        updated = self.snapshot()
        self.assertEqual(next(o for o in updated['objects'] if o['id'] == 'WO-201')['data']['status'], 'Complete')
        self.assertEqual(updated['events'][0]['detail']['before']['status'], 'Open')
        with self.assertRaises(ValueError):
            self.call('demo', {})

    def test_csv_upsert_and_atomic_rollback(self):
        self.call('import', {'name':'CSV', 'type':'Asset', 'kind':'csv', 'content':'id,name\n1,Pump\n2,Valve'})
        self.call('import', {'name':'Updated', 'type':'Asset', 'rows':[{'id':'1','name':'New pump'}]})
        self.assertEqual(len(self.snapshot()['objects']), 2)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.call('import', {'name':'Bad', 'type':'Asset', 'rows':[{'id': '1'}, {'id': '1'}]})
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(Exception):
            self.call('links', {'label':'bad', 'from_type':'Asset', 'from_id':'1', 'to_type':'Asset', 'to_id':'missing'})
        self.assertEqual(self.snapshot(), before)

    def test_http_import_refresh_and_action(self):
        SystemHandler.rows = [{'id':'A1','status':'Ready'}]
        source = self.call('import', {'name':'System', 'type':'Asset', 'kind':'http', 'url':self.endpoint})
        SystemHandler.rows = [{'id':'A1','status':'Running'},{'id':'A2','status':'Idle'}]
        self.call('refresh', {'id':source['id']})
        self.assertEqual(len(self.snapshot()['sources']), 1)
        self.assertEqual(len(self.snapshot()['objects']), 2)
        self.call('actions', {'name':'Dispatch','type':'Asset','kind':'http','config':{'url':self.endpoint}})
        action = self.snapshot()['actions'][0]
        result = self.call('run', {'action':action['id'],'id':'A1'})
        self.assertTrue(result['result']['response']['accepted'])
        self.assertEqual(SystemHandler.received[-1]['properties']['status'], 'Running')

    def test_failed_action_is_audited(self):
        self.call('demo', {})
        self.call('actions', {'name':'Offline','type':'Machine','kind':'http','config':{'url':'http://127.0.0.1:1/action'}})
        action = next(a for a in self.snapshot()['actions'] if a['name'] == 'Offline')
        result = self.call('run', {'action':action['id'],'id':'M-101'})
        self.assertFalse(result['ok'])
        self.assertEqual(self.snapshot()['events'][0]['kind'], 'action failed')

    def test_http_routes_and_origin_guard(self):
        http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{http.server_port}'
        try:
            with urlopen(base + '/') as response:
                self.assertIn(b'FPGA / Ontology', response.read())
            req = Request(base + '/api/demo', data=b'{}', headers={'Content-Type':'application/json'})
            with urlopen(req) as response:
                self.assertEqual(response.status, 200)
            for headers in ({'Origin':'https://other.example'}, {'Host':'other.example'}):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(base + '/api/state', headers=headers))
                self.assertEqual(caught.exception.code, 403)
            with self.assertRaises(HTTPError) as caught:
                urlopen(base + '/server.py')
            self.assertEqual(caught.exception.code, 404)
        finally:
            http.shutdown()
            http.server_close()

    def record_action(self, name='Inspect', type_name='Machine', **extra):
        config = {'evidence_kind': 'inspection', 'parameters': [
            {'name': 'result', 'type': 'enum', 'values': ['PASS', 'FAIL'], 'required': True},
            {'name': 'score', 'type': 'number'},
            {'name': 'order', 'type': 'object', 'object_type': 'WorkOrder', 'link': 'for_order'},
            {'name': 'proof', 'type': 'uri'},
            {'name': 'notes', 'type': 'text'}], **extra}
        self.call('actions', {'name': name, 'type': type_name, 'kind': 'record', 'config': config})
        return next(a for a in self.snapshot()['actions'] if a['name'] == name)

    def test_record_action_creates_immutable_evidence_and_links(self):
        self.call('demo', {})
        action = self.record_action()
        before = next(o for o in self.snapshot()['objects'] if o['id'] == 'M-101')
        result = self.call('run', {'action': action['id'], 'id': 'M-101', 'params': {'result': 'PASS', 'score': '7.5', 'order': 'WO-201', 'proof': 'file:///photo.jpg'}})
        self.assertEqual(result['result']['evidence'], 'EV-00001')
        data = self.snapshot()
        evidence = next(o for o in data['objects'] if o['type'] == 'EvidenceRecord')
        self.assertEqual(evidence['data']['score'], 7.5)
        self.assertEqual(evidence['data']['target_id'], 'M-101')
        self.assertIsNone(evidence['source'])
        labels = {(l['label'], l['to_type'], l['to_id']) for l in data['links'] if l['from_id'] == 'EV-00001'}
        self.assertEqual(labels, {('evidences', 'Machine', 'M-101'), ('for_order', 'WorkOrder', 'WO-201')})
        self.assertEqual(next(o for o in data['objects'] if o['id'] == 'M-101')['data'], before['data'])
        self.assertEqual(data['events'][0]['kind'], 'action run')
        # A source refresh cannot erase evidence, because evidence is never stored on the target.
        self.call('import', {'name': 'Example machines', 'type': 'Machine', 'rows': [{'id': 'M-101', 'name': 'Renamed'}]})
        self.assertEqual(len([o for o in self.snapshot()['objects'] if o['type'] == 'EvidenceRecord']), 1)

    def test_record_action_parameter_validation(self):
        self.call('demo', {})
        action = self.record_action()
        for params, message in (({}, 'required'), ({'result': 'MAYBE'}, 'one of'), ({'result': 'PASS', 'score': 'x'}, 'number'),
                                ({'result': 'PASS', 'order': 'WO-999'}, 'no WorkOrder'), ({'result': 'PASS', 'proof': 'nope'}, 'URI')):
            with self.assertRaises(ValueError) as caught:
                self.call('run', {'action': action['id'], 'id': 'M-101', 'params': params})
            self.assertIn(message, str(caught.exception))
        self.assertFalse([o for o in self.snapshot()['objects'] if o['type'] == 'EvidenceRecord'])
        with self.assertRaises(ValueError):
            self.call('actions', {'name': 'Bad', 'type': 'Machine', 'kind': 'record', 'config': {'evidence_kind': 'x', 'parameters': [{'name': 'a', 'type': 'enum'}]}})

    def test_record_action_submission_criteria(self):
        self.call('demo', {})
        required_if = self.record_action('Verdict', criteria=[{'kind': 'required_if', 'param': 'notes', 'when': {'result': 'FAIL'}}])
        with self.assertRaises(ValueError):
            self.call('run', {'action': required_if['id'], 'id': 'M-101', 'params': {'result': 'FAIL'}})
        self.call('run', {'action': required_if['id'], 'id': 'M-101', 'params': {'result': 'FAIL', 'notes': 'cracked'}})
        capped = self.record_action('Cap', criteria=[{'kind': 'max_from_property', 'param': 'score', 'property': 'temperature'}])
        with self.assertRaises(ValueError):
            self.call('run', {'action': capped['id'], 'id': 'M-101', 'params': {'result': 'PASS', 'score': 65}})
        self.call('run', {'action': capped['id'], 'id': 'M-101', 'params': {'result': 'PASS', 'score': 64}})
        guarded = self.record_action('Guarded', criteria=[{'kind': 'target_property', 'property': 'site', 'equals': 'South plant'}])
        with self.assertRaises(ValueError):
            self.call('run', {'action': guarded['id'], 'id': 'M-101', 'params': {'result': 'PASS'}})
        self.call('run', {'action': guarded['id'], 'id': 'M-103', 'params': {'result': 'PASS'}})

    def test_record_action_state_machine_and_gate_ordering(self):
        self.call('demo', {})
        step = self.record_action('Control', criteria=[{'kind': 'transition', 'param': 'result', 'allowed': {'': ['PASS'], 'PASS': ['FAIL'], 'FAIL': ['PASS']}}])
        with self.assertRaises(ValueError):
            self.call('run', {'action': step['id'], 'id': 'M-101', 'params': {'result': 'FAIL'}})
        self.call('run', {'action': step['id'], 'id': 'M-101', 'params': {'result': 'PASS'}})
        with self.assertRaises(ValueError):
            self.call('run', {'action': step['id'], 'id': 'M-101', 'params': {'result': 'PASS'}})
        self.call('run', {'action': step['id'], 'id': 'M-101', 'params': {'result': 'FAIL'}})
        self.call('import', {'name': 'Gates', 'type': 'Gate', 'rows': [{'id': 'G0'}, {'id': 'G1', 'requires': 'G0'}]})
        gate = self.record_action('Gate result', 'Gate', criteria=[{'kind': 'requires_latest_evidence', 'target_from_property': 'requires', 'target_type': 'Gate',
                                                                  'evidence_kind': 'inspection', 'param': 'result', 'equals': 'PASS'}])
        with self.assertRaises(ValueError):
            self.call('run', {'action': gate['id'], 'id': 'G1', 'params': {'result': 'PASS'}})
        self.call('run', {'action': gate['id'], 'id': 'G0', 'params': {'result': 'PASS'}})
        self.call('run', {'action': gate['id'], 'id': 'G1', 'params': {'result': 'PASS'}})
        with server.connect() as db:
            self.assertEqual(server.evidence_for(db, 'Gate', 'G1', 'inspection')[0]['result'], 'PASS')


if __name__ == '__main__':
    unittest.main()
