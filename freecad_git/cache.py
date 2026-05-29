"""Read entries from a .FCStd cache.

A .FCStd is a zip archive containing Document.xml plus one or more files
per object (typically `{ObjectName}.{PropertyName}.brp` and matching
`.Map.txt` for element naming). The mapping object -> file is encoded in
Document.xml itself via `<Property name="X"><Part file="Y"/></Property>`,
which is the authoritative source we parse here.

Used by:
    * commit path -- extract .brp bytes for IMPORTED objects to commit
    * pull path   -- check whether a cached .brp can be reused as-is or
                     whether the object needs touch()/recompute()
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ObjectFile:
    object_name: str       # obj.Name as found in Document.xml
    property_name: str     # e.g. "Shape", "InternalShape", "AddSubShape"
    file_name: str         # entry name within the .FCStd zip


def read_document_xml(fcstd_path: str | Path) -> bytes:
    """Return Document.xml from the .FCStd archive as raw bytes."""
    with zipfile.ZipFile(fcstd_path) as z:
        return z.read("Document.xml")


def parse_object_files(xml_bytes: bytes) -> list[ObjectFile]:
    """Return every (object, property, file) reference declared in a Document.xml.

    Only properties whose XML body carries a `file="..."` attribute on a
    direct child element are included (this is FreeCAD's encoding for
    Part/Mesh/Points shape properties that spill to a side file).
    """
    root = ET.fromstring(xml_bytes)
    refs: list[ObjectFile] = []
    for obj in root.iter("Object"):
        name = obj.get("name")
        if not name:
            continue  # the top-level <Objects><Object .../> index has no body
        for prop in obj.iter("Property"):
            prop_name = prop.get("name") or ""
            for child in prop:
                file_attr = child.get("file")
                if file_attr:
                    refs.append(ObjectFile(name, prop_name, file_attr))
                    break
    return refs


def list_object_files(fcstd_path: str | Path) -> list[ObjectFile]:
    """Same as parse_object_files, but reads Document.xml from a .FCStd."""
    return parse_object_files(read_document_xml(fcstd_path))


def read_files_for(
    fcstd_path: str | Path,
    object_names: Iterable[str],
) -> dict[str, bytes]:
    """Return {entry_name -> bytes} for every file owned by the given objects.

    Empty entries (e.g. unused InternalShape.brp at 0 bytes) are kept; the
    caller decides whether to skip them.
    """
    wanted = set(object_names)
    if not wanted:
        return {}
    refs = [r for r in list_object_files(fcstd_path) if r.object_name in wanted]
    if not refs:
        return {}
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(fcstd_path) as z:
        for ref in refs:
            out[ref.file_name] = z.read(ref.file_name)
    return out
