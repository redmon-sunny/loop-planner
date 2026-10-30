#!/usr/bin/env python3
"""Seed the planner's recipe tables from the ForeverDB API (https://foreverdb.net/developers).

Writes:
  data/recipes.json      planner-shaped tailoring + enchanting recipes and the mats they reference
  data/raw/*.json        every API response, untouched, for inspection and re-mapping
  backend/mat_item_ids.json  updated with item ids resolved by name

Run:
  export FDB_KEY=...            # from foreverdb.net, after API access is approved
  python fdb_seed.py --out ../data
  python fdb_seed.py --inspect  # print the shape of the first recipe + first item and exit (no files written)

Attribution: ForeverDB requires a visible "Data from ForeverDB.net" link wherever the data is shown;
the planner prints one in its footer when recipes.json is loaded.

The field names below (recipe skill colours, reagents, product) are best guesses at the API's JSON —
the developers page documents what is covered, not the exact keys. First run: use --inspect, then fix
the `pick()` paths in map_recipe()/map_item() if anything comes back empty. Rate limit is 60/min, so
the script sleeps between calls; a full Tailoring + Enchanting pull is ~1–2 minutes.
"""
import argparse, json, os, re, sys, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = 'https://foreverdb.net'
PROFESSIONS = ['tailoring', 'enchanting']
SLEEP = 1.1  # seconds between requests (60/min cap)


# ---------- http ----------
def get(path, key, params=None, cache_dir=None):
    url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
    slug = re.sub(r'[^A-Za-z0-9]+', '_', path + ('_' + urllib.parse.urlencode(params) if params else '')).strip('_')
    cache = os.path.join(cache_dir, slug + '.json') if cache_dir else None
    if cache and os.path.exists(cache):
        with open(cache, encoding='utf-8') as f:
            return json.load(f)
    req = urllib.request.Request(url, headers={'X-API-Key': key, 'Accept': 'application/json',
                                               'User-Agent': 'loop-planner-seed/0.1 (personal use)'})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get('Retry-After', '10'))
                print(f'  429, waiting {wait}s', file=sys.stderr); time.sleep(wait); continue
            raise SystemExit(f'HTTP {e.code} on {url}: {e.read()[:300]!r}')
    else:
        raise SystemExit('gave up after repeated 429s')
    time.sleep(SLEEP)
    if cache:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache, 'w', encoding='utf-8') as f:
            json.dump(body, f, indent=1)
    return body


def paged(path, key, cache_dir, **params):
    """Yield items from a list endpoint, following limit/offset until the total is reached."""
    offset, limit = 0, 100
    while True:
        body = get(path, key, dict(params, limit=limit, offset=offset), cache_dir)
        data = body.get('data', body)
        items = data if isinstance(data, list) else (data.get('recipes') or data.get('items') or data.get('results') or [])
        if isinstance(data, dict) and not items:
            # single-object response (e.g. a profession with nested recipe list under another key)
            for k, v in data.items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    items = v; break
        for it in items:
            yield it
        meta = body.get('meta', {})
        total = meta.get('total'); nxt = meta.get('next')
        if not items or (total is not None and offset + limit >= total) or (nxt in (None, False, '') and total is None):
            break
        offset += limit


# ---------- tolerant field access ----------
def pick(d, *paths, default=None):
    """pick(d, 'a.b', 'c') -> first non-empty value along any dotted path."""
    for p in paths:
        cur = d
        ok = True
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


DE_MAT = re.compile(r'\b(dust|essence|shard|crystal)\b', re.I)
WEAPON_TYPES = {'weapon', 'onehand', 'one-hand', 'twohand', 'two-hand', 'mainhand', 'offhand weapon', 'ranged', 'wand', 'staff', 'dagger', 'sword', 'mace', 'axe', 'bow', 'gun', 'crossbow', 'fist', 'polearm', 'thrown'}


