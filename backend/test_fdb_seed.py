#!/usr/bin/env python3
"""Tests for fdb_seed.py's mappers, offline, against objects pasted from a real --inspect run
(build 1.60.1.70658, 2026-10-30). No network. Also exposes synthetic_recipes() for the headless page check.

  python backend/test_fdb_seed.py
"""
import copy, json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fdb_seed as fs  # noqa: E402

# ---- fixtures: verbatim shapes from the API (notes trimmed) ----
RECIPE_BOLT = {"spellId": 2963, "name": "Bolt of Linen Cloth", "icon": "https://foreverdb.net/icons/132890.webp",
               "category": {"id": 2576, "name": "Reagents", "order": 100, "parent": {"id": 2435, "name": "Tailoring"}},
               "skill": {"orange": 1, "yellow": 25, "green": 37, "grey": 50}, "requiredLevel": None,
               "makes": {"itemId": 2996, "name": "Bolt of Linen Cloth", "count": 1, "quality": "Common", "url": "https://foreverdb.net/item/2996"},
               "effect": None, "reagents": [{"itemId": 2589, "name": "Linen Cloth", "count": 2}], "tools": [], "needsNearby": None,
               "learnedFrom": {"automatic": True, "trainer": None, "recipeItems": [], "recipeItemsHidden": [], "recipeItemsNotInClient": [],
                               "classicVendors": 0, "seenAtForeverTrainers": [], "sources": {"ways": ["auto"], "learnAtSkill": 1, "recipeItem": None, "note": "…"}, "known": True},
               "newInForever": False, "changedFromClassic": [], "sameNameRecipes": [], "leftoverOf": None, "classic": None, "campTier": None,
               "speculation": False, "speculationReasons": [], "url": "https://foreverdb.net/spell/2963"}
RECIPE_ROD = {"spellId": 7421, "name": "Runed Copper Rod", "icon": "https://foreverdb.net/icons/135225.webp",
              "category": {"id": 2491, "name": "Runed Enchanting Rods", "order": 20, "parent": {"id": 2427, "name": "Enchanting"}},
              "skill": {"orange": 1, "yellow": 5, "green": 7, "grey": 10}, "requiredLevel": None,
              "makes": {"itemId": 6218, "name": "Runed Copper Rod", "count": 1, "quality": "Common", "url": "https://foreverdb.net/item/6218"},
              "effect": None, "reagents": [{"itemId": 6217, "name": "Copper Rod", "count": 1}, {"itemId": 247786, "name": "Mote of Magic", "count": 1}],
              "tools": [], "needsNearby": None,
              "learnedFrom": {"automatic": True, "trainer": None, "recipeItems": [], "recipeItemsHidden": [], "recipeItemsNotInClient": [],
                              "classicVendors": 0, "seenAtForeverTrainers": [], "sources": {"ways": ["auto"], "learnAtSkill": 1, "recipeItem": None, "note": "…"}, "known": True},
              "newInForever": False, "changedFromClassic": ["reagents"], "sameNameRecipes": [], "leftoverOf": None,
              "classic": {"reagents": [{"itemId": 6217, "name": "Copper Rod", "count": 1}, {"itemId": 10940, "name": "Strange Dust", "count": 1}, {"itemId": 10938, "name": "Lesser Magic Essence", "count": 1}]},
              "campTier": None, "speculation": False, "speculationReasons": [], "url": "https://foreverdb.net/spell/7421"}
# plausible shapes for objects the inspect run did not show (an enchant with makes == null; a green craft)
RECIPE_ENCH = {"spellId": 7457, "name": "Enchant Bracer - Minor Stamina", "category": {"id": 1, "name": "Bracer", "parent": {"name": "Enchanting"}},
               "skill": {"orange": 70, "yellow": 90, "green": 105, "grey": 120}, "requiredLevel": None, "makes": None,
               "effect": {"spellId": 7457, "text": "+3 Stamina"}, "reagents": [{"itemId": 10940, "name": "Strange Dust", "count": 2}], "tools": [],
               "learnedFrom": {"automatic": False, "trainer": {"id": 3011, "name": "Lavinia Crowe"}, "recipeItems": [], "seenAtForeverTrainers": [{"id": 3011}],
                               "sources": {"ways": ["trainer"], "learnAtSkill": 70, "recipeItem": None}, "known": True},
               "newInForever": False, "changedFromClassic": [], "speculation": False, "url": "https://foreverdb.net/spell/7457"}
