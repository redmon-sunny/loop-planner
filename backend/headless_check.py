#!/usr/bin/env python3
"""Headless browser check for the page + state API (dev-only; needs `pip install playwright` and
`playwright install chromium`; the runtime stays stdlib). Run from the repo root after ./build.sh:

  python backend/headless_check.py            # screenshots go to /tmp/lp-shots (or --shots DIR)

What it proves (acceptance 1, 4, 7 of the brief):
  - the page boots with no console errors at 400 / 820 / 1366 px, both themes, both modes, no horizontal scroll
  - two browsers (separate localStorage) share one document through the API within one reload
  - a stale browser's save gets 409, merges, and keeps the other browser's edit (no clobber)
  - a DE tap is one POST (rev +1, nothing else in the document changes) and shows in the other's ledger
  - when the API goes away the badge says offline, edits stay local, and they are pushed when it returns
  - opened from file:// the page runs on localStorage alone and says so
"""
import argparse, json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.realpath(os.path.join(HERE, '..'))


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


def start_api(state_dir, port):
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'state_api.py'), '--dir', state_dir, '--bind', '127.0.0.1',
                             '--port', str(port), '--static', ROOT], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(50):
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=1); return proc
        except Exception:
            time.sleep(0.1)
    raise RuntimeError('api did not start')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shots', default='/tmp/lp-shots')
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    os.makedirs(a.shots, exist_ok=True)
    if not os.path.exists(os.path.join(ROOT, 'index.html')):
        subprocess.check_call(['sh', 'build.sh'], cwd=ROOT)
    state_dir = tempfile.mkdtemp(prefix='lp-hc-')
    port = free_port(); base = f'http://127.0.0.1:{port}/'
    api = start_api(state_dir, port)
    fails = []
    errors = {}

    def watch(page, name):
        errors[name] = []
        page.on('console', lambda m: errors[name].append(m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: errors[name].append(str(e)))

    def check(cond, msg):
        print(('  ok   ' if cond else '  FAIL ') + msg)
        if not cond:
            fails.append(msg)

    def badge(page):
        return page.locator('#syncBadge').inner_text()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            # ---- A: phone width, fresh server gets seeded ----
            ctxA = browser.new_context(viewport={'width': 400, 'height': 800}, has_touch=True)
            A = ctxA.new_page(); watch(A, 'A')
            A.goto(base); A.wait_for_function("document.querySelector('#syncBadge').textContent.startsWith('synced')", timeout=5000)
            print('A at 400px:', badge(A))
            check(badge(A) == 'synced · rev 1', 'fresh server seeded from the first browser (rev 1)')
            check(not A.locator('#session').is_hidden(), 'session mode opens by default on a narrow touch screen')
            # ---- B: desktop, separate storage, loads the same rev 1 ----
            ctxB = browser.new_context(viewport={'width': 1366, 'height': 900})
            B = ctxB.new_page(); watch(B, 'B')
            B.goto(base); B.wait_for_function("document.querySelector('#syncBadge').textContent.startsWith('synced')", timeout=5000)
            check(not B.locator('#plan').is_hidden(), 'plan mode opens by default on desktop')
            # A edits Enchanting -> rev 2. B still holds rev 1.
            A.fill('#skEnch', '60'); A.dispatch_event('#skEnch', 'input')
            A.wait_for_function("document.querySelector('#syncBadge').textContent==='synced · rev 2'", timeout=5000)
            check(badge(A) == 'synced · rev 2', 'typing a skill pushes a new rev')
            doc = json.load(urllib.request.urlopen(base + 'api/state'))
            check(doc['chars'][doc['activeChar']]['ench'] == 60, 'server holds the typed skill')
            check('mode' not in doc['ui'] and 'theme' not in doc['ui'], 'mode and theme stay out of the shared doc')
            # the clobber case: stale B edits Tailoring. Its PUT must 409, merge, and keep A's 72.
            B.fill('#skTail', '101'); B.dispatch_event('#skTail', 'input')
            B.wait_for_function("document.querySelector('#syncBadge').textContent==='synced · rev 3'", timeout=5000)
            doc = json.load(urllib.request.urlopen(base + 'api/state'))
            check(doc['chars']['Main'] == {'tail': 101, 'ench': 60}, 'stale save 409s, merges, keeps the other device\'s edit (rev 3)')
            check(B.input_value('#skEnch') == '60', 'B adopted A\'s edit during the merge')
            # B logs a DE tap: one POST, rev +1, nothing else in the document moves
            B.click('#modeSession'); B.click('#sQty button[data-q="4"]')
            B.wait_for_function("document.querySelector('#syncBadge').textContent==='synced · rev 4'", timeout=5000)
            doc = json.load(urllib.request.urlopen(base + 'api/state'))
            check(doc['rev'] == 4 and len(doc['observed']) == 1 and doc['observed'][0]['qty'] == 4, 'DE tap is a single POST (rev 4, one entry)')
            check(doc['chars']['Main'] == {'tail': 101, 'ench': 60}, 'the tap carried no document PUT')
            # A pulls it on its next health poll (simulate the 15 s timer)
            A.evaluate('pollHealth()'); A.wait_for_function("S.observed.length===1", timeout=5000)
            A.click('#modePlan'); A.click('nav.tabs button[data-tab="observed"]')
            check('1' in A.locator('#obsSummary').inner_text(), 'tap logged on B appears in A\'s Observed ledger without a reload')
            # undo on B removes it everywhere
            B.click('#sUndo'); B.wait_for_function("S.observed.length===0", timeout=5000); time.sleep(0.8)
            doc = json.load(urllib.request.urlopen(base + 'api/state'))
            check(len(doc['observed']) == 0, 'undo deletes the entry on the server (PUT does not resurrect it)')

            # ---- layout: three widths, two themes, both modes, no horizontal scroll ----
            for w in (400, 820, 1366):
                for theme in ('dark', 'light'):
                    for mode in ('session', 'plan'):
                        B.set_viewport_size({'width': w, 'height': 900})
                        B.select_option('#theme', theme); B.click('#mode' + mode.capitalize())
                        if mode == 'plan':
                            B.click('nav.tabs button[data-tab="tailoring"]')
                        B.wait_for_timeout(150)
                        sw, iw = B.evaluate('[document.documentElement.scrollWidth, window.innerWidth]')
                        check(sw <= iw, f'no horizontal scroll at {w}px {theme} {mode} ({sw}<={iw})')
                        B.screenshot(path=os.path.join(a.shots, f'{mode}-{theme}-{w}.png'), full_page=(w != 1366))
            B.select_option('#theme', 'auto'); B.set_viewport_size({'width': 1366, 'height': 900})

            # ---- offline: kill the API, B edits and taps, A edits once it is back, then B reconnects ----
            api.terminate(); api.wait(5)
            B.click('#modePlan'); B.fill('#skTail', '102'); B.dispatch_event('#skTail', 'input')
            B.wait_for_function("document.querySelector('#syncBadge').textContent==='offline'", timeout=8000)
            check(badge(B) == 'offline', 'badge flips to offline when the API is down')
            B.click('#modeSession'); B.click('#sQty button[data-q="2"]'); B.wait_for_timeout(300)
            check(B.evaluate("JSON.parse(localStorage.getItem('wowf-loop-planner-v2')).chars.Main.tail") == 102, 'offline edit kept in localStorage')
            check(B.evaluate("S.observed.length") == 1, 'offline tap kept locally')
            api = start_api(state_dir, port)
            A.evaluate('pollHealth()'); A.wait_for_function("document.querySelector('#syncBadge').textContent.startsWith('synced')", timeout=8000)
            A.fill('#skEnch', '80'); A.dispatch_event('#skEnch', 'input')
            A.wait_for_function("document.querySelector('#syncBadge').textContent.startsWith('synced') && !sync.pending", timeout=8000)
            B.evaluate('pollHealth()'); B.wait_for_function("document.querySelector('#syncBadge').textContent.startsWith('synced') && !sync.pending && !sync.inFlight", timeout=8000)
            time.sleep(0.5)
            doc = json.load(urllib.request.urlopen(base + 'api/state'))
            check(doc['chars']['Main'] == {'tail': 102, 'ench': 80}, 'reconnect merges: B\'s offline edit and A\'s meanwhile edit both land')
            check(len(doc['observed']) == 1 and doc['observed'][0]['qty'] == 2, 'offline tap pushed on reconnect')
            check(B.input_value('#skEnch') == '80', 'B shows A\'s meanwhile edit')
            # a reload in the other browser sees it (acceptance #1: same state within one reload)
            A.reload(); A.wait_for_function("document.querySelector('#syncBadge').textContent.startsWith('synced')", timeout=5000)
            check(A.input_value('#skTail') == '102', 'other browser sees the change after one reload')

            # ---- file:// still works, on localStorage alone ----
            ctxF = browser.new_context(viewport={'width': 1366, 'height': 900})
            F = ctxF.new_page(); watch(F, 'file')
            F.goto('file://' + os.path.join(ROOT, 'index.html')); F.wait_for_timeout(400)
            check(badge(F) == 'local file', 'file:// shows the local-file badge')
            check(not F.locator('#plan').is_hidden(), 'file:// page renders')
            browser.close()
    finally:
        api.terminate(); api.wait(5); shutil.rmtree(state_dir, ignore_errors=True)

    for name, errs in errors.items():
        # a 409 is logged by Chromium as a failed resource; it is the conflict path doing its job, not a page error
        real = [e for e in errs if 'fonts.g' not in e and 'ERR_' not in e and '409' not in e]
        check(not real, f'no console errors in {name}' + (f': {real[:3]}' if real else ''))
    print(f'\nscreenshots in {a.shots}')
    print('\nRESULT:', 'PASS' if not fails else f'FAIL ({len(fails)})')
    for f in fails:
        print('  -', f)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
