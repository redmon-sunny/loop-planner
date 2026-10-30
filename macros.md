# Loop Planner — enchanting / disenchanting macros

WoW's secure system allows exactly one spell cast per hardware keypress, and no addon or macro can
bypass that. So every "queue" below is: you press a key, one thing happens, you press it again.
That is the human gate. Keybind each macro to one key near the movement cluster (F1–F4, or the
mouse side buttons) so a batch is "press the same key N times while the kettle boils".

All macros are Classic-era syntax; Forever has kept the Classic API so far. Verify each once at launch —
if a line errors, the spell name or slot number is what changed. Macro length limit is 255 characters.

---

## 1. Disenchant the next item in the DE bag (one press = one disenchant)

Dedicate your leftmost bag (bag 4) to disenchant fodder. Mail or loot greens straight into it.

```
#showtooltip Disenchant
/cast Disenchant
/use 4 1
```

- `/use 4 1` = bag 4, slot 1. Each press disenchants whatever is in that slot.
- Items don't auto-shift, so after slot 1 is empty the macro does nothing. Fix: sort the bag (the
  default "Clean Up Bags" button, or `/run C_Container.SortBags()` on another key) and press again,
  or make a second macro with `/use 4 2`, a third with `/use 4 3`.
- The addon route (Disenchant Forever, Siz DisenchantValue) is this macro with "find the next
  green" done for you — still one click per cast. Install one; keep this macro as the fallback.
- Safety: the macro only ever targets bag 4. Nothing equipped, nothing in other bags can be hit.

## 2. Skill-up enchant on your own gear (one press = one cast, second press = confirm)

Inventory slot ids: 5 chest · 8 feet · 9 wrist · 10 hands · 15 back · 16 main hand · 17 off hand.

```
#showtooltip
/cast Enchant Bracer - Minor Stamina
/use 9
```

Re-enchanting the same slot pops "replace existing enchantment?". Put the confirm on its own key:

```
/click StaticPopup1Button1
```

Rhythm for a batch of 30: cast-key, confirm-key, cast-key, confirm-key. Swap the spell name as you
move up the table (Minor Deflect → Minor Stamina → Minor Protection on `/use 15` for cloaks → Minor
Stamina boots on `/use 8`). The planner's Enchanting tab tells you which one is cheapest per point
right now; this file is just the mechanics.

## 3. Enchant someone else's gear (trade window)

No macro possible — the trade-window item must be clicked by hand. Flow: they open trade, drop the
item in the "Will not be traded" slot, you `/cast Enchant …` and click the item, both accept.
The enchant lands on their item, the skill-up on you. Worth a `/macro` anyway just to shorten the cast:

```
#showtooltip
/cast Enchant Chest - Lesser Stamina
```

## 4. Wand crafting / oils (profession window)

Enchanting crafts like Lesser Magic Wand and Minor Mana Oil are made from the profession window,
where "Create All" already queues N crafts behind one click (interrupted by moving). No macro needed.
Tailoring is the same: Bolt of Linen ×30 is one click.

## 5. Session guard

One macro that keeps the bag discipline honest at any town stop:

```
/run local c=C_Container local n=0 for s=1,c.GetContainerNumSlots(4) do local i=c.GetContainerItemInfo(4,s) if i and i.quality==2 then n=n+1 end end print("DE bag: "..n.." greens")
```

Prints the number of greens waiting in bag 4. Zero = walk past the trainer; more than zero = press
macro 1 that many times. That's the whole "nudge".

---

### Keybind suggestion (dad-gaming layout)

| Key | Macro |
|---|---|
| F1 | Disenchant next (macro 1) |
| F2 | Current skill-up enchant (macro 2) |
| F3 | Confirm popup |
| F4 | DE bag count (macro 5) |

Four keys, no mouse travel, every press is one reversible thing. Nothing fires without you.