def map_recipe(r, prof):
    """ForeverDB recipe -> planner recipe. Adjust the pick() paths after --inspect."""
    name = pick(r, 'name', 'spell.name', 'recipe.name', default='?')
    # skill colours: either a dict {orange,yellow,green,gray} or a 4-list
    sk = pick(r, 'skill', 'skillColors', 'skill_colours', 'skillup', 'colors', 'ranks')
    if isinstance(sk, dict):
        skill = [sk.get('orange') or sk.get('learn') or sk.get('min'), sk.get('yellow'), sk.get('green'), sk.get('gray') or sk.get('grey')]
    elif isinstance(sk, list) and len(sk) >= 4:
        skill = sk[:4]
    else:
        skill = [pick(r, 'skillRequired', 'requiredSkill', 'minSkill'), None, None, None]
    reagents = []
    for x in pick(r, 'reagents', 'materials', 'inputs', 'ingredients', default=[]) or []:
        iid = pick(x, 'item.id', 'itemId', 'id')
        iname = pick(x, 'item.name', 'name', 'itemName', default=str(iid))
        qty = pick(x, 'quantity', 'count', 'qty', 'amount', default=1)
        reagents.append({'itemId': iid, 'name': iname, 'qty': int(qty)})
    prod = pick(r, 'creates', 'makes', 'product', 'result', 'output', 'createsItem')
    prod_id = pick(prod, 'item.id', 'itemId', 'id') if isinstance(prod, dict) else None
    prod_name = pick(prod, 'item.name', 'name', default=name) if isinstance(prod, dict) else name
    learned = pick(r, 'learnedFrom.sources', 'learnedFrom', 'source', default=[])
    return {'id': slug(name), 'name': name, 'profession': prof, 'skill': skill, 'reagents': reagents,
            'productId': prod_id, 'productName': prod_name, 'learnedFrom': learned, '_raw_keys': sorted(r.keys())}


