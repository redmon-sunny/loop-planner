#!/usr/bin/env python3
"""Tests for state_api.py against a live server on a random port. Stdlib only.

  python backend/test_state_api.py
"""
import json, os, shutil, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))


def call(url, method='GET', body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b'null'), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'null'), dict(e.headers)


class StateApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='lp-state-')
        cls.static = tempfile.mkdtemp(prefix='lp-static-')
        with open(os.path.join(cls.static, 'index.html'), 'w') as f:
            f.write('<!doctype html><title>t</title>ok')
        os.makedirs(os.path.join(cls.static, 'data', 'raw'))
        with open(os.path.join(cls.static, 'data', 'prices.json'), 'w') as f:
            f.write('{"prices":{}}')
        with open(os.path.join(cls.static, 'data', 'raw', 'secret.json'), 'w') as f:
            f.write('{"key":"nope"}')
        import socket
        s = socket.socket(); s.bind(('127.0.0.1', 0)); cls.port = s.getsockname()[1]; s.close()
        cls.proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'state_api.py'), '--dir', cls.tmp,
                                     '--bind', '127.0.0.1', '--port', str(cls.port), '--static', cls.static],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.base = f'http://127.0.0.1:{cls.port}'
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.base + '/api/health', timeout=1); break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError('server did not start: ' + cls.proc.stderr.read().decode())

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(5)
        shutil.rmtree(cls.tmp, ignore_errors=True); shutil.rmtree(cls.static, ignore_errors=True)

    def test_01_empty_then_put(self):
        code, body, _ = call(self.base + '/api/state')
        self.assertEqual(code, 204, 'no state yet is no content, not an error')
        code, body, _ = call(self.base + '/api/state', 'PUT', {'chars': {'Main': {'tail': 5, 'ench': 3}}, 'observed': []})
        self.assertEqual(code, 200); self.assertEqual(body['rev'], 1); self.assertIn('updatedAt', body)
        code, doc, hdr = call(self.base + '/api/state')
        self.assertEqual(code, 200); self.assertEqual(doc['chars']['Main']['tail'], 5); self.assertEqual(doc['rev'], 1)
        self.assertEqual(hdr.get('Cache-Control'), 'no-store')
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'state.json')))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'state.json.tmp')))

    def test_02_observed_union_on_put(self):
        # device A logs a tap via POST; device B saves a whole doc that doesn't have it
        code, body, _ = call(self.base + '/api/observed', 'POST', {'lvl': 16, 'slot': 'armor', 'quality': 'green', 'mat': 'strange_dust', 'qty': 4, 'ts': 1000})
        self.assertEqual(code, 200); self.assertEqual(body['added'], 1)
        code, body, _ = call(self.base + '/api/observed', 'POST', {'lvl': 16, 'slot': 'armor', 'quality': 'green', 'mat': 'strange_dust', 'qty': 4, 'ts': 1000})
        self.assertEqual(body['added'], 0, 'duplicate must be ignored')
        code, body, _ = call(self.base + '/api/state', 'PUT', {'chars': {}, 'observed': [{'lvl': 20, 'slot': 'weapon', 'quality': 'green', 'mat': 'lae', 'qty': 1, 'ts': 2000}]})
        self.assertEqual(code, 200); self.assertEqual(len(body['observed']), 2, 'PUT returns the merged log')
        _, doc, _ = call(self.base + '/api/state')
        self.assertEqual([o['ts'] for o in doc['observed']], [1000, 2000])
        self.assertGreater(doc['rev'], 1)

    def test_03_post_defaults_and_list(self):
        code, body, _ = call(self.base + '/api/observed', 'POST', [{'lvl': 12, 'mat': 'strange_dust'}, {'lvl': 12, 'mat': 'gme', 'qty': 2}])
        self.assertEqual(code, 200); self.assertEqual(body['added'], 2)
        _, doc, _ = call(self.base + '/api/state')
        last = doc['observed'][-1]
        self.assertEqual(last['slot'], 'armor'); self.assertEqual(last['quality'], 'green'); self.assertIn('ts', last)

    def test_04_bad_input(self):
        code, body, _ = call(self.base + '/api/state', 'PUT', [1, 2, 3])
        self.assertEqual(code, 400)
        code, body, _ = call(self.base + '/api/observed', 'POST', {'nope': 1})
        self.assertEqual(code, 400)
        req = urllib.request.Request(self.base + '/api/state', data=b'{not json', method='PUT', headers={'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 400)
        code, _, _ = call(self.base + '/api/nothing')
        self.assertEqual(code, 404)

    def test_05_health_and_cors(self):
        code, body, hdr = call(self.base + '/api/health')
        self.assertEqual(code, 200); self.assertTrue(body['ok']); self.assertGreater(body['rev'], 0)
        self.assertEqual(hdr.get('Access-Control-Allow-Origin'), '*')
        req = urllib.request.Request(self.base + '/api/state', method='OPTIONS')
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 204)

    def test_06_static_dev_mode(self):
        with urllib.request.urlopen(self.base + '/', timeout=5) as r:
            self.assertEqual(r.status, 200); self.assertIn(b'ok', r.read()); self.assertIn('text/html', r.headers['Content-Type'])
        with urllib.request.urlopen(self.base + '/data/prices.json', timeout=5) as r:
            self.assertEqual(r.headers['Cache-Control'], 'no-store')
        with urllib.request.urlopen(self.base + '/data/recipes.json', timeout=5) as r:
            self.assertEqual(r.status, 204, 'a missing data file is no content, not an error')
        for bad in ('/data/raw/secret.json', '/../etc/passwd', '/data/../../etc/passwd', '/backend/state_api.py'):
            code, _, _ = call(self.base + bad)
            self.assertEqual(code, 404, bad)

    def test_06b_delete_observed(self):
        e = {'lvl': 30, 'slot': 'armor', 'quality': 'green', 'mat': 'soul_dust', 'qty': 3, 'ts': 3000}
        call(self.base + '/api/observed', 'POST', e)
        _, before, _ = call(self.base + '/api/health')
        code, body, _ = call(self.base + '/api/observed', 'DELETE', e)
        self.assertEqual(code, 200); self.assertEqual(body['removed'], 1); self.assertEqual(body['observed'], before['observed'] - 1)
        code, body, _ = call(self.base + '/api/observed', 'DELETE', e)
        self.assertEqual(body['removed'], 0)
        code, body, _ = call(self.base + '/api/observed', 'DELETE', [1])
        self.assertEqual(code, 400)
        # a stale device still holding the entry must not resurrect it
        code, body, _ = call(self.base + '/api/state', 'PUT', {'chars': {}, 'observed': [e]})
        self.assertEqual(code, 200); self.assertNotIn(3000, [o['ts'] for o in body['observed']], 'tombstone blocks re-merge')
        self.assertIn('30|armor|green|soul_dust|3|3000', body['obsDeleted'])
        # logging it again on purpose clears the tombstone
        code, body, _ = call(self.base + '/api/observed', 'POST', e)
        self.assertEqual(body['added'], 1)
        _, doc, _ = call(self.base + '/api/state')
        self.assertNotIn('30|armor|green|soul_dust|3|3000', doc['obsDeleted'])
        call(self.base + '/api/observed', 'DELETE', e)

    def test_07_concurrent_puts_keep_rev_monotonic(self):
        import threading
        results = []
        def w(i):
            results.append(call(self.base + '/api/state', 'PUT', {'n': i, 'observed': []})[1]['rev'])
        ts = [threading.Thread(target=w, args=(i,)) for i in range(20)]
        for t in ts: t.start()
        for t in ts: t.join()
        self.assertEqual(len(set(results)), 20, 'every write must get its own rev')
        _, doc, _ = call(self.base + '/api/state')
        self.assertEqual(doc['rev'], max(results))
        self.assertEqual(len(doc['observed']), 4, 'observed survives every whole-document PUT')
        self.assertEqual(doc['obsDeleted'], {'30|armor|green|soul_dust|3|3000': doc['obsDeleted']['30|armor|green|soul_dust|3|3000']})

    def test_08_delete_all(self):
        code, body, _ = call(self.base + '/api/observed', 'DELETE', {'all': True})
        self.assertEqual(code, 200); self.assertEqual(body['observed'], 0)
        _, doc, _ = call(self.base + '/api/state')
        self.assertEqual(doc['observed'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