RECIPE_CAPE = {"spellId": 2402, "name": "Woolen Cape", "category": {"name": "Cloaks", "parent": {"name": "Tailoring"}},
               "skill": {"orange": 80, "yellow": 95, "green": 105, "grey": 115}, "requiredLevel": None,
               "makes": {"itemId": 2570, "name": "Woolen Cape", "count": 1, "quality": "Uncommon"}, "effect": None,
               "reagents": [{"itemId": 2997, "name": "Bolt of Woolen Cloth", "count": 1}, {"itemId": 2321, "name": "Fine Thread", "count": 1}], "tools": [],
               "learnedFrom": {"automatic": False, "trainer": {"name": "Bowen Brisboise"}, "sources": {"ways": ["trainer"], "learnAtSkill": 80}, "known": True},
               "newInForever": False, "changedFromClassic": [], "speculation": False}
ITEM_POTION = {"id": 118, "name": "Minor Healing Potion", "quality": "Common", "qualityId": 1, "itemLevel": 5, "requiredLevel": 1, "classId": 0, "class": "Consumable",
               "subclassId": 1, "subclass": "Potions", "inventoryTypeId": 0, "inventoryType": "Non-equippable", "icon": "", "url": "", "betaEvent": False,
               "speculation": False, "speculationReasons": [], "stats": [], "damage": None, "speed": None, "bonding": None, "sellPrice": 5, "unique": False,
               "effects": [{"trigger": "use", "spellId": 439, "text": "Restores 70 to 90 health."}], "disenchantable": False, "disenchant": None}
ITEM_HAMMER = {"id": 789, "name": "Stout Battlehammer", "quality": "Uncommon", "qualityId": 2, "itemLevel": 22, "requiredLevel": 17, "classId": 2, "class": "Weapon",
               "subclassId": 4, "subclass": "Mace", "inventoryTypeId": 21, "inventoryType": "Main Hand", "betaEvent": False, "speculation": False, "speculationReasons": []}
ITEM_CAPE = dict(ITEM_POTION, id=2570, name="Woolen Cape", quality="Uncommon", qualityId=2, itemLevel=21, requiredLevel=16, classId=4, **{"class": "Armor"},
                 inventoryType="Back", bonding="Bind on Equip", sellPrice=120, disenchantable=True)
ITEM_MOTE_GUESS = dict(ITEM_POTION, id=247786, name="Mote of Magic", itemLevel=1, sellPrice=5,
                       soldBy=[{"id": 1, "name": "Malcomb Wynn", "price": 20, "pricePerItem": 20, "seenInForever": True, "costs": []}])
DE_ROWS = [
    {"id": "q2-armor-1", "quality": "Uncommon", "qualityId": 2, "kind": "armor", "itemLevelMin": 1, "itemLevelMax": 15, "skill": None, "seenInForever": None,
     "classic": {"source": "Classic data (CMaNGOS), not confirmed in Forever", "results": [
         {"itemId": 10940, "name": "Strange Dust", "chancePercent": 80, "min": 1, "max": 2}, {"itemId": 10938, "name": "Lesser Magic Essence", "chancePercent": 20.0, "min": 1, "max": 2}]}},
    {"id": "q2-armor-21", "quality": "Uncommon", "qualityId": 2, "kind": "armor", "itemLevelMin": 21, "itemLevelMax": 25, "skill": None, "seenInForever": None,
     "classic": {"source": "Classic data (CMaNGOS), not confirmed in Forever", "results": [
         {"itemId": 10940, "name": "Strange Dust", "chancePercent": 75, "min": 4, "max": 6}, {"itemId": 10998, "name": "Lesser Astral Essence", "chancePercent": 15, "min": 1, "max": 2},
         {"itemId": 10978, "name": "Small Glimmering Shard", "chancePercent": 10.0, "min": 1, "max": 1}]}},
    # plausible Forever-observed row (shape guessed from the brief: results + disenchant/player counts)
    {"id": "q2-weapon-21", "quality": "Uncommon", "qualityId": 2, "kind": "weapon", "itemLevelMin": 21, "itemLevelMax": 25, "skill": None,
     "seenInForever": {"disenchants": 40, "players": 7, "results": [{"itemId": 10998, "name": "Lesser Astral Essence", "chancePercent": 72.5, "min": 1, "max": 2},
                                                                     {"itemId": 10940, "name": "Strange Dust", "chancePercent": 22.5, "min": 4, "max": 6},
                                                                     {"itemId": 10978, "name": "Small Glimmering Shard", "chancePercent": 5, "min": 1, "max": 1}]},
     "classic": {"source": "Classic", "results": []}},
]


