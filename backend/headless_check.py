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
  - a recipes.json built offline from the stage-2 fixtures (test_fdb_seed.synthetic_recipes) loads: footer shows the
    build, the ForeverDB recipes and the item-level DE table replace the seed, Forever-observed rows are labelled
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
    # stage-2 data file, written beside the page for the duration of the check only
    import test_fdb_seed
    data_dir = os.path.join(ROOT, 'data'); os.makedirs(data_dir, exist_ok=True)
    rec_path = os.path.join(data_dir, 'recipes.json'); had_rec = os.path.exists(rec_path)
    if had_rec:
        os.replace(rec_path, rec_path + '.bak')
    with open(rec_path, 'w', encoding='utf-8') as f:
        json.dump(test_fdb_seed.synthetic_recipes(), f)

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

            # ---- stage 2: recipes.json (synthetic, real shapes) replaced the seed tables ----
            check('build 1.60.1.70658' in B.locator('#footer').inner_text() and 'client dev-0077' in B.locator('#footer').inner_text(), 'footer carries the ForeverDB build and client from recipes.json')
            check('Forever-observed' in B.locator('#dataStatus').inner_text(), 'header status names the DE rows')
            B.click('#modePlan'); B.click('nav.tabs button[data-tab="tailoring"]'); B.check('#tailShowAll')
            tail = B.locator('#tailTable').inner_text()
            check('Woolen Cape' in tail and 'Bolt of Linen Cloth' in tail and 'Brown Linen Robe' not in tail, 'ForeverDB recipes replaced the seed tailoring table')
            B.click('nav.tabs button[data-tab="shuffle"]'); B.fill('#shLevel', '22'); B.select_option('#shSlot', 'weapon'); B.dispatch_event('#shLevel', 'input')
            out = B.locator('#shuffleOut').inner_text()
            check('21–25 weapon green' in out and 'Forever (40 DEs)' in out and 'Lesser Astral Essence 73%' in out, 'Shuffle uses the item-level DE row and labels the Forever-observed source')
            B.select_option('#shSlot', 'armor'); B.dispatch_event('#shLevel', 'input')
            out = B.locator('#shuffleOut').inner_text()
            check('Strange Dust 75% ×4–6' in out and 'Classic (C)' in out, 'armor 21–25 row is the Classic table, flagged')
            check(B.evaluate("S.de.length") == 3 and B.evaluate("S.ui.loadedDeBuild") == '1.60.1.70658', 'DE table replaced once per build')
            check(B.evaluate("S.mats.find(m=>m.id==='mote').itemId") == 247786 and B.evaluate("S.mats.find(m=>m.id==='mote').vendor") == 20, 'Mote of Magic got its Forever id and vendor price')
            B.click('#modeSession'); B.fill('#sLvl', '22'); B.dispatch_event('#sLvl', 'input'); B.click('#sSlot button[data-v="weapon"]')
            check('ilvl 21–25 weapon green' in B.locator('#sBracket').inner_text() and B.locator('#sMats button').first.inner_text() == 'Lesser Astral Essence', 'DE logger offers the row\'s mats for that item level and slot')
            B.click('#sSlot button[data-v="armor"]')

            # ---- stage 2c: review items B1, B2, B4, B5, B8 on the seed tables (file:// has no recipes.json) ----
            ctxR = browser.new_context(viewport={'width': 1366, 'height': 900})
            R = ctxR.new_page(); watch(R, 'review')
            R.goto('file://' + os.path.join(ROOT, 'index.html')); R.wait_for_timeout(400)
            # B1: Tailoring 60 / Enchanting 1 on seed data used to pick Novice Arcanist's Sash (BoP, unpriced potions)
            R.fill('#skTail', '60'); R.dispatch_event('#skTail', 'input'); R.fill('#skEnch', '1'); R.dispatch_event('#skEnch', 'input')
            R.click('#modeSession'); stop = R.locator('#stopOut').inner_text()
            check("Arcanist" not in stop.split('skipped')[0] and 'Linen Bag' in stop, 'B1: Craft now skips the BoP/unpriced sash and names Linen Bag')
            # make the sash the cheapest thing on paper (its potions priced, linen nearly free): it must still be skipped for BoP, and say so
            R.evaluate("S.prices.minor_mana_pot=1; S.prices.linen=1; S.mats.find(m=>m.id==='coarse_thread').vendor=5000; S.mats.find(m=>m.id==='fine_thread').vendor=1; renderAll()"); stop = R.locator('#stopOut').inner_text()
            check('Arcanist' not in stop.split('skipped')[0] and 'skipped:' in stop and 'Arcanist' in stop.split('skipped')[1] and 'BoP' in stop, 'B1: a BoP recipe that would win on net/pt is skipped and the reason shown')
            R.evaluate("delete S.prices.minor_mana_pot; S.prices.linen=300; S.mats.find(m=>m.id==='coarse_thread').vendor=10; S.mats.find(m=>m.id==='fine_thread').vendor=100; renderAll()")
            # B2: a custom recipe with an unknown mat must not look free or become Craft now
            R.evaluate("S.tailoring.push({id:'ghost',name:'Ghost Robe',skill:[1,200,250,300],inputs:[{mat:'nonexistent',qty:1}],output:{quality:'green',reqLevel:5,itemLevel:10,slot:'armor'},sell:0,custom:true}); renderAll()")
            stop = R.locator('#stopOut').inner_text()
            check('Ghost Robe' not in stop.split('skipped')[0], 'B2: unknown-mat recipe is not Craft now')
            check(R.evaluate("recipeUnpriced(S.tailoring.find(r=>r.id==='ghost')).length") == 1, 'B2: unknown mat id is reported as unpriced')
            R.click('#modePlan'); R.click('nav.tabs button[data-tab="plan"]'); R.fill('#planTailFrom', '1'); R.fill('#planTailTo', '300'); R.click('#runPlan')
            check('Ghost Robe' not in R.locator('#planOut').inner_text(), 'B2: solver never picks a recipe with an unknown mat')
            R.evaluate("S.tailoring.pop(); renderAll()")
            # B4 / B5
            check(R.evaluate("[fmt(Infinity), fmt(NaN), fmtShort(Infinity), fmtShort(NaN), fmt(-150)]") == ['—', '—', '—', '—', '−1s 50c'], 'B4: non-finite money renders as a dash')
            check(R.evaluate("[parseMoney('-5s'), parseMoney('−5s'), parseMoney('5s'), parseMoney('1g 20s')]") == [0, 0, 500, 12000], 'B5: negative money rejected')
            # B8: three packs, no cloth farm; a stored farm pack is pruned by normalize()
            check(R.locator('#packBtns button').count() == 3 and 'Cloth' not in R.locator('#packBtns').inner_text(), 'B8: three packs, cloth-farm gone')
            check(R.evaluate("const s=normalize(Object.assign(freshState(),{packs:{farm:{name:'x',slots:[],labels:[],check:[]}},ui:{pack:'farm'}})); [!!s.packs.farm, s.ui.pack]") == [False, 'de'], 'B8: a stored farm pack is pruned and the selection falls back')
            ctxR.close()

            # ---- file:// still works, on localStorage alone ----
            ctxF = browser.new_context(viewport={'width': 1366, 'height': 900})
            F = ctxF.new_page(); watch(F, 'file')
            F.goto('file://' + os.path.join(ROOT, 'index.html')); F.wait_for_timeout(400)
            check(badge(F) == 'local file', 'file:// shows the local-file badge')
            check(not F.locator('#plan').is_hidden(), 'file:// page renders')
            browser.close()
    finally:
        api.terminate(); api.wait(5); shutil.rmtree(state_dir, ignore_errors=True)
        os.remove(rec_path)
        if had_rec:
            os.replace(rec_path + '.bak', rec_path)

    errors.pop('review', None) if not [e for e in errors.get('review', []) if 'ERR_' not in e] else None
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
