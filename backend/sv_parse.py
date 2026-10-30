#!/usr/bin/env python3
"""Turn WoW SavedVariables into the planner's data files.

  prices.json    <- Auctionator's price database (your own scans)
  observed.json  <- a disenchant log: observed.csv you keep by hand, and/or a
                    disenchant addon's SavedVariables once its shape is mapped

Typical run (Windows path shown; quote it):
  python sv_parse.py --auctionator "C:/Program Files (x86)/World of Warcraft/_classic_/WTF/Account/<ACCOUNT>/SavedVariables/Auctionator.lua" --out ../data

Inspect any SavedVariables file as JSON (to map a new addon):
  python sv_parse.py --dump "<path>/SizDisenchantValue.lua" | less

Nothing here needs network or third-party packages.
"""
import argparse, csv, json, os, sys, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lua_table import load_saved_variables  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def load_id_map(path):
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    by_id, by_name = {}, {}
    for mat, v in raw.items():
        if mat.startswith('_'):
            continue
        if v.get('id') is not None:
            by_id[int(v['id'])] = mat
        if v.get('name'):
            by_name[v['name'].lower()] = mat
    return by_id, by_name


# ---------- Auctionator ----------
def auctionator_prices(sv, by_id, realm=None):
    """Walk AUCTIONATOR_PRICE_DATABASE -> {itemId: copper}, choosing a realm/ruleset key.

    Known shape (Auctionator 10.x for Classic clients):
      AUCTIONATOR_PRICE_DATABASE = {
        ["__dbversion"] = N,
        ["<Realm>"] = { ["2589"] = { ["m"] = <min copper now>, ["h"] = { [day] = <min that day> }, ... }, ["g:..."] = ... },
      }
    'm' is the current minimum; if absent we fall back to the latest 'h' entry. Gear keys ('g:') are skipped.
    """
    db = sv.get('AUCTIONATOR_PRICE_DATABASE')
    if not isinstance(db, dict):
        raise SystemExit('AUCTIONATOR_PRICE_DATABASE not found; is this Auctionator.lua?')
    realms = [k for k in db if isinstance(k, str) and not k.startswith('__') and isinstance(db[k], dict)]
    if not realms:
        raise SystemExit('no realm tables in the price database (scan the AH once, then /reload or log out)')
    if realm is None:
        # pick the realm with the most items
        realm = max(realms, key=lambda r: len(db[r]))
    elif realm not in db:
        raise SystemExit(f'realm {realm!r} not in database; have: {realms}')
    table = db[realm]
    prices, unmapped = {}, {}
    for key, rec in table.items():
        if not isinstance(key, str) or key.startswith('g:') or not isinstance(rec, dict):
            continue
        try:
            item_id = int(key.split(':')[0])
        except ValueError:
            continue
        price = rec.get('m')
        if not isinstance(price, (int, float)) or price <= 0:
            h = rec.get('h')
            if isinstance(h, dict) and h:
                last_day = max(k for k in h if isinstance(k, int))
                price = h[last_day]
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        mat = by_id.get(item_id)
        if mat:
            prices[mat] = int(price)
        else:
            unmapped[item_id] = int(price)
    return realm, realms, prices, unmapped


# ---------- observed disenchants ----------
def observed_from_csv(path, by_name):
    """observed.csv columns: lvl,slot,quality,mat,qty[,ts]  (mat = planner id or item name)."""
    out = []
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            mat = row['mat'].strip()
            mat = by_name.get(mat.lower(), mat)
            out.append({
                'lvl': int(row['lvl']), 'slot': row['slot'].strip().lower(),
                'quality': (row.get('quality') or 'green').strip().lower(),
                'mat': mat, 'qty': int(row.get('qty') or 1),
                'ts': int(row['ts']) if row.get('ts') else int(time.time() * 1000),
            })
    return out


def observed_from_addon(sv, by_id):
    """Adapter for a disenchant-logging addon's SavedVariables.

    Not mapped yet: the on-disk shape of Siz DisenchantValue / Grimlight Enchanting isn't
    documented. Run `sv_parse.py --dump <file>` after your first few disenchants, find the
    table that records (source item -> result item/count), and fill this function in.
    It must return a list of {lvl, slot, quality, mat, qty, ts}; lvl/slot come from the
    source item (ForeverDB items endpoint gives required level and inventory type).
    """
    raise NotImplementedError('map the addon table first; see --dump')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--auctionator', help='path to Auctionator.lua')
    ap.add_argument('--realm', help='realm/ruleset key inside the price DB (default: the biggest one)')
    ap.add_argument('--observed-csv', help='hand-kept disenchant log (lvl,slot,quality,mat,qty)')
    ap.add_argument('--de-sv', help='disenchant addon SavedVariables (adapter not mapped yet)')
    ap.add_argument('--ids', default=os.path.join(HERE, 'mat_item_ids.json'), help='mat -> item id map')
    ap.add_argument('--out', default=os.path.join(HERE, '..', 'data'), help='output directory')
    ap.add_argument('--dump', metavar='SV_FILE', help='print any SavedVariables file as JSON and exit')
    a = ap.parse_args()

    if a.dump:
        json.dump(load_saved_variables(a.dump), sys.stdout, indent=1, default=str)
        return

    by_id, by_name = load_id_map(a.ids)
    os.makedirs(a.out, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')

    if a.auctionator:
        sv = load_saved_variables(a.auctionator)
        realm, realms, prices, unmapped = auctionator_prices(sv, by_id, a.realm)
        mtime = datetime.fromtimestamp(os.path.getmtime(a.auctionator), timezone.utc).isoformat(timespec='seconds')
        doc = {'source': 'auctionator', 'realm': realm, 'realmsSeen': realms, 'fileModified': mtime,
               'generatedAt': now, 'prices': prices, 'unmappedCount': len(unmapped)}
        with open(os.path.join(a.out, 'prices.json'), 'w', encoding='utf-8') as f:
            json.dump(doc, f, indent=1)
        missing = [m for m in by_id.values() if m not in prices]
        print(f'prices.json: {len(prices)} mats priced from realm {realm!r}; {len(unmapped)} other items scanned; '
              f'no price yet for: {", ".join(sorted(set(missing))) or "none"}')

    obs = []
    if a.observed_csv and os.path.exists(a.observed_csv):
        obs += observed_from_csv(a.observed_csv, by_name)
    if a.de_sv:
        obs += observed_from_addon(load_saved_variables(a.de_sv), by_id)
    if a.observed_csv or a.de_sv:
        with open(os.path.join(a.out, 'observed.json'), 'w', encoding='utf-8') as f:
            json.dump({'generatedAt': now, 'observed': obs}, f, indent=1)
        print(f'observed.json: {len(obs)} disenchants')


if __name__ == '__main__':
    main()
