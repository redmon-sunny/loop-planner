#!/usr/bin/env python3
"""Seed the planner's recipe tables and disenchant yields from the ForeverDB API (https://foreverdb.net/developers).

Writes:
  data/recipes.json          planner-shaped tailoring + enchanting recipes, the mats they reference,
                             the disenchant yield table, and the build/attribution the page shows
  data/raw/*.json            every API response, untouched, for inspection and re-mapping (also the cache)
  data/greens.json           only with --greens: every uncommon item by required level, for the Shuffle/Market views
  backend/mat_item_ids.json  updated with item ids resolved by name (Forever renumbers new items: Mote of Magic is 247786)

Run (key in .env on the homelab, never in the shell history):
  set -a; . ./.env; set +a
  python3 backend/fdb_seed.py --inspect          # ~4 calls: the objects whose shape is still unconfirmed, then exit
  python3 backend/fdb_seed.py                    # full seed; refuses above --max-calls (500) unless --yes

Field mappings were fixed on 2026-10-30 against build 1.60.1.70658 from a real --inspect run. Shapes seen:
  recipe:  spellId, name, category{name, parent{name}}, skill{orange,yellow,green,grey}, requiredLevel,
           makes{itemId,name,count,quality} | null, effect, reagents[{itemId,name,count}], tools, learnedFrom{...},
           newInForever, changedFromClassic[], classic{reagents}, speculation
  item:    id, name, quality ("Common"/"Uncommon"/"Rare"), itemLevel, requiredLevel, classId (2 = Weapon),
           inventoryType, bonding, sellPrice, disenchantable, disenchant
  /disenchant: rows {id "q2-armor-16", quality, kind armor|weapon, itemLevelMin/Max, seenInForever|null,
           classic{source, results[{itemId,name,chancePercent,min,max}]}}  — keyed by ITEM level
Still unconfirmed (the script reports what it finds): the `effect` object on enchant recipes, the shape of
`seenInForever` once players upload, and where a reagent's vendor buy price lives (`soldBy`/`vendors` are tried).

Attribution: ForeverDB requires a visible "Data from ForeverDB.net" link wherever the data is shown;
the planner prints one in its footer when recipes.json is loaded. Rate limit 60/min, 10,000/day per key.
"""
import argparse, json, os, re, sys, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = 'https://foreverdb.net'
PROFESSIONS = ['tailoring', 'enchanting']
SLEEP = 1.05            # seconds between live requests (60/min cap)
CALLS = {'live': 0, 'cached': 0}


# ---------- http ----------
def get(path, key, params=None, cache_dir=None, max_calls=None):
    url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
    slug_ = re.sub(r'[^A-Za-z0-9]+', '_', path + ('_' + urllib.parse.urlencode(params) if params else '')).strip('_')
    cache = os.path.join(cache_dir, slug_ + '.json') if cache_dir else None
    if cache and os.path.exists(cache):
        CALLS['cached'] += 1
        with open(cache, encoding='utf-8') as f:
            return json.load(f)
    if max_calls is not None and CALLS['live'] >= max_calls:
        raise SystemExit(f'stopped at {CALLS["live"]} live calls (--max-calls {max_calls}); re-run with --yes to spend more. '
                         f'Everything fetched so far is cached under data/raw and will not be re-fetched.')
    req = urllib.request.Request(url, headers={'X-API-Key': key, 'Accept': 'application/json',
                                               'User-Agent': 'loop-planner-seed/0.2 (personal use)'})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get('Retry-After', '10'))
                print(f'  429, waiting {wait}s', file=sys.stderr); time.sleep(wait); continue
            if e.code == 404:
                return None
            raise SystemExit(f'HTTP {e.code} on {url}: {e.read()[:300]!r}')
    else:
        raise SystemExit('gave up after repeated 429s')
    CALLS['live'] += 1
    time.sleep(SLEEP)
    if cache:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache, 'w', encoding='utf-8') as f:
            json.dump(body, f, indent=1)
    return body


LIST_KEYS = ('recipes', 'items', 'results', 'rows')   # a list response is an object holding several lists (ranks, categories, recipes…)


