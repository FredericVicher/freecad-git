"""Classify FreeCAD document objects as computed vs imported.

The git workflow needs to know which objects carry stored geometry that must
be persisted on disk (imported BREPs, meshes) versus those that can be
recomputed from parameters and dependencies (sketches, primitives, booleans).

Classification relies on two FreeCAD-native signals:
    * obj.TypeId             -- the C++/Python type registered for the object
    * obj.InList / obj.OutList -- the document dependency graph
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class Kind(str, Enum):
    IMPORTED = "imported"    # raw stored geometry, must be tracked by git
    COMPUTED = "computed"    # derived from parameters and upstream objects
    CONTAINER = "container"  # organizational only, no geometry
    UNKNOWN = "unknown"


# TypeIds whose instances carry raw geometry that cannot be regenerated.
IMPORTED_TYPE_IDS = frozenset({
    "Part::Feature",       # base class used for STEP/IGES/BREP imports
    "Mesh::Feature",       # STL/OBJ/PLY imports
    "Points::Feature",     # imported point clouds
    "Image::ImagePlane",   # imported raster planes
})

# Prefixes that indicate parametric/computed objects. The bare base classes
# listed in IMPORTED_TYPE_IDS are checked first and won't fall into these.
COMPUTED_TYPE_PREFIXES = (
    "Part::",
    "PartDesign::",
    "Sketcher::",
    "Draft::",
    "Arch::",
    "Surface::",
    "Mesh::",
    "Points::",
    "Path::",
    "Fem::",
    "TechDraw::",
    "Spreadsheet::",
)

# Pure container types: organizational nodes with no geometry of their own.
CONTAINER_TYPE_IDS = frozenset({
    "App::Part",
    "App::DocumentObjectGroup",
    "App::Origin",
    "App::Plane",
    "App::Line",
    "App::Point",
    "App::LocalCoordinateSystem",
    "App::Link",
    "App::LinkGroup",
})


@dataclass(frozen=True)
class Classification:
    name: str                    # obj.Name (unique internal id)
    label: str                   # obj.Label (user-facing)
    type_id: str                 # obj.TypeId
    kind: Kind
    out_list: tuple[str, ...]    # names of objects this one depends on
    in_list: tuple[str, ...]     # names of objects that depend on this one

    @property
    def is_root(self) -> bool:
        """True if nothing else in the document depends on this object."""
        return not self.in_list

    @property
    def is_source(self) -> bool:
        """True if this object has no upstream dependencies."""
        return not self.out_list


def classify(obj) -> Kind:
    """Classify a single FreeCAD DocumentObject by its TypeId.

    Part::Feature is special-cased: if it carries an OutList it is in fact
    a derived feature (rare, but the FreeCAD API allows it) and is treated
    as computed.
    """
    type_id = getattr(obj, "TypeId", "") or ""

    if type_id in CONTAINER_TYPE_IDS:
        return Kind.CONTAINER

    if type_id in IMPORTED_TYPE_IDS:
        if type_id == "Part::Feature" and getattr(obj, "OutList", None):
            return Kind.COMPUTED
        return Kind.IMPORTED

    for prefix in COMPUTED_TYPE_PREFIXES:
        if type_id.startswith(prefix):
            return Kind.COMPUTED

    return Kind.UNKNOWN


def classify_document(doc) -> list[Classification]:
    """Return a Classification for every object in the document."""
    results: list[Classification] = []
    for obj in doc.Objects:
        out_names = tuple(o.Name for o in (obj.OutList or ()))
        in_names = tuple(o.Name for o in (obj.InList or ()))
        results.append(Classification(
            name=obj.Name,
            label=obj.Label,
            type_id=obj.TypeId,
            kind=classify(obj),
            out_list=out_names,
            in_list=in_names,
        ))
    return results


def imported_objects(classifications: Iterable[Classification]) -> list[Classification]:
    """Imported objects whose .brp file must be committed."""
    return [c for c in classifications if c.kind is Kind.IMPORTED]


def computed_objects(classifications: Iterable[Classification]) -> list[Classification]:
    """Computed objects; their geometry is regenerated on pull."""
    return [c for c in classifications if c.kind is Kind.COMPUTED]


def format_report(classifications: Iterable[Classification]) -> str:
    """Human-readable summary suitable for CLI / log output."""
    by_kind: dict[Kind, list[Classification]] = {k: [] for k in Kind}
    for c in classifications:
        by_kind[c.kind].append(c)

    lines: list[str] = []
    for kind in Kind:
        items = by_kind[kind]
        if not items:
            continue
        lines.append(f"[{kind.value}] {len(items)} object(s):")
        for c in items:
            deps = f"  <- {', '.join(c.out_list)}" if c.out_list else ""
            lines.append(f"  {c.label} ({c.name} : {c.type_id}){deps}")
    return "\n".join(lines)
