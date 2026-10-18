"""L5 import/export core: vCard files, Android backup, duplicates (Revision 2).

Pure module: no GTK, no D-Bus, safe headless and in tests. The service
is the single writer: the UI dry-runs :func:`plan_import` against records
it reads directly (reads are allowed), shows the counts in a confirm
dialog, and then performs exactly one ``Import`` service call, which
re-runs the same plan inside one store transaction with one ``Changed``.

Duplicate rule (spec section 4, task): same UID wins first (a vCard
``UID`` that is an RFC 4122 uuid matching a stored uid, or an Android
lookup-key ``UID`` kept in ``extra`` matching another stored one);
otherwise the same normalized name (casefolded display name) plus at
least one equal normalized phone digit string. Anything else is a
create with a fresh uid. Cards with no name, phone, email or org are
skipped, mirroring the Android export filter.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

from xx_contacts import phones as phones_module
from xx_contacts.groups import reserved_kind
from xx_contacts.store import is_valid_uid, new_uid


@dataclass
class IncomingCard:
    """One parsed card plus every identity key it carries."""

    data: object = None
    hint: str | None = None
    raw_uids: list[str] = field(default_factory=list)


@dataclass
class PlannedCreate:
    data: object = None
    uid: str = ""


@dataclass
class PlannedUpdate:
    uid: str = ""
    data: object = None


@dataclass
class ImportPlan:
    creates: list[PlannedCreate] = field(default_factory=list)
    updates: list[PlannedUpdate] = field(default_factory=list)
    skipped: int = 0

    @property
    def new_count(self) -> int:
        return len(self.creates)

    @property
    def update_count(self) -> int:
        return len(self.updates)

    @property
    def total(self) -> int:
        return len(self.creates) + len(self.updates) + self.skipped


@dataclass
class GroupEntry:
    title: str = ""
    members: list[str] = field(default_factory=list)


def name_key(name: str | None) -> str:
    return (name or "").strip().casefold()


def digits_of_data(data) -> set[str]:
    """Normalized digit strings for every phone on parsed/store data."""
    found: set[str] = set()
    for phone in getattr(data, "phones", ()) or ():
        digits = getattr(phone, "number_digits", "") or ""
        if not digits:
            digits = phones_module.digits_of(
                getattr(phone, "number_raw", "") or "")
        if digits:
            found.add(digits)
    return found


def android_uids_of_data(data) -> list[str]:
    """Non-uuid ``UID`` values a card carries (Android lookup keys)."""
    found: list[str] = []
    for line in getattr(data, "extra", ()) or ():
        if (getattr(line, "prop", "") or "").upper() != "UID":
            continue
        value = (getattr(line, "value", "") or "").strip()
        if value and value not in found:
            found.append(value)
    return found


def is_empty_data(data) -> bool:
    """Mirror the Android export filter: nothing worth importing."""
    if (getattr(data, "display_name", "") or "").strip():
        return False
    if (getattr(data, "org", "") or "").strip():
        return False
    for phone in getattr(data, "phones", ()) or ():
        if (getattr(phone, "number_raw", "") or "").strip():
            return False
    for email in getattr(data, "emails", ()) or ():
        if (getattr(email, "address", "") or "").strip():
            return False
    return True


def parse_text(text: str, default_region: str = "US"
               ) -> list[IncomingCard]:
    """Parse every card of ``text`` (3.0 or 4.0, single or multi)."""
    from xx_contacts import vcard as vcard_module

    incoming: list[IncomingCard] = []
    for data, hint in vcard_module.contacts_from_vcard(
            text or "", default_region or "US"):
        incoming.append(IncomingCard(data=data, hint=hint,
                                    raw_uids=android_uids_of_data(data)))
    return incoming


def _photo_carried(data) -> bool:
    if (getattr(data, "photo_path", "") or "").strip():
        return True
    for line in getattr(data, "extra", ()) or ():
        if (getattr(line, "prop", "") or "").upper() == "PHOTO":
            return True
    return False


def merged_for_update(existing, incoming):
    """Incoming wholesale, except a missing photo never wipes the stored one.

    An import file that carries no ``PHOTO`` at all (no path, no PHOTO
    line) must not delete the photo the stored contact already has;
    everything else (groups, tags, extra) comes from the file so an
    export re-imports losslessly.
    """
    merged = copy.deepcopy(incoming)
    if not _photo_carried(incoming) and _photo_carried(existing):
        merged.photo_path = existing.photo_path
    return merged


def plan_import(existing_records, incoming: list[IncomingCard],
                default_region: str = "US") -> ImportPlan:
    """Decide create/update/skip for parsed cards without writing."""
    _ = default_region
    plan = ImportPlan()
    by_uid: dict[str, object] = {}
    by_android_uid: dict[str, str] = {}
    by_name_phone: dict[tuple[str, str], str] = {}
    taken: set[str] = set()

    def _index(uid: str, data) -> None:
        taken.add(uid)
        for raw in android_uids_of_data(data):
            by_android_uid.setdefault(raw, uid)
        nkey = name_key(getattr(data, "display_name", ""))
        if nkey:
            for digits in digits_of_data(data):
                by_name_phone.setdefault((nkey, digits), uid)

    for record in existing_records or ():
        uid = getattr(record, "uid", "")
        if not uid:
            continue
        by_uid[uid] = record
        _index(uid, record)

    for card in incoming:
        data = card.data
        if data is None or is_empty_data(data):
            plan.skipped += 1
            continue
        target: str | None = None
        if card.hint and is_valid_uid(card.hint) and card.hint in by_uid:
            target = card.hint
        if target is None:
            for raw in card.raw_uids:
                if raw in by_android_uid:
                    target = by_android_uid[raw]
                    break
        if target is None:
            nkey = name_key(getattr(data, "display_name", ""))
            if nkey:
                for digits in digits_of_data(data):
                    if (nkey, digits) in by_name_phone:
                        target = by_name_phone[(nkey, digits)]
                        break
        if target is not None:
            merged = merged_for_update(by_uid[target], data)
            plan.updates.append(PlannedUpdate(uid=target, data=merged))
            _index(target, merged)
            by_uid[target] = merged
        else:
            chosen = (card.hint if card.hint and is_valid_uid(card.hint)
                      and card.hint not in taken else new_uid())
            while chosen in taken:
                chosen = new_uid()
            plan.creates.append(PlannedCreate(data=data, uid=chosen))
            by_uid[chosen] = data
            _index(chosen, data)
    return plan


def record_like_for(card):
    """Build a serializable record from a parsed card (folded groups in).

    The uid is the card's uuid hint, else blank (the parser drops a
    bare ``UID:`` line, so the service assigns one); the Android
    lookup key survives in ``extra`` and re-parses into ``raw_uids``.
    """
    from xx_contacts.store import ContactRecord

    record = ContactRecord()
    for field_name in card.data.__dataclass_fields__:
        setattr(record, field_name, getattr(card.data, field_name))
    record.uid = card.hint or ""
    return record


def backup_to_text(cards: list[IncomingCard]) -> str:
    """Serialize folded backup cards for the single ``Import`` call."""
    from xx_contacts import vcard as vcard_module

    return "".join(vcard_module.contact_to_vcard(record_like_for(card))
                   for card in cards)


def _count(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def exported_line(count: int, path: str) -> str:
    return f"Exported {_count(count, 'contact')} to {path}."


def summarize(plan: ImportPlan, groups_applied: int = 0) -> str:
    """Confirm-dialog body: counts before anything is written."""
    parts = [f"{plan.new_count} new",
             _count(plan.update_count, "update"),
             f"{plan.skipped} skipped"]
    if groups_applied:
        parts.append(_count(groups_applied, "group membership"))
    return ("This import will write " + ", ".join(parts) + ".")


def parse_groups_json(text: str) -> list[GroupEntry]:
    """Android ``groups.json`` shape: ``[{title, members[]}]``."""
    if not (text or "").strip():
        return []
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"bad groups.json: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("bad groups.json: expected a list")
    groups: list[GroupEntry] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("bad groups.json: expected objects")
        title = item.get("title", "")
        members = item.get("members", [])
        if not isinstance(title, str) or not isinstance(members, list):
            raise ValueError("bad groups.json: bad group shape")
        groups.append(GroupEntry(
            title=title.strip(),
            members=[str(m) for m in members if str(m).strip()]))
    return [g for g in groups if g.title]


def fold_groups_json(incoming: list[IncomingCard], text: str
                     ) -> tuple[int, int]:
    """Fold ``groups.json`` memberships into parsed cards before Import.

    Members are Android lookup keys (or our uuids on a Linux round
    trip): each resolves to the parsed card carrying that key. ``Star``
    / ``Business`` / ``Blocked`` set the contact flags instead of a
    group row (matching ``groups.stored_name``); ``Blocked`` also keeps
    the canonical ``Blocked`` group row. Returns
    ``(applied, unmapped)``.
    """
    groups = parse_groups_json(text)
    if not groups:
        return 0, 0
    keymap: dict[str, int] = {}
    for index, card in enumerate(incoming):
        if card.hint:
            keymap.setdefault(card.hint, index)
        for raw in card.raw_uids:
            keymap.setdefault(raw, index)
    applied = 0
    unmapped = 0
    for group in groups:
        kind = reserved_kind(group.title)
        for member in group.members:
            index = keymap.get(member)
            if index is None:
                unmapped += 1
                continue
            data = incoming[index].data
            if kind == "star":
                data.starred = True
            elif kind == "business":
                data.business = True
            elif kind == "blocked":
                data.blocked = True
                if "Blocked" not in (data.groups or []):
                    data.groups.append("Blocked")
            else:
                if group.title not in (data.groups or []):
                    data.groups.append(group.title)
            applied += 1
    return applied, unmapped


def export_text(records, photo_reader=None) -> str:
    """Serialize records to one multi-contact vCard 4.0 document."""
    from xx_contacts import vcard as vcard_module

    if photo_reader is None:
        from xx_contacts import photos as photos_module

        photo_reader = photos_module.read_photo_bytes
    chunks: list[str] = []
    for record in records or ():
        photo = None
        path = getattr(record, "photo_path", "") or ""
        if path:
            try:
                photo = photo_reader(path)
            except OSError:
                photo = None
            if photo is not None and photo[:2] != b"\xff\xd8":
                photo = None
        chunks.append(vcard_module.contact_to_vcard(record,
                                                    photo_bytes=photo))
    return "".join(chunks)


def export_groups_json(records) -> str:
    """Serialize group memberships in the Android ``groups.json`` shape."""
    table: dict[str, list[str]] = {}
    for record in records or ():
        uid = getattr(record, "uid", "")
        for name in getattr(record, "groups", ()) or ():
            cleaned = (name or "").strip()
            if cleaned:
                table.setdefault(cleaned, []).append(uid)
    payload = [{"title": title,
                "members": sorted(table[title])}
               for title in sorted(table)]
    return json.dumps(payload)
