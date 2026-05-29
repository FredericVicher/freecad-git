"""Real-world reconciler test: modify a parameter, save, diff.

    & "C:\\Program Files\\FreeCAD 1.x\\bin\\python.exe" test_reconcile.py path/to/file.FCStd
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import FreeCAD  # type: ignore[import-not-found]

from freecad_git import cache, detector, reconciler


def pick_modifiable_object(doc):
    """Return any parametric object whose Placement we can nudge."""
    for obj in doc.Objects:
        if "Placement" in obj.PropertiesList and detector.classify(obj) is detector.Kind.COMPUTED:
            return obj
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    src = Path(argv[1]).resolve()
    if not src.is_file():
        print(f"file not found: {src}")
        return 1

    work = Path(tempfile.mkdtemp(prefix="freecad-git-recon-"))
    old_path = work / "old.FCStd"
    new_path = work / "new.FCStd"

    # ---- baseline: open + immediately saveAs (no modification) ----
    # This normalizes byte-level non-determinism from the source's prior save.
    doc = FreeCAD.openDocument(str(src))
    doc.saveAs(str(old_path))
    FreeCAD.closeDocument(doc.Name)

    # ---- variant: open the baseline, modify one parameter, saveAs ----
    doc = FreeCAD.openDocument(str(old_path))
    try:
        target = pick_modifiable_object(doc)
        if target is None:
            print("no modifiable parametric object found in document")
            return 3
        original_x = target.Placement.Base.x
        target.Placement.Base.x = original_x + 1.0
        print(f"modified: {target.Name} ({target.TypeId}) Placement.x: "
              f"{original_x} -> {target.Placement.Base.x}")
        doc.recompute()
        doc.saveAs(str(new_path))
    finally:
        FreeCAD.closeDocument(doc.Name)

    # ---- Read both sides ----
    old_xml = cache.read_document_xml(old_path)
    new_xml = cache.read_document_xml(new_path)
    old_refs = cache.list_object_files(old_path)
    new_refs = cache.list_object_files(new_path)
    old_files = cache.read_files_for(old_path, {r.object_name for r in old_refs})
    new_files = cache.read_files_for(new_path, {r.object_name for r in new_refs})

    print(f"\nold: {len(old_xml)} bytes XML, {len(old_files)} cache files")
    print(f"new: {len(new_xml)} bytes XML, {len(new_files)} cache files")

    # ---- Reconcile ----
    deltas = reconciler.diff_documents(old_xml, new_xml, old_files, new_files)
    print()
    print(reconciler.format_report(deltas))

    print()
    print(f"objects to touch(): {reconciler.names_to_touch(deltas)}")
    refresh = reconciler.files_to_refresh(deltas)
    print(f"objects with file changes: {list(refresh.keys())}")

    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