def list_of(body):
    """The rows of a list response: data itself when it is a list, else data.<recipes|items|results|rows>, never a sibling list."""
    data = body.get('data', body) if isinstance(body, dict) else body
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in LIST_KEYS:
            if isinstance(data.get(k), list):
                return data[k]
    return []


def is_recipe(r):
    return isinstance(r, dict) and isinstance(r.get('skill'), dict) and 'rank' not in r


def paged(path, key, cache_dir, max_calls=None, **params):
    """Yield rows from a list endpoint. Follows meta.next when given, else limit/offset until meta.total."""
    offset, limit = 0, 100
    while True:
        body = get(path, key, dict(params, limit=limit, offset=offset), cache_dir, max_calls)
        if body is None:
            return
        items = list_of(body)
        for it in items:
            yield it
        meta = body.get('meta', {}) if isinstance(body, dict) else {}
        total, nxt = meta.get('total'), meta.get('next')
        if not items or not nxt and (total is None or offset + limit >= total):
            break
        offset += limit


def pick(d, *paths, default=None):
    """pick(d, 'a.b', 'c') -> first non-empty value along any dotted path."""
    for p in paths:
        cur, ok = d, True
        for part in p.split('.'):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                ok = False; break
        if ok and cur not in (None, '', [], {}):
            return cur
    return default


def slug(name):
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


# ---------- mappers (pure; unit-tested against the pasted shapes) ----------
DE_MAT = re.compile(r'\b(dust|essence|shard|crystal)\b', re.I)
QUALITY = {'poor': 'gray', 'common': 'white', 'uncommon': 'green', 'rare': 'blue', 'epic': 'epic', 'legendary': 'epic'}
QUALITY_ID = {0: 'gray', 1: 'white', 2: 'green', 3: 'blue', 4: 'epic', 5: 'epic'}


def quality_of(obj):
    q = pick(obj, 'quality', 'makes.quality', default=None)
    if isinstance(q, str) and q.lower() in QUALITY:
        return QUALITY[q.lower()]
    qid = pick(obj, 'qualityId', default=None)
    if isinstance(qid, int):
        return QUALITY_ID.get(qid, 'white')
    return 'white'


def map_recipe(r, prof):
    """ForeverDB recipe -> intermediate record (product resolved later from /items)."""
    name = pick(r, 'name', default='?')
    sk = pick(r, 'skill', default={}) or {}
    skill = [sk.get('orange'), sk.get('yellow'), sk.get('green'), sk.get('grey', sk.get('gray'))]
    reagents = [{'itemId': x.get('itemId'), 'name': x.get('name') or str(x.get('itemId')), 'qty': int(x.get('count') or 1)}
                for x in (r.get('reagents') or []) if isinstance(x, dict)]
    makes = r.get('makes') if isinstance(r.get('makes'), dict) else None
    lf = r.get('learnedFrom') or {}
    src = lf.get('sources') or {}
    trainer = lf.get('trainer')
    learned = {
        'auto': bool(lf.get('automatic')),
        'ways': src.get('ways') or [],
        'learnAtSkill': src.get('learnAtSkill'),
        'trainer': (trainer.get('name') if isinstance(trainer, dict) else trainer) or None,
        'recipeItem': (src.get('recipeItem') or {}).get('name') if isinstance(src.get('recipeItem'), dict) else src.get('recipeItem'),
        'seenAtForeverTrainers': len(lf.get('seenAtForeverTrainers') or []),
        'known': lf.get('known'),
    }
    cat = r.get('category') or {}
    return {
        'spellId': r.get('spellId'), 'name': name, 'profession': prof, 'skill': skill,
        'category': cat.get('name'), 'reqLevel': r.get('requiredLevel'),
        'makes': ({'itemId': makes.get('itemId'), 'name': makes.get('name'), 'count': int(makes.get('count') or 1),
                   'quality': quality_of(makes)} if makes else None),
        'effect': r.get('effect'), 'reagents': reagents, 'tools': [t.get('name') if isinstance(t, dict) else t for t in (r.get('tools') or [])],
        'learnedFrom': learned, 'newInForever': bool(r.get('newInForever')), 'changedFromClassic': r.get('changedFromClassic') or [],
        'speculation': bool(r.get('speculation')), 'url': r.get('url'),
    }