def map_item(it):
    q = pick(it, 'quality', 'quality.name', 'rarity', default='')
    if isinstance(q, dict):
        q = q.get('name') or q.get('id')
    q = str(q).lower()
    quality = 'blue' if q in ('rare', 'blue', '3') else 'green' if q in ('uncommon', 'green', '2') else 'white'
    inv = str(pick(it, 'inventoryType', 'slot', 'inventory_type', 'type', default='')).lower()
    slot = 'weapon' if any(w in inv for w in WEAPON_TYPES) else 'armor'
    return {'id': pick(it, 'id', 'itemId'), 'name': pick(it, 'name', default='?'), 'quality': quality,
            'reqLevel': int(pick(it, 'requiredLevel', 'reqLevel', 'required_level', 'level.required', default=0) or 0),
            'slot': slot, 'binding': pick(it, 'binding', 'bind', default=None),
            'sellPrice': int(pick(it, 'sellPrice', 'sell_price', 'vendorSell', default=0) or 0),
            'itemLevel': pick(it, 'itemLevel', 'ilvl', default=None), '_raw_keys': sorted(it.keys())}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--key', default=os.environ.get('FDB_KEY'), help='API key (or env FDB_KEY)')
    ap.add_argument('--out', default=os.path.join(HERE, '..', 'data'))
    ap.add_argument('--inspect', action='store_true', help='print the first recipe and item objects and exit')
    ap.add_argument('--no-cache', action='store_true', help='ignore data/raw cache')
    a = ap.parse_args()
    if not a.key:
        raise SystemExit('no API key: pass --key or set FDB_KEY (request access at foreverdb.net/developers)')
    raw_dir = None if a.no_cache else os.path.join(a.out, 'raw')

    meta = get('/api/v1/meta', a.key, cache_dir=raw_dir)
    build = pick(meta, 'data.build', 'meta.build', 'build', default='unknown')
    print(f'ForeverDB build {build}')

    recipes = {}
    for prof in PROFESSIONS:
        rows = list(paged(f'/api/v1/professions/{prof}', a.key, raw_dir))
        print(f'{prof}: {len(rows)} recipes')
        if a.inspect and rows:
            print(json.dumps(rows[0], indent=1)[:4000]);
        recipes[prof] = [map_recipe(r, prof) for r in rows]

    # products -> items (quality, req level, slot) ; reagents -> mats
    item_ids = set()
    for rs in recipes.values():
        for r in rs:
            if r['productId']: item_ids.add(int(r['productId']))
            for x in r['reagents']:
                if x['itemId']: item_ids.add(int(x['itemId']))
    items = {}
    for n, iid in enumerate(sorted(item_ids)):
        body = get(f'/api/v1/items/{iid}', a.key, cache_dir=raw_dir)
        items[iid] = map_item(body.get('data', body))
        if a.inspect and n == 0:
            print(json.dumps(body, indent=1)[:3000]); return
    if a.inspect:
        return

    # planner schema
    ids_path = os.path.join(HERE, 'mat_item_ids.json')
    with open(ids_path, encoding='utf-8') as f:
        idmap = json.load(f)
    name_to_mat = {v['name'].lower(): k for k, v in idmap.items() if not k.startswith('_') and v.get('name')}

    def mat_id_for(iid, name):
        m = name_to_mat.get(name.lower())
        if m:
            idmap[m]['id'] = iid; return m
        s = slug(name); idmap[s] = {'id': iid, 'name': name}; name_to_mat[name.lower()] = s; return s

    mats = {}
    out = {'source': 'foreverdb', 'build': build, 'generatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
           'attribution': 'Data from ForeverDB.net', 'tailoring': [], 'enchanting': [], 'mats': [], 'unmapped': []}
    for prof, rs in recipes.items():
        for r in rs:
            if not all(isinstance(v, (int, float)) for v in r['skill']):
                out['unmapped'].append({'recipe': r['name'], 'why': 'skill colours missing', 'keys': r['_raw_keys']}); continue
            inputs = []
            for x in r['reagents']:
                mid = mat_id_for(x['itemId'], x['name']) if x['itemId'] else slug(x['name'])
                mats.setdefault(mid, {'id': mid, 'name': x['name'], 'itemId': x['itemId'], 'vendor': None, 'de': bool(DE_MAT.search(x['name']))})
                inputs.append({'mat': mid, 'qty': x['qty']})
            prod = items.get(int(r['productId'])) if r['productId'] else None
            rec = {'id': r['id'], 'name': r['name'], 'skill': [int(v) for v in r['skill']], 'inputs': inputs, 'sell': 0,
                   'learnedFrom': r['learnedFrom'], 'build': build}
            if prof == 'tailoring':
                rec['output'] = {'quality': prod['quality'] if prod else 'white', 'reqLevel': prod['reqLevel'] if prod else 0,
                                 'slot': prod['slot'] if prod else 'armor', 'bop': bool(prod and str(prod['binding']).lower().startswith(('pickup', 'bop', 'bind on pickup')))}
                rec['vendor'] = prod['sellPrice'] if prod else 0
                if prod and prod['name'] and 'bolt of' in prod['name'].lower():
                    rec['intermediate'] = True
                    mats.setdefault(rec['id'], {'id': rec['id'], 'name': prod['name'], 'itemId': prod['id'], 'vendor': None, 'de': False})
            else:
                rec['produces'] = 'item' if prod and prod['quality'] != 'white' or (prod and re.search(r'wand|oil|rod', prod['name'], re.I)) else None
                rec['consumesRod'] = bool(re.search(r'\brod\b', r['name'], re.I))
            out[prof].append(rec)
    out['mats'] = sorted(mats.values(), key=lambda m: m['name'])
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'recipes.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1)
    with open(ids_path, 'w', encoding='utf-8') as f:
        json.dump(idmap, f, indent=2)
    print(f"recipes.json: {len(out['tailoring'])} tailoring, {len(out['enchanting'])} enchanting, {len(out['mats'])} mats; "
          f"{len(out['unmapped'])} recipes unmapped (see 'unmapped' in the file)")


if __name__ == '__main__':
    main()
