# Loop Planner

Self-hosted Tailoring -> Disenchant -> Enchanting planner for World of Warcraft: Forever. Single HTML page plus two Python scripts; runs on a LAN, one user.

Recipe and item data: [Data from ForeverDB.net](https://foreverdb.net). Prices and disenchant observations are the user's own.

Single static page plus two Python scripts. No frameworks, no database, no third-party packages.

```
loop-planner/
  index.html              the planner (open directly, or serve it)
  data/                   files the page loads on open when served over http(s)
    recipes.json          from fdb_seed.py   (ForeverDB)     â€” optional
    prices.json           from sv_parse.py   (Auctionator)   â€” optional
    observed.json         from sv_parse.py   (your DE log)   â€” optional
    raw/                  ForeverDB responses, cached; not served
  backend/
    fdb_seed.py           ForeverDB API -> data/recipes.json
    sv_parse.py           SavedVariables -> data/prices.json + data/observed.json
    lua_table.py          Lua table-literal parser used by sv_parse.py (no Lua runtime)
    mat_item_ids.json     planner mat id <-> WoW item id/name
  docker-compose.yml      nginx:alpine on :8087
  nginx.conf
```

## Run it

**Local file:** open `index.html`. Seed data only; the `data/` loader is skipped on `file://`.

**Homelab:** `docker compose up -d`, then `http://<host>:8087/`. The page reads `data/*.json` from its own
origin (same-origin, no CORS). Keep it off the public internet â€” there is no auth, and localStorage is per-browser.

## Precedence (what wins when sources disagree)

| Thing | Order |
|---|---|
| Recipes, skill colours, mats | `recipes.json` (replaces the seed tables once per ForeverDB build) > seed. Recipes you add with `custom: true` in the Data tab survive a rebuild. `sell` prices you typed are kept by recipe id. |
| Prices | a price you typed by hand beats `prices.json` until a scan **newer** than your edit arrives; then the scan wins |
| Observed disenchants | union of the page's log and `observed.json`, deduped on (lvl, slot, quality, mat, qty, ts) |

Everything the page holds is in the browser's localStorage. **Data â†’ Export** writes it all to one JSON; Import restores it.

## Backend

### Prices and observed DE â€” `sv_parse.py`

Auctionator writes its scan database to
`World of Warcraft\_classic_\WTF\Account\<ACCOUNT>\SavedVariables\Auctionator.lua`
(folder name may differ for Forever â€” look for the one containing `Auctionator.lua` after your first scan).
The file is written on logout or `/reload`, not live.

```
python backend/sv_parse.py --auctionator "<path>/Auctionator.lua" --observed-csv observed.csv --out data
```

- Picks the realm/ruleset table with the most items; `--realm Normal` to force one (`realmsSeen` in the output lists them).
- Uses each item's current minimum (`m`), falling back to the latest daily history entry.
- Maps item ids to planner mats through `backend/mat_item_ids.json`. Ids there are Classic-era; Forever items
  that got new ids show up as `unmappedCount` until `fdb_seed.py` rewrites the map from ForeverDB names.
- `observed.csv` is a hand-kept log: `lvl,slot,quality,mat,qty` per disenchant (mat = planner id or item name).
  One line per DE is the whole discipline; the Observed tab does the stats.
- `--de-sv <file>` is a stub for a disenchant addon's SavedVariables. Run `--dump <file>` after a few disenchants,
  find the table that records source item â†’ result, and fill in `observed_from_addon()`.

Windows scheduled task or a post-logout hook: run the command above, then copy `data/*.json` to the homelab share.
Or run it on the homelab against a synced copy of the `WTF` folder.

### Recipes â€” `fdb_seed.py`

1. Log in at https://foreverdb.net with Battle.net, request API access (state the use case, agree to credit the site), create a key.
2. `export FDB_KEY=â€¦` then `python backend/fdb_seed.py --inspect` â€” prints the first recipe and item objects.
3. If any field in `map_recipe()` / `map_item()` comes back empty, fix the `pick()` paths to match what you saw. The API docs list
   what is covered (skill colours, reagents, product, learnedFrom; item quality, required level, sell price) but not the exact key names.
4. `python backend/fdb_seed.py --out data` â€” writes `data/recipes.json`, caches every response in `data/raw/`, and updates
   `backend/mat_item_ids.json` with real item ids. Unmapped recipes are listed under `unmapped` in the file rather than dropped.

Rate limit 60/min, 10k/day; the script sleeps between calls and honours `Retry-After`. Re-run per game build.
Attribution: the page footer shows "Data from ForeverDB.net" whenever `recipes.json` is loaded â€” keep it.

## What the page does with it

- **Prices** â€” type or import AH buyouts; vendor items are fixed. Bolts can be priced too (the cost model buys them if cheaper than crafting).
- **Tailoring** â€” per craft at your skill: skill-up chance, cost/point, expected disenchant yield, net after DE value.
- **Enchanting** â€” per recipe: cost/point with each mat at the cheaper of AH and its effective crafted price.
- **Plan** â€” greedy Aâ†’B solver for both professions; tailoring's DE output is banked and enchanting draws it down. Outputs crafts, cloth consumed, shopping list, surplus, net engine cost.
- **Shuffle** â€” expected DE value and max buyout per required-level bracket.
- **Observed DE** â€” log real results; 20 samples per bracket+slot overrides the seed table.
- **Data** â€” every table as editable JSON; Export/Import; Reset.
- Header line: next Enchanting wand floor and next Tailoring trainer gate â€” the only two numbers you need while playing.

## v0.3 â€” Session mode (added 2026-10-23)

`page.html` is the source; `./build.sh` wraps it into `index.html` for nginx. The page opens in **Session** mode on
touch or narrow screens and **Plan** mode on the desk; the toggle in the header is remembered.

Session mode is the couch/iPad flow: Now tiles (both skills, distance to the next wand floor and trainer gate,
price freshness, DE-bag count once the companion addon exists) â†’ This stop (the cheapest enchant and craft right now,
buyout ceilings, one rule) â†’ a session pack (Disenchant batch / Craft + enchant / Cloth farm / AH + mail) that fills
eight fixed macros LP1â€“LP8 for A/B/X/Y and LB+A/B/X/Y, copies them, and shows a tap checklist â†’ a one-tap disenchant
logger. Plan mode is the tab rail plus a **Macros** tab where pack templates are edited and exported in
`macros-cache.txt` shape.

Characters: the header selector holds one skill pair per character; `data/character.json` (from the companion addon,
not built yet) will fill them. Prices start as marked examples and are replaced by the first scan or typed price.

