#!/usr/bin/env python3
"""Loop Planner state API: one JSON document, shared by every browser on the LAN.

Owns state/state.json (characters, skills, typed prices, observed DE log, checklists,
packs, UI prefs). The page loads it on open and writes it on every change; when the API
is unreachable the page falls back to the browser's localStorage and shows "offline".

Endpoints (all under /api/ so nginx can proxy one prefix):
  GET  /api/health           {ok, rev, updatedAt, observed, bytes}
  GET  /api/state            the document; 204 (no content) until the first PUT
  PUT  /api/state            replace the document (last write wins), except `observed`,
                             which is unioned with what is stored so a tap logged on one
                             device is never clobbered by a save from another
  POST /api/observed         append one entry or a list; deduped on (lvl,slot,quality,mat,qty,ts)
  DELETE /api/observed       remove one entry (undo) or everything with {"all": true} (reset)

Every write stamps `rev` (monotonic int) and `updatedAt` (UTC ISO) on the document and
returns both; PUT also returns the merged `observed` list so the writer adopts other devices'
taps. A DELETE leaves a tombstone (`obsDeleted`, pruned after 7 days) so a device still
holding the entry cannot re-add it with its next PUT. Writes are atomic (tmp file + os.replace) and serialised by a lock.

Run under Docker (see docker-compose.yml) or by hand:
  python backend/state_api.py --dir state --port 8088
Dev mode, no nginx: also serve index.html and data/ from the repo root on the same port:
  python backend/state_api.py --static . --port 8088    # then open http://localhost:8088/

Stdlib only. No auth: LAN only, one user. CORS is wide open so the iPad can hit the API
on a different host:port if you ever serve the page from somewhere else.
"""
import argparse, json, os, sys, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MAX_BODY = 8 * 1024 * 1024
TOMB_DAYS = 7          # how long a deleted observed entry stays blocked from re-merging
OBS_KEYS = ('lvl', 'slot', 'quality', 'mat', 'qty', 'ts')


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def obs_key(o):
    return '|'.join(str(o.get(k, '')) for k in OBS_KEYS)