def synthetic_recipes(idmap_path=None):
    """A recipes.json document built offline from the fixtures, for the page check."""
    with open(idmap_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mat_item_ids.json'), encoding='utf-8') as f:
        idmap = json.load(f)
    recipes = {'tailoring': [fs.map_recipe(RECIPE_BOLT, 'tailoring'), fs.map_recipe(RECIPE_CAPE, 'tailoring')],
               'enchanting': [fs.map_recipe(RECIPE_ROD, 'enchanting'), fs.map_recipe(RECIPE_ENCH, 'enchanting')]}
    items = {i['id']: fs.map_item(i) for i in (ITEM_POTION, ITEM_HAMMER, ITEM_CAPE, ITEM_MOTE_GUESS)}
    items[2996] = fs.map_item(dict(ITEM_POTION, id=2996, name="Bolt of Linen Cloth", classId=7, **{"class": "Trade Goods"}, sellPrice=10))
    items[6218] = fs.map_item(dict(ITEM_POTION, id=6218, name="Runed Copper Rod", classId=7, sellPrice=25))
    return fs.build_output(recipes, items, copy.deepcopy(DE_ROWS), copy.deepcopy(idmap), '1.60.1.70658', 'dev-0077')


class MapRecipeTest(unittest.TestCase):
    def test_bolt(self):
        r = fs.map_recipe(RECIPE_BOLT, 'tailoring')
        self.assertEqual(r['skill'], [1, 25, 37, 50])
        self.assertEqual(r['makes'], {'itemId': 2996, 'name': 'Bolt of Linen Cloth', 'count': 1, 'quality': 'white'})
        self.assertEqual(r['reagents'], [{'itemId': 2589, 'name': 'Linen Cloth', 'qty': 2}])
        self.assertEqual(r['learnedFrom']['ways'], ['auto']); self.assertTrue(r['learnedFrom']['auto'])
        self.assertEqual(r['category'], 'Reagents')

    def test_rod_changed_from_classic(self):
        r = fs.map_recipe(RECIPE_ROD, 'enchanting')
        self.assertEqual([x['itemId'] for x in r['reagents']], [6217, 247786])
        self.assertEqual(r['changedFromClassic'], ['reagents'])

    def test_enchant_without_product(self):
        r = fs.map_recipe(RECIPE_ENCH, 'enchanting')
        self.assertIsNone(r['makes']); self.assertEqual(r['learnedFrom']['trainer'], 'Lavinia Crowe')


class MapItemTest(unittest.TestCase):
    def test_quality_slot_levels(self):
        p = fs.map_item(ITEM_POTION); h = fs.map_item(ITEM_HAMMER); c = fs.map_item(ITEM_CAPE)
        self.assertEqual((p['quality'], p['slot'], p['itemLevel'], p['reqLevel'], p['sellPrice']), ('white', 'armor', 5, 1, 5))
        self.assertEqual((h['quality'], h['slot'], h['itemLevel'], h['reqLevel']), ('green', 'weapon', 22, 17))
        self.assertEqual((c['quality'], c['slot'], c['bop']), ('green', 'armor', False))
        self.assertTrue(fs.map_item(dict(ITEM_CAPE, bonding='Bind on Pickup'))['bop'])
        self.assertIsNone(p['vendorPrice']); self.assertEqual(fs.map_item(ITEM_MOTE_GUESS)['vendorPrice'], 20)


class MapDeTest(unittest.TestCase):
    def test_classic_row(self):
        d = fs.map_de_row(DE_ROWS[1], lambda iid, name: fs.slug(name))
        self.assertEqual((d['quality'], d['kind'], d['min'], d['max'], d['source'], d['n']), ('green', 'armor', 21, 25, 'classic', 0))
        self.assertEqual(d['results'][0], {'mat': 'strange_dust', 'itemId': 10940, 'name': 'Strange Dust', 'chance': 0.75, 'min': 4, 'max': 6})
        self.assertAlmostEqual(sum(r['chance'] for r in d['results']), 1.0)

    def test_forever_row_preferred(self):
        d = fs.map_de_row(DE_ROWS[2], lambda iid, name: fs.slug(name))
        self.assertEqual((d['source'], d['n'], d['players'], d['kind']), ('forever', 40, 7, 'weapon'))
        self.assertEqual(d['results'][0]['chance'], 0.725)


class BuildOutputTest(unittest.TestCase):
    def setUp(self):
        self.out = synthetic_recipes()

    def test_recipes(self):
        t = {r['id']: r for r in self.out['tailoring']}; e = {r['id']: r for r in self.out['enchanting']}
        self.assertTrue(t['bolt_linen']['intermediate'], 'intermediate keeps the seed recipe id so the page can price it through the recipe')
        self.assertEqual(t['bolt_linen']['inputs'], [{'mat': 'linen', 'qty': 2}], 'reagent resolved to the planner mat id by name')
        self.assertNotIn('bolt_linen', {m['id'] for m in self.out['mats']}); self.assertNotIn('bolt_wool', {m['id'] for m in self.out['mats']})
        cape = t['woolen_cape']
        self.assertEqual(cape['output'], {'quality': 'green', 'reqLevel': 16, 'itemLevel': 21, 'slot': 'armor', 'bop': False, 'itemId': 2570})
        self.assertEqual(cape['inputs'], [{'mat': 'bolt_wool', 'qty': 1}, {'mat': 'fine_thread', 'qty': 1}], 'intermediate resolved to the seed id by name')
        self.assertEqual(cape['vendor'], 120)
        self.assertTrue(e['runed_copper_rod']['consumesRod']); self.assertEqual(e['runed_copper_rod']['produces'], 'item')
        self.assertIsNone(e['enchant_bracer_minor_stamina']['produces']); self.assertEqual(e['enchant_bracer_minor_stamina']['effect'], '+3 Stamina')
        self.assertEqual(e['enchant_bracer_minor_stamina']['inputs'], [{'mat': 'strange_dust', 'qty': 2}])
        self.assertEqual(self.out['unmapped'], [])

    def test_mats_and_ids(self):
        mats = {m['id']: m for m in self.out['mats']}
        self.assertEqual(mats['mote']['itemId'], 247786, 'Forever id replaces the null Classic id by name')
        self.assertEqual(mats['mote']['vendor'], 20, 'vendor price taken from the reagent item when present')
        self.assertTrue(mats['strange_dust']['de']); self.assertFalse(mats['linen']['de'])
        self.assertIn('copper_rod', mats)

    def test_de_and_meta(self):
        self.assertEqual(len(self.out['de']), 3)
        self.assertEqual(self.out['stats']['deForeverRows'], 1)
        self.assertEqual((self.out['build'], self.out['client'], self.out['attribution']), ('1.60.1.70658', 'dev-0077', 'Data from ForeverDB.net'))
        json.dumps(self.out)  # serialisable

    def test_same_name_recipes_disambiguated(self):
        twin = copy.deepcopy(RECIPE_CAPE); twin['spellId'] = 99999
        out = fs.build_output({'tailoring': [fs.map_recipe(RECIPE_CAPE, 'tailoring'), fs.map_recipe(twin, 'tailoring')], 'enchanting': []},
                              {2570: fs.map_item(ITEM_CAPE)}, [], {'linen': {'id': 2589, 'name': 'Linen Cloth'}}, 'b', 'c')
        self.assertEqual([r['id'] for r in out['tailoring']], ['woolen_cape', 'woolen_cape_99999'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