def vendor_price(it):
    """Reagent buy price in copper from an /items object, or None. Keys are guesses until a vendor reagent is inspected."""
    for key in ('soldBy', 'vendors', 'sources.vendors', 'buyPrice', 'vendorPrice'):
        v = pick(it, key)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, list):
            prices = []
            for e in v:
                if not isinstance(e, dict):
                    continue
                p = e.get('pricePerItem')
                if p is None and e.get('price') is not None:
                    p = e['price'] / max(1, int(e.get('stackCount') or e.get('count') or 1))
                if isinstance(p, (int, float)) and p > 0 and not e.get('costs'):   # gold-priced only
                    prices.append((0 if e.get('seenInForever') else 1, int(round(p))))
            if prices:
                prices.sort()
                return prices[0][1]
    return None


def map_item(it):
    bonding = it.get('bonding')
    bop = (isinstance(bonding, str) and 'pickup' in bonding.lower().replace(' ', '')) or bonding == 1
    return {
        'id': it.get('id'), 'name': it.get('name') or '?', 'quality': quality_of(it),
        'reqLevel': int(it.get('requiredLevel') or 0), 'itemLevel': int(it.get('itemLevel') or 0),
        'slot': 'weapon' if it.get('classId') == 2 or str(it.get('class') or '').lower() == 'weapon' else 'armor',
        'inventoryType': it.get('inventoryType'), 'bop': bool(bop), 'bonding': bonding,
        'sellPrice': int(it.get('sellPrice') or 0), 'vendorPrice': vendor_price(it),
        'disenchantable': it.get('disenchantable'), 'keys': sorted(it.keys()),
    }


def map_de_row(row, mat_for):
    """One /disenchant row -> planner yield row. Prefers Forever-observed results, falls back to Classic (flagged)."""
    seen = row.get('seenInForever') if isinstance(row.get('seenInForever'), dict) else None
    src = seen if seen and seen.get('results') else (row.get('classic') or {})
    results = []
    for x in src.get('results') or []:
        if not isinstance(x, dict):
            continue
        chance = x.get('chancePercent', x.get('chance'))
        chance = float(chance) / 100 if isinstance(chance, (int, float)) and chance > 1 else float(chance or 0)
        results.append({'mat': mat_for(x.get('itemId'), x.get('name') or ''), 'itemId': x.get('itemId'), 'name': x.get('name'),
                        'chance': round(chance, 4), 'min': int(x.get('min') or 1), 'max': int(x.get('max') or x.get('min') or 1)})
    return {
        'id': row.get('id'), 'quality': QUALITY_ID.get(row.get('qualityId'), quality_of(row)), 'kind': row.get('kind') or 'armor',
        'min': int(row.get('itemLevelMin') or 1), 'max': int(row.get('itemLevelMax') or 60),
        'source': 'forever' if src is seen else 'classic',
        'n': int(seen.get('disenchants') or seen.get('count') or 0) if seen else 0,
        'players': int(seen.get('players') or 0) if seen else 0,
        'results': results,
    }


