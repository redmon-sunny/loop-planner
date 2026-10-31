# Loop Planner

Self-hosted Tailoring -> Disenchant -> Enchanting planner for World of Warcraft: Forever. Single HTML page plus two Python scripts; runs on a LAN, one user.

Recipe and item data: [Data from ForeverDB.net](https://foreverdb.net). Prices and disenchant observations are the user's own.

Single static page plus two Python scripts. No frameworks, no database, no third-party packages.

```
loop-planner/
  index.html              the planner (open directly, or serve it)
  data/                   files the page loads on open when served over http(s)
    recipes.json          from fdb_seed.py   (ForeverDB)     — optional
    prices.json           from sv_parse.py   (Auctionator)   — optional
    observed.json         from sv_parse.py   (your DE log)   — optional
    raw/                  ForeverDB responses, cached; not served
  backend/
    fdb_seed.py           ForeverDB API -> data/recipes.json
    sv_parse.py           SavedVariables -> data/prices.json + data/observed.json
    lua_table.py          Lua table-literal parser used by sv_parse.py (no Lua runtime)
    mat_item_ids.json     planner mat id <-> WoW item id/name
    state_api.py          shared-state HTTP service (state/state.json) — see "Shared state"
    sv_pull.sh            cron: pull SavedVariables from the desktop over SSH, then sv_parse.py
    test_state_api.py     unit tests for it (stdlib)
    headless_check.py     Playwright page check (dev only)
  scripts/Enable-SvPull.ps1   desktop one-time: OpenSSH Server + core-01's key (read-only pull of the WoW folder)
  state/                  state.json and sv/ (pulled .lua files); git-ignored
  docker-compose.yml      nginx:alpine on :8087 + python:3.12-alpine state API behind /api/
  nginx.conf
```

## Run it

**Local file:** open `index.html`. Seed data only; the `data/` loader is skipped on `file://`.

**Homelab:** once, `install -d -o "$(id -u)" -g "$(id -g)" state` (Docker would otherwise create it root-owned) and,
if your uid isn't 1000, `LP_UID=$(id -u)` and `LP_GID=$(id -g)` in `.env`. Then `docker compose up -d` and
`http://<host>:8087/`. Two containers: nginx serving the page and `data/`, and the state API (see below) behind
`/api/`, running as your uid. Keep it off the public internet — there is no auth.

**Without Docker:** `python backend/state_api.py --static .` serves the page, `data/` and the API on :8088.

## Precedence (what wins when sources disagree)

| Thing | Order |
|---|---|
| Recipes, skill colours, mats | `recipes.json` (replaces the seed tables once per ForeverDB build) > seed. Recipes you add with `custom: true` in the Data tab survive a rebuild. `sell` prices you typed are kept by recipe id. |
| Disenchant yields | your Observed log for a bracket+slot+quality with ≥ 20 samples > `recipes.json`'s table (Forever-observed rows, else Classic) > the page's Classic seed |
| Prices | a price you typed by hand beats `prices.json` until a scan **newer** than your edit arrives; then the scan wins |
| Observed disenchants | union of the page's log and `observed.json`, deduped on (lvl, slot, quality, mat, qty, ts) |

**Data → Export** copies the whole document as JSON; Import restores it.

## The page (v0.6): four tabs

| Tab | For | What is on it |
|---|---|---|
| **Now** | the 2-minute town stop, any device | one "Do this now" sentence per profession; four tiles coloured by state (≤15 points to a floor = green, scan age fresh/stale/none, DE bag count); This Stop with one action per profession and the math behind a tap; buyout ceilings as a strip by item level with the bracket your crafts feed highlighted |
| **Session** | the couch, iPad + controller | three packs; the controller legend (labels only; bodies behind a toggle); Copy all 8; checklist; the DE tap logger |
| **Plan** | the desk | Prices · Tailoring · Enchanting · A→B solver · Shuffle · Macros · Observed, as a second row of tabs |
| **Data** | housekeeping | characters (rename/delete), theme, export/import/reset, the recipe/mat/yield tables with overrides |

The tab that opens first is chosen once per device from the pointer type (touch → Now, mouse → Plan) and then
remembered; the layout adapts to any width live, but resizing or rotating never switches tabs. Theme and the open
tab are per device and never shared.

**Pricing rule on Now:** a number appears only when every mat behind it has a price. Otherwise the line says
"prices missing" and names them; nothing is ever costed at zero. "Craft now" is the best net-per-point craft that is
craftable and fully priced; bind-on-pickup outputs and recipes with any unpriced input are skipped and named when
they would otherwise have won. The same filter drives the A→B solver and the macro placeholders. Thresholds (15
points, 24 h) are provisional until real prices exist.

**Tables are not in the shared document.** Recipes, mats and the yield table come from `data/recipes.json`
(ForeverDB) on every device — cached per device so an API outage doesn't lose them — or from the built-in Classic
seed on `file://`. What travels is yours: custom recipes, sale prices, vendor-price and yield-row overrides (Data →
Apply diffs the tables against the source and stores only the differences). That takes the document from ~450 KB to
~20 KB per save. Materials that exist under two ids (seed `lme`, ForeverDB `lesser_magic_essence`) are aliased by
item id or name on load, and every price, observation and override key is migrated to the ForeverDB id.

## Shared state — `state_api.py` (v0.4)

One JSON document, `state/state.json`, shared by every browser on the LAN: characters and skills, typed prices,
the observed DE log, checklists, packs, UI prefs. The page loads it on open, writes it 400 ms after any change,
and shows a badge in the header:

| Badge | Meaning |
|---|---|
| `synced · rev N` | the API has this document; N climbs on every write from any device |
| `saving…` | a write is in flight |
| `offline` | API unreachable; edits stay in this browser's localStorage and are pushed when it answers again |
| `local file` | opened from disk: localStorage only, nothing shared |

Rules:
- Every PUT carries `If-Match: <rev>`, the rev the page loaded. If another device wrote since, the server answers
  **409 with its current document** and writes nothing; the page takes that document, re-applies **only the paths it
  changed itself** (plain objects split two levels deep: `chars.Main.tail`, `prices.linen`, `ui.pack`, `checks.de.0`;
  arrays whole) and PUTs again, up to three times. The iPad bumping Enchanting and the desk bumping Tailoring on
  the same character are two paths and both land; the same path edited on both is last-write-wins at that path.
  A save with nothing changed in the shared document (a mode or theme switch) sends no PUT at all.
- The observed DE log never rides a document PUT from the page: a tap is one `POST`, an undo one `DELETE`. The
  server still **unions** `observed` on every PUT, keyed by (lvl, slot, quality, mat, qty, ts), and an undo leaves a
  tombstone so a device still holding the entry can't re-add it; logging the same entry again on purpose clears it.
- Every successful PUT returns the merged log, so the writer adopts what other devices added or removed. Open pages
  also poll `/api/health` every 15 s and merge the document when another device has written since.
- Mode and theme are per device, not in the shared document: the iPad stays in Session while the desk stays in Plan.
- Offline: edits and taps stay in this browser; when the API answers again the page merges the server's document,
  re-applies its own changed paths, unions its queued taps, and pushes.
- A data file that no script has written yet (`data/recipes.json` before the first seed) answers 204, not 404, so
  the browser console stays clean.

Dev mode (`--static`) serves only `/index.html` and `*.json` directly under `data/`; any `..`, percent-encoding,
backslash or symlink out of `data/` is refused (unit-tested). Not a substitute for nginx on a shared host.

Endpoints, all JSON, all under `/api/`: `GET health`, `GET state` (204 until the first write), `PUT state`,
`POST observed` (one entry or a list), `DELETE observed` (one entry, or `{"all":true}`). Same-origin only (no CORS
headers; the page reaches it through nginx). Stdlib Python, atomic writes, one lock. `python backend/test_state_api.py` runs the unit tests; `python backend/headless_check.py`
(needs Playwright, dev only) drives two browsers through the sync, offline and layout checks.

## Backend

### Prices and observed DE — `sv_parse.py`

Auctionator writes its scan database to
`World of Warcraft\_classic_\WTF\Account\<ACCOUNT>\SavedVariables\Auctionator.lua`
(folder name may differ for Forever — look for the one containing `Auctionator.lua` after your first scan).
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
  find the table that records source item → result, and fill in `observed_from_addon()`.

Windows scheduled task or a post-logout hook: run the command above, then copy `data/*.json` to the homelab share.
Or run it on the homelab against a synced copy of the `WTF` folder.

### Getting the SavedVariables off the desktop — `sv_pull.sh` + `scripts/Enable-SvPull.ps1`

core-01 pulls `Auctionator.lua` and `ForeverDB.lua` from the gaming desktop over SSH on a timer and runs
`sv_parse.py`; the desktop needs nothing but the Windows OpenSSH Server feature. The WoW folder is only ever read.

Desktop, once, elevated PowerShell (core-01's public key from `cat ~/.ssh/id_ed25519.pub`):
```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
C:\scripts\Enable-SvPull.ps1 -CoreKey "ssh-ed25519 AAAA… redmon@core-01" -LanCidr 192.168.1.0/24
```
Installs sshd, starts it on boot, opens TCP 22 on the Private profile for the LAN only, authorises the key.

core-01, once:
```sh
ssh <winuser>@<desktop> 'echo ok'                 # accept the host key
printf "SV_HOST=<winuser>@<desktop>\nSV_DIR='C:/Program Files (x86)/World of Warcraft/_classic_/WTF/Account/<ACCOUNT>/SavedVariables'\n" >> .env
sh backend/sv_pull.sh                             # first pull by hand; then
crontab -e   →   */30 * * * *  cd $HOME/srv/loop-planner && sh backend/sv_pull.sh >> state/sv_pull.log 2>&1
```
A run while nothing changed is one SSH round-trip and no parse; desktop asleep is a silent exit; a pulled file is
parsed into `data/` at once. Copies live in `state/sv/` (git-ignored, never served) with the original mtimes, so
`prices.json`'s `fileModified` is the real scan time.

### Recipes and disenchant yields — `fdb_seed.py` (v0.5, mapped against build 1.60.1.70658)

```
set -a; . ./.env; set +a                        # FDB_KEY lives in .env on the homelab
python3 backend/fdb_seed.py --inspect           # ~4 calls: prints the objects whose shape is still unconfirmed
python3 backend/fdb_seed.py                     # full seed; refuses past --max-calls 500 unless --yes
python3 backend/fdb_seed.py --greens            # also data/greens.json: every uncommon item by required level
```

- Writes `data/recipes.json`: every Tailoring and Enchanting recipe (471 + 261 on the beta build), the mats they use,
  the disenchant yield table, build, client id and attribution. Every response is cached under `data/raw/` and never
  re-fetched, so a second run after a stop costs nothing.
- Recipes carry `spellId`, skill colours, `inputs` resolved to planner mat ids **by name** (Forever renumbers new items:
  Mote of Magic is 247786, not a Classic id), `output` with quality, **item level**, required level, slot and bind,
  `learnedFrom` (auto / trainer / recipe item, learn-at skill), and `newInForever` / `changedFromClassic` /
  `speculation` flags straight from ForeverDB. Same-name recipes get `_<spellId>` suffixes. Intermediates (bolts) keep
  the seed ids (`bolt_linen`) because the page prices them through their recipe.
- **The disenchant table is keyed by item level**, as the game rolls it; the page moved to item level everywhere in v0.5
  (recipes know their output's item level; the tap logger and Shuffle lookup ask for item level). Rows are
  `{quality, kind, min, max, source, n, results[{mat, chance, min, max}]}`; `source` is `forever` when ForeverDB has
  player-observed results for that row, else `classic` (flagged (C) in the page). On the Oct 30 pull every row was
  Classic — your Observed log is the override until uploads arrive.
- Vendor buy prices for reagents: the script tries `soldBy` / `vendors` on `/items/{id}` and reports how many mats got
  one; zero means the field lives elsewhere and the planner keeps its seed vendor prices (flagged).
- Quota: one full seed is roughly 2 list pages per profession + one `/items/{id}` per distinct product and reagent
  (~600) + `/disenchant` ≈ 650 calls, ~12 minutes at the 60/min limit. The script estimates before it starts and stops
  at `--max-calls` (500) unless `--yes` — the brief says ask before spending more than that.
- `backend/test_fdb_seed.py` tests the mappers offline against objects pasted from a real `--inspect` run.

## What the page does with it

- **Prices** — type or import AH buyouts; vendor items are fixed. Bolts can be priced too (the cost model buys them if cheaper than crafting).
- **Tailoring** — per craft at your skill: skill-up chance, cost/point, expected disenchant yield, net after DE value.
- **Enchanting** — per recipe: cost/point with each mat at the cheaper of AH and its effective crafted price.
- **Plan** — greedy A→B solver for both professions; tailoring's DE output is banked and enchanting draws it down. Outputs crafts, cloth consumed, shopping list, surplus, net engine cost.
- **Shuffle** — expected DE value and max buyout per required-level bracket.
- **Observed DE** — log real results; 20 samples per bracket+slot overrides the seed table.
- **Data** — every table as editable JSON; Export/Import; Reset.
- Header line: next Enchanting wand floor and next Tailoring trainer gate — the only two numbers you need while playing.

## v0.3 — Session mode (added 2026-10-23; superseded by the tabs in v0.6)

`page.html` is the source; `./build.sh` wraps it into `index.html` for nginx. The page opens in **Session** mode on
touch or narrow screens and **Plan** mode on the desk; the toggle in the header is remembered.

Session mode is the couch/iPad flow: Now tiles (both skills, distance to the next wand floor and trainer gate,
price freshness, DE-bag count once the companion addon exists) → This stop (the cheapest enchant and craft right now,
buyout ceilings, one rule) → a session pack (Disenchant batch / Craft + enchant / Cloth farm / AH + mail) that fills
eight fixed macros LP1–LP8 for A/B/X/Y and LB+A/B/X/Y, copies them, and shows a tap checklist → a one-tap disenchant
logger. Plan mode is the tab rail plus a **Macros** tab where pack templates are edited and exported in
`macros-cache.txt` shape.

Characters: the header selector holds one skill pair per character; `data/character.json` (from the companion addon,
not built yet) will fill them. Prices start as marked examples and are replaced by the first scan or typed price.

