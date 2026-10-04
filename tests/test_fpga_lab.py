import importlib.util
import tempfile
import unittest
from pathlib import Path

import server

spec = importlib.util.spec_from_file_location('fpga_lab', Path(__file__).resolve().parents[1] / 'examples' / 'fpga_lab' / 'load.py')
fpga_lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fpga_lab)


class FpgaLabExampleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = server.DB
        fpga_lab.stats.clear()
        self.counts, self.links = fpga_lab.run(Path(self.temp.name) / 'lab.db')

    def tearDown(self):
        server.DB = self.old_db
        self.temp.cleanup()

    def call(self, path, data):
        with server.connect() as db:
            return server.mutate(db, '/api/' + path, data)

    def action(self, name):
        with server.connect() as db:
            return next(a for a in server.state(db)['actions'] if a['name'] == name)

    def latest(self, type_name, object_id, kind):
        with server.connect() as db:
            return server.evidence_for(db, type_name, object_id, kind)

    def test_loads_and_is_idempotent(self):
        self.assertEqual(self.counts['Board'], 3)
        self.assertEqual(self.counts['Build'], 4)
        self.assertEqual(self.counts['Gate'], 5)
        self.assertEqual(self.counts[server.EVIDENCE_TYPE], 0)
        self.assertEqual(fpga_lab.stats['actions'], 6)
        first_links = fpga_lab.stats['links']
        self.assertEqual(first_links, self.links)
        fpga_lab.stats.clear()
        counts, links = fpga_lab.run(server.DB)
        self.assertEqual((counts, links), (self.counts, self.links))
        self.assertEqual(fpga_lab.stats['links'], 0)

    def test_every_object_is_linked(self):
        with server.connect() as db:
            orphans = db.execute('''SELECT type, id FROM objects o WHERE NOT EXISTS (
                SELECT 1 FROM links l WHERE (l.from_type=o.type AND l.from_id=o.id) OR (l.to_type=o.type AND l.to_id=o.id))''').fetchall()
        self.assertEqual([tuple(row) for row in orphans], [])

    def test_gate_chain_and_power_cap(self):
        gate = self.action('Record gate result')
        with self.assertRaises(ValueError):
            self.call('run', {'action': gate['id'], 'id': 'G1', 'params': {'result': 'PASSED', 'evidence_uri': 'file:///g1', 'recorded_by': 't'}})
        self.call('run', {'action': gate['id'], 'id': 'G0', 'params': {'result': 'PASSED', 'evidence_uri': 'file:///g0', 'recorded_by': 't'}})
        self.call('run', {'action': gate['id'], 'id': 'G1', 'params': {'result': 'PASSED', 'evidence_uri': 'file:///g1', 'build': 'B-0002', 'recorded_by': 't'}})
        self.assertEqual(self.latest('Gate', 'G1', 'gate')[0]['result'], 'PASSED')
        power = self.action('Record power measurement')
        with self.assertRaises(ValueError):
            self.call('run', {'action': power['id'], 'id': 'U55C-01', 'params': {'watts': 160, 'recorded_by': 't'}})
        self.call('run', {'action': power['id'], 'id': 'U55C-01', 'params': {'watts': 121.5, 'at_clock_mhz': 300, 'build': 'B-0002', 'recorded_by': 't'}})
        self.assertEqual(self.latest('Board', 'U55C-01', 'power')[0]['watts'], 121.5)

    def test_step_control_and_release_hold(self):
        control = self.action('Bring-up step control')
        with self.assertRaises(ValueError):
            self.call('run', {'action': control['id'], 'id': 'S-01', 'params': {'decision': 'ROLL BACK', 'observation': 'x', 'recorded_by': 't'}})
        self.call('run', {'action': control['id'], 'id': 'S-01', 'params': {'decision': 'PROCEED', 'observation': 'Card enumerated.', 'recorded_by': 't'}})
        with self.assertRaises(ValueError):
            self.call('run', {'action': control['id'], 'id': 'S-01', 'params': {'decision': 'PROCEED', 'observation': 'again', 'recorded_by': 't'}})
        release = self.action('Set release-hold status')
        with self.assertRaises(ValueError):
            self.call('run', {'action': release['id'], 'id': 'R-TIMING', 'params': {'status': 'RELEASED', 'evidence_uri': 'file:///x', 'recorded_by': 't'}})
        with self.assertRaises(ValueError):
            self.call('run', {'action': release['id'], 'id': 'R-POWER-U55C', 'params': {'status': 'RELEASED', 'recorded_by': 't'}})
        self.call('run', {'action': release['id'], 'id': 'R-POWER-U55C', 'params': {'status': 'RELEASED', 'evidence_uri': 'file:///power.csv', 'recorded_by': 't'}})
        self.assertEqual(self.latest('Requirement', 'R-POWER-U55C', 'release')[0]['status'], 'RELEASED')


if __name__ == '__main__':
    unittest.main()