# ---------- assembly ----------
def build_output(recipes, items, de_rows, idmap, build, client):
    """recipes: {prof: [map_recipe()]}, items: {itemId: map_item()}, de_rows: raw /disenchant rows."""
    name_to_mat = {v['name'].lower(): k for k, v in idmap.items() if not k.startswith('_') and v.get('name')}

    def mat_id_for(iid, name):
        m = name_to_mat.get(name.lower())
        if m:
            if iid:
                idmap[m]['id'] = iid
            return m
        s = slug(name)
        idmap[s] = {'id': iid, 'name': name}
        name_to_mat[name.lower()] = s
        return s

    mats, out = {}, {'source': 'foreverdb', 'build': build, 'client': client,
                     'generatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                     'attribution': 'Data from ForeverDB.net', 'attributionUrl': 'https://foreverdb.net',
                     'tailoring': [], 'enchanting': [], 'mats': [], 'de': [], 'unmapped': [], 'stats': {}}
    seen_ids, intermediates = {}, set()
    for prof, rs in recipes.items():
        for r in rs:
            if not all(isinstance(v, (int, float)) for v in r['skill']):
                out['unmapped'].append({'recipe': r['name'], 'spellId': r['spellId'], 'why': 'skill colours missing', 'skill': r['skill']}); continue
            rid = slug(r['name'])
            intermediate = prof == 'tailoring' and ((r['category'] or '').lower() == 'reagents'
                                                   or bool(r['makes'] and r['makes']['name'] and r['makes']['name'].lower().startswith('bolt of')))
            if intermediate and r['makes'] and name_to_mat.get(r['makes']['name'].lower()):
                rid = name_to_mat[r['makes']['name'].lower()]  # the page prices an intermediate through its recipe id (bolt_linen), so keep the seed id
            if rid in seen_ids and seen_ids[rid] != r['spellId']:
                rid = f"{rid}_{r['spellId']}"            # sameNameRecipes: keep both, disambiguated by spell
            seen_ids[rid] = r['spellId']
            inputs = []
            for x in r['reagents']:
                mid = mat_id_for(x['itemId'], x['name'])
                it = items.get(x['itemId']) if x['itemId'] else None
                if not (mid in intermediates or x['name'].lower().startswith('bolt of')):
                    m = mats.setdefault(mid, {'id': mid, 'name': x['name'], 'itemId': x['itemId'], 'vendor': None, 'de': bool(DE_MAT.search(x['name']))})
                    if it and it.get('vendorPrice') and m['vendor'] is None:
                        m['vendor'] = it['vendorPrice']
                inputs.append({'mat': mid, 'qty': x['qty']})
            prod = items.get(r['makes']['itemId']) if r['makes'] else None
            rec = {'id': rid, 'spellId': r['spellId'], 'name': r['name'], 'skill': [int(v) for v in r['skill']], 'inputs': inputs, 'sell': 0,
                   'category': r['category'], 'learnedFrom': r['learnedFrom'], 'tools': r['tools'], 'build': build}
            for flag in ('newInForever', 'speculation'):
                if r[flag]:
                    rec[flag] = True
            if r['changedFromClassic']:
                rec['changedFromClassic'] = r['changedFromClassic']
            if r['makes']:
                rec['count'] = r['makes']['count']
            if prof == 'tailoring':
                q = prod['quality'] if prod else (r['makes']['quality'] if r['makes'] else 'white')
                rec['output'] = {'quality': q, 'reqLevel': prod['reqLevel'] if prod else 0, 'itemLevel': prod['itemLevel'] if prod else 0,
                                 'slot': prod['slot'] if prod else 'armor', 'bop': bool(prod and prod['bop']),
                                 'itemId': r['makes']['itemId'] if r['makes'] else None}
                rec['vendor'] = (prod['sellPrice'] * rec.get('count', 1)) if prod else 0
                if intermediate:
                    rec['intermediate'] = True
                    intermediates.add(rid)
                    if r['makes']:
                        idmap.setdefault(rid, {'id': r['makes']['itemId'], 'name': r['makes']['name']})
                        mats.pop(rid, None)   # an intermediate is priced through its recipe, never listed as a mat
            else:
                rec['produces'] = 'item' if r['makes'] else None
                rec['consumesRod'] = bool(re.search(r'\brods?\b', r['category'] or '', re.I)) or bool(re.match(r'^runed .* rod$', r['name'], re.I))
                if r['makes']:
                    rec['output'] = {'quality': prod['quality'] if prod else r['makes']['quality'], 'itemLevel': prod['itemLevel'] if prod else 0,
                                     'reqLevel': prod['reqLevel'] if prod else 0, 'itemId': r['makes']['itemId']}
                    rec['vendor'] = (prod['sellPrice'] * rec.get('count', 1)) if prod else 0
                if isinstance(r['effect'], dict):
                    rec['effect'] = r['effect'].get('text') or r['effect'].get('name') or r['effect']
                elif r['effect']:
                    rec['effect'] = r['effect']
            out[prof].append(rec)
    # seed existing mat ids the recipes didn't mention (so the planner's mat list stays complete)
    for k, v in idmap.items():
        if k.startswith('_') or k in mats or k in intermediates or k.startswith('bolt_'):
            continue
        mats[k] = {'id': k, 'name': v.get('name') or k, 'itemId': v.get('id'), 'vendor': None, 'de': bool(DE_MAT.search(v.get('name') or ''))}
    out['mats'] = sorted(mats.values(), key=lambda m: m['name'])
    out['de'] = sorted((map_de_row(row, mat_id_for) for row in de_rows if isinstance(row, dict)), key=lambda d: (d['quality'], d['kind'], d['min']))
    vend = sum(1 for m in out['mats'] if m['vendor'])
    out['stats'] = {'tailoring': len(out['tailoring']), 'enchanting': len(out['enchanting']), 'mats': len(out['mats']),
                    'matsWithVendorPrice': vend, 'deRows': len(out['de']),
                    'deForeverRows': sum(1 for d in out['de'] if d['source'] == 'forever'), 'unmapped': len(out['unmapped']),
                    'newInForever': sum(1 for p in PROFESSIONS for r in out[p] if r.get('newInForever'))}
    return out


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--key', default=os.environ.get('FDB_KEY'), help='API key (or env FDB_KEY)')
    ap.add_argument('--out', default=os.path.join(HERE, '..', 'data'))
    ap.add_argument('--inspect', action='store_true', help='fetch only the objects whose shape is unconfirmed, print them, exit')
    ap.add_argument('--greens', action='store_true', help='also write data/greens.json (every uncommon item, req level 1-60; ~30 calls)')
    ap.add_argument('--no-cache', action='store_true', help='ignore data/raw cache (re-fetch everything)')
    ap.add_argument('--max-calls', type=int, default=500, help='refuse to go past this many live calls unless --yes')
    ap.add_argument('--yes', action='store_true', help='allow more than --max-calls live calls (the brief says ask first)')
    a = ap.parse_args()
    if not a.key:
        raise SystemExit('no API key: pass --key or set FDB_KEY (request access at foreverdb.net/developers)')
    raw_dir = None if a.no_cache else os.path.join(a.out, 'raw')
    cap = None if a.yes else a.max_calls
    G = lambda path, params=None: get(path, a.key, params, raw_dir, cap)

    if a.inspect:
        print('== /items/247786 (Mote of Magic: where does a vendor reagent carry its buy price?)')
        mote = G('/api/v1/items/247786')
        print(json.dumps((mote or {}).get('data', mote), indent=1)[:3500])
        print('\n== first enchanting recipe with makes == null (shape of `effect`)')
        ench = None
        for r in paged('/api/v1/professions/enchanting', a.key, raw_dir, cap):
            if is_recipe(r) and not r.get('makes'):
                ench = r; break
        print(json.dumps({k: ench[k] for k in ench if k != 'learnedFrom'}, indent=1)[:2500] if ench else 'none found')
        print('\n== /disenchant summary')
        de = G('/api/v1/disenchant')
        rows = (de or {}).get('data', de) or []
        kinds = sorted({(r.get('qualityId'), r.get('kind')) for r in rows if isinstance(r, dict)})
        print(f'{len(rows)} rows; (qualityId, kind) present: {kinds}; rows with seenInForever: '
              f'{sum(1 for r in rows if isinstance(r, dict) and r.get("seenInForever"))}')
        sv = next((r['seenInForever'] for r in rows if isinstance(r, dict) and r.get('seenInForever')), None)
        print('seenInForever sample:', json.dumps(sv, indent=1)[:1500] if sv else 'none yet (all Classic)')
        print(f'\nlive calls: {CALLS["live"]}, cached: {CALLS["cached"]}')
        return

    meta = G('/api/v1/meta') or {}
    build = pick(meta, 'data.build', 'meta.build', 'build', default=None)
    client = pick(meta, 'meta.client', 'client', default=None)

    recipes = {}
    for prof in PROFESSIONS:
        rows = [r for r in paged(f'/api/v1/professions/{prof}', a.key, raw_dir, cap) if is_recipe(r)]
        print(f'{prof}: {len(rows)} recipes')
        if len(rows) < 50:
            raise SystemExit(f'only {len(rows)} {prof} recipes came back; the list shape changed (data keys: '
                             'see data/raw) — stopping before any item is fetched')
        recipes[prof] = [map_recipe(r, prof) for r in rows]
        if rows and not build:
            # no /meta: the list response carries the build and client too
            body = G(f'/api/v1/professions/{prof}', {'limit': 100, 'offset': 0})
            build = pick(body, 'meta.build', default=build); client = pick(body, 'meta.client', default=client)
    print(f'ForeverDB build {build} (client {client})')

    item_ids = set()
    for rs in recipes.values():
        for r in rs:
            if r['makes'] and r['makes']['itemId']:
                item_ids.add(int(r['makes']['itemId']))
            for x in r['reagents']:
                if x['itemId']:
                    item_ids.add(int(x['itemId']))
    need = [i for i in sorted(item_ids) if not (raw_dir and os.path.exists(os.path.join(raw_dir, f'api_v1_items_{i}.json')))]
    print(f'{len(item_ids)} distinct items; {len(need)} not cached (~{len(need) * SLEEP / 60:.0f} min at the rate limit)')
    if cap is not None and len(need) + 2 > cap - CALLS['live']:
        raise SystemExit(f'this run needs ~{len(need) + 2} more live calls, over the {cap} cap. Re-run with --yes '
                         f'(one-time per build; ~{len(need) * SLEEP / 60:.0f} min), or raise --max-calls.')
    items = {}
    for n, iid in enumerate(sorted(item_ids)):
        body = G(f'/api/v1/items/{iid}')
        if body:
            items[iid] = map_item(body.get('data', body))
        if n % 100 == 99:
            print(f'  items {n + 1}/{len(item_ids)}', flush=True)

    de = G('/api/v1/disenchant')
    de_rows = (de or {}).get('data', de) or []

    ids_path = os.path.join(HERE, 'mat_item_ids.json')
    with open(ids_path, encoding='utf-8') as f:
        idmap = json.load(f)
    out = build_output(recipes, items, de_rows, idmap, build, client)

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'recipes.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1)
    with open(ids_path, 'w', encoding='utf-8') as f:
        json.dump(idmap, f, indent=2)
    s = out['stats']
    print(f"recipes.json: {s['tailoring']} tailoring, {s['enchanting']} enchanting ({s['newInForever']} new in Forever), {s['mats']} mats "
          f"({s['matsWithVendorPrice']} with a vendor price), {s['deRows']} DE rows ({s['deForeverRows']} Forever-observed), "
          f"{s['unmapped']} recipes unmapped")
    if s['matsWithVendorPrice'] == 0:
        sample = next((items[i] for i in items if items[i]['name'].lower() == 'mote of magic'), None)
        print('  no vendor prices found on /items objects; keys on Mote of Magic:', sample['keys'] if sample else 'not fetched',
              '\n  -> the planner keeps its seed vendor prices (flagged) until the vendor shape is mapped')

    if a.greens:
        greens = []
        for it in paged('/api/v1/items', a.key, raw_dir, cap, quality=2, minLevel=1, maxLevel=60):
            m = map_item(it)
            greens.append({'id': m['id'], 'name': m['name'], 'reqLevel': m['reqLevel'], 'itemLevel': m['itemLevel'], 'slot': m['slot'],
                           'class': it.get('class'), 'subclass': it.get('subclass'), 'inventoryType': m['inventoryType']})
        with open(os.path.join(a.out, 'greens.json'), 'w', encoding='utf-8') as f:
            json.dump({'source': 'foreverdb', 'build': build, 'generatedAt': out['generatedAt'], 'attribution': out['attribution'], 'items': greens}, f, indent=1)
        print(f'greens.json: {len(greens)} uncommon items, req level 1-60')
    print(f'live calls: {CALLS["live"]}, cached: {CALLS["cached"]}')


if __name__ == '__main__':
    main()
