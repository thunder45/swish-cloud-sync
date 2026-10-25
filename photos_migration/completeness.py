"""One rule, shared by audit and cleanup: is a GoPro item completely in Google Photos?"""
from __future__ import annotations

SEQUENCE_TYPES = ("TimeLapse", "Burst")


def duplicate_target(row, by_unit: dict):
    """The unit a DUPLICATE row points to ('same original bytes as <unit_id>')."""
    note = row["note"] or ""
    return by_unit.get(note.split(" as ")[-1]) if " as " in note else None


def unit_in_google(row, by_unit: dict, readable: set[str] | None) -> bool:
    """DONE (and readable, when `readable` is given), or DUPLICATE of such a unit with the same bytes."""
    if row["status"] == "DONE":
        return readable is None or row["media_item_id"] in readable
    if row["status"] == "DUPLICATE":
        t = duplicate_target(row, by_unit)
        return bool(t and t["orig_sha256"] == row["orig_sha256"] and unit_in_google(t, by_unit, readable)
                    and t["status"] == "DONE")
    return False


def item_fully_in_google(item: dict, rows: list, by_unit: dict, readable: set[str] | None) -> tuple[bool, str]:
    """(complete?, reason). Every expected unit must be in Google:
    - time-lapse/burst: exactly item_count frames;
    - chaptered video uploaded per chapter: exactly item_count parts;
    - otherwise the single whole-item unit (older uploads merge chapters into one file).
    `readable=None` skips the read-back check (items Google merged into an existing original count)."""
    if not rows:
        return False, "no ledger rows"
    parts = [r for r in rows if "#" in r["unit_id"]]
    whole = [r for r in rows if r["unit_id"] == item["id"]]
    expected = item.get("item_count") or 1
    if item["type"] in SEQUENCE_TYPES:
        units = parts
        if len(units) != expected:
            return False, f"{len(units)}/{expected} frames in the ledger"
    elif parts:
        units = parts
        if len(units) != expected:
            return False, f"{len(units)}/{expected} chapters in the ledger"
    else:
        units = whole
    bad = [r for r in units if not unit_in_google(r, by_unit, readable)]
    if bad:
        return False, f"{len(bad)} unit(s) not in Google: " + ", ".join(f"{r['unit_id']}={r['status']}" for r in bad[:3])
    return True, ""