class Store:
    """state.json on disk, one document, read-modify-write under a lock."""

    def __init__(self, directory):
        self.dir = os.path.abspath(directory)
        self.path = os.path.join(self.dir, 'state.json')
        self.lock = threading.Lock()
        os.makedirs(self.dir, exist_ok=True)

    def _read(self):
        if not os.path.exists(self.path):
            return None
        with open(self.path, encoding='utf-8') as f:
            return json.load(f)

    def _write(self, doc):
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(doc, f, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def get(self):
        with self.lock:
            return self._read()

    @staticmethod
    def _tombstones(*docs):
        """Keys of deleted observed entries, unioned across documents and pruned after TOMB_DAYS."""
        cutoff = int(time.time() * 1000) - TOMB_DAYS * 86400000
        out = {}
        for d in docs:
            for k, ts in (d.get('obsDeleted') or {}).items():
                if isinstance(ts, (int, float)) and ts >= cutoff:
                    out[k] = max(out.get(k, 0), int(ts))
        return out

    def put(self, incoming):
        if not isinstance(incoming, dict):
            raise ValueError('state must be a JSON object')
        with self.lock:
            stored = self._read() or {}
            doc = dict(incoming)
            # union the observed log so a tap on one device survives a save from another;
            # tombstones keep an undo from being resurrected by a device that still holds the entry
            tomb = self._tombstones(stored, incoming)
            merged, seen = [], set()
            for o in list(stored.get('observed') or []) + list(incoming.get('observed') or []):
                if not isinstance(o, dict):
                    continue
                k = obs_key(o)
                if k in seen or k in tomb:
                    continue
                seen.add(k)
                merged.append(o)
            merged.sort(key=lambda o: (o.get('ts') or 0))
            doc['observed'] = merged
            doc['obsDeleted'] = tomb
            doc['rev'] = int(stored.get('rev') or 0) + 1
            doc['updatedAt'] = now_iso()
            self._write(doc)
            # the merged log goes back so the writer can adopt what other devices added or removed
            return {'rev': doc['rev'], 'updatedAt': doc['updatedAt'], 'observed': merged, 'obsDeleted': tomb}

    def append_observed(self, entries):
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            raise ValueError('observed must be an object or a list')
        for o in entries:
            if not isinstance(o, dict) or 'lvl' not in o or 'mat' not in o:
                raise ValueError('each entry needs at least lvl and mat')
            o.setdefault('slot', 'armor')
            o.setdefault('quality', 'green')
            o.setdefault('qty', 1)
            o.setdefault('ts', int(time.time() * 1000))
        with self.lock:
            doc = self._read() or {}
            obs = list(doc.get('observed') or [])
            seen = {obs_key(o) for o in obs if isinstance(o, dict)}
            tomb = self._tombstones(doc)
            added = 0
            for o in entries:
                k = obs_key(o)
                if k in seen:
                    continue
                seen.add(k)
                tomb.pop(k, None)   # re-logging an undone entry is a deliberate re-add
                obs.append(o)
                added += 1
            doc['observed'] = obs
            doc['obsDeleted'] = tomb
            doc['rev'] = int(doc.get('rev') or 0) + 1
            doc['updatedAt'] = now_iso()
            self._write(doc)
            return {'rev': doc['rev'], 'updatedAt': doc['updatedAt'], 'added': added, 'observed': len(obs)}

    def remove_observed(self, body):
        """Undo: body is one entry (removed by key) or {"all": true} (clears the log, for Reset)."""
        if not isinstance(body, dict):
            raise ValueError('body must be an entry or {"all": true}')
        with self.lock:
            doc = self._read() or {}
            obs = list(doc.get('observed') or [])
            tomb = self._tombstones(doc)
            now_ms = int(time.time() * 1000)
            if body.get('all') is True:
                for o in obs:
                    if isinstance(o, dict):
                        tomb[obs_key(o)] = now_ms
                removed, obs = len(obs), []
            else:
                k = obs_key(body)
                keep = [o for o in obs if not (isinstance(o, dict) and obs_key(o) == k)]
                removed, obs = len(obs) - len(keep), keep
                tomb[k] = now_ms
            doc['observed'] = obs
            doc['obsDeleted'] = tomb
            doc['rev'] = int(doc.get('rev') or 0) + 1
            doc['updatedAt'] = now_iso()
            self._write(doc)
            return {'rev': doc['rev'], 'updatedAt': doc['updatedAt'], 'removed': removed, 'observed': len(obs)}

    def health(self):
        with self.lock:
            doc = self._read()
            size = os.path.getsize(self.path) if os.path.exists(self.path) else 0
        return {'ok': True, 'rev': (doc or {}).get('rev', 0), 'updatedAt': (doc or {}).get('updatedAt'),
                'observed': len((doc or {}).get('observed') or []), 'bytes': size}


STATIC_TYPES = {'.html': 'text/html; charset=utf-8', '.json': 'application/json', '.css': 'text/css',
                '.js': 'text/javascript', '.md': 'text/plain; charset=utf-8', '.txt': 'text/plain; charset=utf-8'}


def make_handler(store, static_dir):
    class H(BaseHTTPRequestHandler):
        server_version = 'loop-planner-state/0.1'
        protocol_version = 'HTTP/1.1'

        # ---- helpers ----
        def _cors(self):
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, PUT, POST, DELETE, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')

        def _json(self, code, obj):
            body = json.dumps(obj, separators=(',', ':')).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self._cors()
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get('Content-Length') or 0)
            if n > MAX_BODY:
                raise ValueError(f'body over {MAX_BODY} bytes')
            raw = self.rfile.read(n) if n else b''
            if not raw:
                raise ValueError('empty body')
            return json.loads(raw.decode('utf-8'))

        def _static(self, path):
            if static_dir is None:
                return self._json(404, {'error': 'not found'})
            if path in ('/', '/index.html'):
                rel = 'index.html'
            elif path.startswith('/data/') and not path.startswith('/data/raw/'):
                rel = path.lstrip('/')
            else:
                return self._json(404, {'error': 'not found'})
            full = os.path.realpath(os.path.join(static_dir, rel))
            if not full.startswith(static_dir + os.sep):
                return self._json(404, {'error': 'not found'})
            if not os.path.isfile(full):
                if rel.startswith('data/'):   # same as nginx: a data file that isn't there yet is 204, not an error
                    self.send_response(204); self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', '0'); self.end_headers(); return
                return self._json(404, {'error': 'not found'})
            with open(full, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', STATIC_TYPES.get(os.path.splitext(full)[1], 'application/octet-stream'))
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store' if rel.startswith('data/') else 'no-cache')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        # ---- verbs ----
        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/api/health':
                return self._json(200, store.health())
            if path == '/api/state':
                doc = store.get()
                if doc is not None:
                    return self._json(200, doc)
                self.send_response(204); self._cors(); self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', '0'); self.end_headers(); return
            if path.startswith('/api/'):
                return self._json(404, {'error': 'not found'})
            return self._static(path)

        do_HEAD = do_GET

        def do_PUT(self):
            path = urlsplit(self.path).path
            if path != '/api/state':
                return self._json(404, {'error': 'not found'})
            try:
                return self._json(200, store.put(self._body()))
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(400, {'error': str(e)})

        def do_POST(self):
            path = urlsplit(self.path).path
            if path != '/api/observed':
                return self._json(404, {'error': 'not found'})
            try:
                return self._json(200, store.append_observed(self._body()))
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(400, {'error': str(e)})

        def do_DELETE(self):
            path = urlsplit(self.path).path
            if path != '/api/observed':
                return self._json(404, {'error': 'not found'})
            try:
                return self._json(200, store.remove_observed(self._body()))
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(400, {'error': str(e)})

        def log_message(self, fmt, *args):
            sys.stdout.write('%s %s\n' % (self.address_string(), fmt % args))
            sys.stdout.flush()

    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', default=os.environ.get('STATE_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'state')),
                    help='directory holding state.json (env STATE_DIR)')
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8088')))
    ap.add_argument('--bind', default=os.environ.get('BIND', '0.0.0.0'))
    ap.add_argument('--static', metavar='DIR', help='dev mode: also serve index.html and data/ from DIR')
    a = ap.parse_args()
    store = Store(a.dir)
    static_dir = os.path.realpath(a.static) if a.static else None
    srv = ThreadingHTTPServer((a.bind, a.port), make_handler(store, static_dir))
    print(f'state API on http://{a.bind}:{a.port}/api/  state file {store.path}'
          + (f'  static from {static_dir}' if static_dir else ''), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
