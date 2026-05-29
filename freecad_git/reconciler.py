"""Reconcile two versions of a FreeCAD document on pull.

After a git pull we have:
    * `old` -- the document state currently in the cache .FCStd
    * `new` -- the state from the pulled commit (Document.xml + tracked .brp)

This module diffs them at the object level so the caller can decide:
    * which .brp files from the old cache can be reused as-is
    * which objects need obj.touch() before doc.recompute()
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ObjectDelta:
    name: str
    type_id: str
    properties_changed: bool        # the <Object> subtree in Document.xml differs
    file_changes: tuple[str, ...]   # entries (in the .FCStd zip) with differing bytes
    added: bool = False
    removed: bool = False

    @property
    def changed(self) -> bool:
        return (
            self.properties_changed
            or bool(self.file_changes)
            or self.added
            or self.removed
        )


def _object_subtrees(xml_bytes: bytes) -> dict[str, ET.Element]:
    """Extract <ObjectData>/<Object name="X"> elements indexed by name."""
    root = ET.fromstring(xml_bytes)
    object_data = root.find("ObjectData")
    if object_data is None:
        return {}
    return {
        obj.get("name"): obj
        for obj in object_data.findall("Object")
        if obj.get("name")
    }


def _object_types(xml_bytes: bytes) -> dict[str, str]:
    """Extract the <Objects><Object name="X" type="Y"/></Objects> index."""
    root = ET.fromstring(xml_bytes)
    index = root.find("Objects")
    if index is None:
        return {}
    return {
        o.get("name"): (o.get("type") or "")
        for o in index.findall("Object")
        if o.get("name")
    }


def _files_referenced(object_elem: ET.Element) -> list[str]:
    """Collect every file="..." reference inside this object's properties."""
    refs: list[str] = []
    for prop in object_elem.iter("Property"):
        for child in prop:
            file_attr = child.get("file")
            if file_attr:
                refs.append(file_attr)
                break
    return refs


def _strip_noise(elem: ET.Element) -> ET.Element:
    """Return a deep copy of `elem` with per-save serialization noise removed.

    FreeCAD bumps a transient "Touched" bit in every Property `status` on
    each save, plus a couple of placeholder tags that don't carry semantic
    information. We strip them so the comparison reflects actual content
    changes rather than save-cycle artefacts.
    """
    clean = copy.deepcopy(elem)

    # ElementMap2 just references a .Map.txt -- the file is already tracked
    # via file_changes, so the inline reference is redundant for this diff.
    for parent in clean.iter():
        for child in list(parent):
            if child.tag == "ElementMap2":
                parent.remove(child)

    for e in clean.iter():
        e.attrib.pop("status", None)        # per-Property transient flag bits
        e.attrib.pop("HasherIndex", None)   # internal hasher index

    # <ElementMap new="1" count="1"><Element key="Dummy"/></ElementMap> is
    # the verbose form of an empty element map; collapse both to <ElementMap/>.
    for em in clean.iter("ElementMap"):
        is_dummy = (
            em.get("new") == "1"
            and len(em) == 1
            and em[0].tag == "Element"
            and em[0].get("key") == "Dummy"
        )
        if is_dummy or len(em) == 0:
            em.clear()

    return clean


def _canon(elem: ET.Element) -> bytes:
    """Stable byte serialization for equality testing.

    Noise attributes/tags are stripped first; FreeCAD's XML serializer is
    otherwise deterministic.
    """
    return ET.tostring(_strip_noise(elem))


def diff_documents(
    old_xml: bytes,
    new_xml: bytes,
    old_files: Mapping[str, bytes] | None = None,
    new_files: Mapping[str, bytes] | None = None,
) -> list[ObjectDelta]:
    """Compute object-level deltas between two FreeCAD document versions.

    `old_files` / `new_files` map .FCStd entry names to bytes. A file
    referenced by an Object whose bytes differ (or is present only on one
    side) is recorded in `file_changes`.

    Objects with no detectable change are omitted from the result.
    """
    old_files = old_files or {}
    new_files = new_files or {}

    old_subs = _object_subtrees(old_xml)
    new_subs = _object_subtrees(new_xml)
    old_types = _object_types(old_xml)
    new_types = _object_types(new_xml)

    deltas: list[ObjectDelta] = []
    for name in sorted(set(old_subs) | set(new_subs)):
        old_elem = old_subs.get(name)
        new_elem = new_subs.get(name)

        if old_elem is None:
            files = _files_referenced(new_elem)
            deltas.append(ObjectDelta(
                name, new_types.get(name, ""),
                properties_changed=True, file_changes=tuple(files), added=True,
            ))
            continue

        if new_elem is None:
            files = _files_referenced(old_elem)
            deltas.append(ObjectDelta(
                name, old_types.get(name, ""),
                properties_changed=True, file_changes=tuple(files), removed=True,
            ))
            continue

        props_changed = _canon(old_elem) != _canon(new_elem)

        refs_old = set(_files_referenced(old_elem))
        refs_new = set(_files_referenced(new_elem))
        file_changes = tuple(sorted(
            f for f in (refs_old | refs_new)
            if old_files.get(f) != new_files.get(f)
        ))

        if props_changed or file_changes:
            deltas.append(ObjectDelta(
                name, new_types.get(name, old_types.get(name, "")),
                properties_changed=props_changed, file_changes=file_changes,
            ))

    return deltas


def names_to_touch(deltas: Iterable[ObjectDelta]) -> list[str]:
    """Objects whose obj.touch() is required before doc.recompute()."""
    return [d.name for d in deltas if d.properties_changed and not d.removed]


def files_to_refresh(deltas: Iterable[ObjectDelta]) -> dict[str, list[str]]:
    """Map object name -> list of .FCStd entries to take from the new cache."""
    return {
        d.name: list(d.file_changes)
        for d in deltas if d.file_changes and not d.removed
    }


def format_report(deltas: Iterable[ObjectDelta]) -> str:
    deltas = list(deltas)
    if not deltas:
        return "no changes"
    lines = [f"{len(deltas)} object(s) changed:"]
    for d in deltas:
        flags = []
        if d.added:
            flags.append("ADDED")
        elif d.removed:
            flags.append("REMOVED")
        else:
            if d.properties_changed:
                flags.append("props")
            if d.file_changes:
                flags.append(f"files({len(d.file_changes)})")
        files_summary = f"  files: {', '.join(d.file_changes)}" if d.file_changes else ""
        lines.append(f"  [{','.join(flags):15s}] {d.name} ({d.type_id}){files_summary}")
    return "\n".join(lines)
