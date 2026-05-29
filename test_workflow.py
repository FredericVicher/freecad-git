"""End-to-end workflow test: commit, then pull back to an earlier commit.

    & "C:\\Program Files\\FreeCAD 1.x\\bin\\python.exe" test_workflow.py path/to/file.FCStd
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import FreeCAD  # type: ignore[import-not-found]

from freecad_git import detector, workflow
from freecad_git.git_store import Author, GitStore


AUTHOR = Author("Fred", "frederic.vicher@laposte.net")


def get_placement_x(cache_path: Path, name: str) -> float:
    doc = FreeCAD.openDocument(str(cache_path))
    try:
        obj = doc.getObject(name)
        return obj.Placement.Base.x if obj else float("nan")
    finally:
        FreeCAD.closeDocument(doc.Name)


def pick_target(doc) -> str | None:
    for obj in doc.Objects:
        if "Placement" not in obj.PropertiesList:
            continue
        if detector.classify(obj) is not detector.Kind.COMPUTED:
            continue
        if "PartDesign::Body" in obj.TypeId:
            return obj.Name
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    src = Path(argv[1]).resolve()
    work = Path(tempfile.mkdtemp(prefix="freecad-git-wflow-"))
    cache_fc = work / "cache.FCStd"
    store = GitStore(work / "repo")
    print(f"workdir: {work}")

    # ---- baseline ----
    doc = FreeCAD.openDocument(str(src))
    doc.saveAs(str(cache_fc))
    target_name = pick_target(doc)
    print(f"target object: {target_name}")
    initial_x = doc.getObject(target_name).Placement.Base.x
    FreeCAD.closeDocument(doc.Name)
    print(f"initial Placement.x = {initial_x}")

    # ---- commit A: baseline ----
    doc = FreeCAD.openDocument(str(cache_fc))
    oid_A = workflow.commit_doc(doc, store, "A: baseline", AUTHOR)
    FreeCAD.closeDocument(doc.Name)
    print(f"commit A: {oid_A[:12]}  (Placement.x = {initial_x})")

    # ---- commit B: shift +5 ----
    doc = FreeCAD.openDocument(str(cache_fc))
    doc.getObject(target_name).Placement.Base.x = initial_x + 5.0
    doc.recompute()
    oid_B = workflow.commit_doc(doc, store, "B: shift +5", AUTHOR)
    FreeCAD.closeDocument(doc.Name)
    print(f"commit B: {oid_B[:12]}  (Placement.x = {initial_x + 5.0})")

    # ---- commit C: shift to +12 ----
    doc = FreeCAD.openDocument(str(cache_fc))
    doc.getObject(target_name).Placement.Base.x = initial_x + 12.0
    doc.recompute()
    oid_C = workflow.commit_doc(doc, store, "C: shift to +12", AUTHOR)
    FreeCAD.closeDocument(doc.Name)
    print(f"commit C: {oid_C[:12]}  (Placement.x = {initial_x + 12.0})")

    # Verify we're at C right now
    x_now = get_placement_x(cache_fc, target_name)
    print(f"\ncache after C: Placement.x = {x_now}  (expected {initial_x + 12.0})")
    assert abs(x_now - (initial_x + 12.0)) < 1e-9

    # ---- PULL back to A ----
    print(f"\n--- pull_doc back to commit A ({oid_A[:12]}) ---")
    oid, touched = workflow.pull_doc(store, cache_fc, ref=oid_A)
    print(f"pulled to: {oid[:12]}")
    print(f"touched: {touched}")

    x_after_pull_A = get_placement_x(cache_fc, target_name)
    print(f"cache after pull to A: Placement.x = {x_after_pull_A}  (expected {initial_x})")
    assert abs(x_after_pull_A - initial_x) < 1e-9, "rollback to A failed"

    # ---- PULL forward to C ----
    print(f"\n--- pull_doc forward to commit C ({oid_C[:12]}) ---")
    oid, touched = workflow.pull_doc(store, cache_fc, ref=oid_C)
    print(f"pulled to: {oid[:12]}")
    print(f"touched: {touched}")

    x_after_pull_C = get_placement_x(cache_fc, target_name)
    print(f"cache after pull to C: Placement.x = {x_after_pull_C}  (expected {initial_x + 12.0})")
    assert abs(x_after_pull_C - (initial_x + 12.0)) < 1e-9, "fast-forward to C failed"

    # ---- PULL to A again, then ensure we can commit fresh from there ----
    print(f"\n--- pull back to A, then make a NEW commit D on top ---")
    workflow.pull_doc(store, cache_fc, ref=oid_A)
    doc = FreeCAD.openDocument(str(cache_fc))
    doc.getObject(target_name).Placement.Base.x = initial_x - 3.0
    doc.recompute()
    oid_D = workflow.commit_doc(doc, store, "D: shift -3 from A", AUTHOR)
    FreeCAD.closeDocument(doc.Name)
    print(f"commit D: {oid_D[:12]}  (Placement.x = {initial_x - 3.0})")
    x_at_D = get_placement_x(cache_fc, target_name)
    assert abs(x_at_D - (initial_x - 3.0)) < 1e-9

    # Parent of D should be A (not C), since we pulled back before committing.
    parent_oid = str(store.repo[oid_D].parents[0].id)
    print(f"parent of D: {parent_oid[:12]}  (expected A = {oid_A[:12]})")
    assert parent_oid == oid_A, "D should be a child of A, not C"

    print("\nall assertions passed")
    del store
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
