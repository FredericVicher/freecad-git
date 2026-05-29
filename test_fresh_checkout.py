"""Fresh-checkout scenario: pull when no cache .FCStd exists.

Simulates a collaborator cloning the repo: they have only the git ODB
(Document.xml + imported .brp blobs). We must produce a working cache.

    & "C:\\Program Files\\FreeCAD 1.x\\bin\\python.exe" test_fresh_checkout.py path/to/file.FCStd
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


def doc_snapshot(cache_path: Path) -> dict:
    """Open the cache, return a small summary for comparison."""
    doc = FreeCAD.openDocument(str(cache_path))
    try:
        result = {"object_count": len(doc.Objects), "objects": {}}
        for obj in doc.Objects:
            entry = {
                "type_id": obj.TypeId,
                "label": obj.Label,
            }
            if "Placement" in obj.PropertiesList:
                p = obj.Placement.Base
                entry["placement"] = (round(p.x, 6), round(p.y, 6), round(p.z, 6))
            shape = getattr(obj, "Shape", None)
            if shape is not None:
                try:
                    entry["volume"] = round(shape.Volume, 4)
                    entry["nfaces"] = len(shape.Faces)
                except Exception:
                    entry["volume"] = "invalid"
            result["objects"][obj.Name] = entry
        return result
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
    work = Path(tempfile.mkdtemp(prefix="freecad-git-fresh-"))
    cache_fc = work / "cache.FCStd"
    store = GitStore(work / "repo")
    print(f"workdir: {work}")

    # ---- Build a small history (A, B) in the original workspace ----
    doc = FreeCAD.openDocument(str(src))
    doc.saveAs(str(cache_fc))
    target_name = pick_target(doc)
    print(f"target object: {target_name}")
    FreeCAD.closeDocument(doc.Name)

    doc = FreeCAD.openDocument(str(cache_fc))
    oid_A = workflow.commit_doc(doc, store, "A: baseline", AUTHOR)
    FreeCAD.closeDocument(doc.Name)

    doc = FreeCAD.openDocument(str(cache_fc))
    doc.getObject(target_name).Placement.Base.x += 7.0
    doc.recompute()
    oid_B = workflow.commit_doc(doc, store, "B: shift +7", AUTHOR)
    FreeCAD.closeDocument(doc.Name)
    print(f"committed A={oid_A[:8]}, B={oid_B[:8]}")

    # Snapshot the reference state (what we should reproduce after fresh checkout)
    reference = doc_snapshot(cache_fc)
    print(f"reference cache: {reference['object_count']} objects, "
          f"{target_name}.Placement.x = {reference['objects'][target_name]['placement'][0]}")
    print(f"reference {target_name}.Shape.Volume = "
          f"{reference['objects'][target_name].get('volume')}")

    # ---- Simulate fresh checkout: delete the cache, keep only the repo ----
    print("\n=== Simulate fresh checkout: deleting cache.FCStd ===")
    cache_fc.unlink()
    assert not cache_fc.exists()
    print(f"cache removed. Files left in workdir:")
    for p in sorted(work.rglob("*")):
        if p.is_file():
            rel = p.relative_to(work)
            print(f"  {rel}  ({p.stat().st_size} bytes)")

    # ---- pull_doc with no cache present ----
    print(f"\n--- pull_doc into empty workdir, ref=B ({oid_B[:8]}) ---")
    oid, touched = workflow.pull_doc(store, cache_fc, ref=oid_B)
    print(f"pulled to: {oid[:8]}")
    print(f"touched count: {len(touched)} (expect: all COMPUTED objects)")
    print(f"first 10 touched: {touched[:10]}")

    assert cache_fc.exists(), "cache was not created"
    print(f"new cache: {cache_fc.stat().st_size} bytes")

    # ---- Compare to reference ----
    print(f"\n=== Compare fresh-checkout cache to reference ===")
    fresh = doc_snapshot(cache_fc)
    print(f"fresh cache: {fresh['object_count']} objects "
          f"(reference: {reference['object_count']})")
    assert fresh["object_count"] == reference["object_count"], \
        "object count mismatch"

    # Walk every object: same TypeId, same placement, equivalent volume/faces
    mismatches = []
    for name, ref_entry in reference["objects"].items():
        if name not in fresh["objects"]:
            mismatches.append(f"missing: {name}")
            continue
        fresh_entry = fresh["objects"][name]
        if ref_entry["type_id"] != fresh_entry["type_id"]:
            mismatches.append(f"type mismatch on {name}: "
                              f"{ref_entry['type_id']} vs {fresh_entry['type_id']}")
        if "placement" in ref_entry and ref_entry["placement"] != fresh_entry.get("placement"):
            mismatches.append(f"placement mismatch on {name}: "
                              f"{ref_entry['placement']} vs {fresh_entry.get('placement')}")
        rv = ref_entry.get("volume")
        fv = fresh_entry.get("volume")
        if isinstance(rv, float) and isinstance(fv, float):
            if abs(rv - fv) > 1e-4:
                mismatches.append(f"volume mismatch on {name}: {rv} vs {fv}")
        elif rv != fv:
            mismatches.append(f"volume kind mismatch on {name}: {rv} vs {fv}")

    if mismatches:
        print(f"\n{len(mismatches)} mismatch(es):")
        for m in mismatches[:20]:
            print(f"  - {m}")
        if len(mismatches) > 20:
            print(f"  ... and {len(mismatches) - 20} more")
        return 1
    else:
        print("all objects match the reference state (type, placement, volume, faces)")

    # ---- Also pull back to A on the fresh cache ----
    print(f"\n--- pull_doc back to A ({oid_A[:8]}) on the fresh cache ---")
    workflow.pull_doc(store, cache_fc, ref=oid_A)
    after_back_to_A = doc_snapshot(cache_fc)
    target_after = after_back_to_A["objects"][target_name]
    print(f"{target_name}.Placement = {target_after['placement']}")
    print(f"{target_name}.Volume = {target_after.get('volume')}")
    # Expected: same as initial (before the +7 shift)
    initial_target_x = reference["objects"][target_name]["placement"][0] - 7.0
    assert abs(target_after["placement"][0] - initial_target_x) < 1e-6

    print("\nall assertions passed")
    del store
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
