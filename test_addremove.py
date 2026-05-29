"""Stress add/remove of objects through the full pipeline.

Scenarios tested:
    1. add a computed primitive (Part::Box)
    2. add an imported geometry (synthetic .brp via Part.read or shape import)
    3. delete the imported geometry
    4. delete the computed primitive

    & "C:\\Program Files\\FreeCAD 1.x\\bin\\python.exe" test_addremove.py path/to/file.FCStd
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import FreeCAD  # type: ignore[import-not-found]
import Part     # type: ignore[import-not-found]

from freecad_git import cache, detector, reconciler
from freecad_git.git_store import Author, GitStore


AUTHOR = Author("Fred", "frederic.vicher@laposte.net")


def commit_doc(store: GitStore, cache_path: Path, message: str) -> str:
    doc = FreeCAD.openDocument(str(cache_path))
    try:
        classifications = detector.classify_document(doc)
    finally:
        FreeCAD.closeDocument(doc.Name)
    imported_names = [c.name for c in detector.imported_objects(classifications)]
    xml = cache.read_document_xml(cache_path)
    blobs = cache.read_files_for(cache_path, imported_names)
    return store.commit(
        document_xml=xml, blobs=blobs, message=message, author=AUTHOR,
    )


def snapshot(cache_path: Path) -> tuple[bytes, dict[str, bytes]]:
    xml = cache.read_document_xml(cache_path)
    names = {r.object_name for r in cache.list_object_files(cache_path)}
    return xml, cache.read_files_for(cache_path, names)


def show_deltas(label_old: str, label_new: str, deltas):
    print(f"\n  {label_old:20s} -> {label_new:20s}: {len(deltas)} change(s)")
    for d in deltas:
        flags = []
        if d.added: flags.append("ADD")
        if d.removed: flags.append("DEL")
        if d.properties_changed and not (d.added or d.removed): flags.append("props")
        if d.file_changes: flags.append(f"files({len(d.file_changes)})")
        print(f"    [{','.join(flags):20s}] {d.name} ({d.type_id})")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    src = Path(argv[1]).resolve()
    work = Path(tempfile.mkdtemp(prefix="freecad-git-addrem-"))
    cache_fc = work / "cache.FCStd"
    store = GitStore(work / "repo")

    print(f"workdir: {work}")
    print(f"source : {src.name}")

    # ---- baseline ----
    doc = FreeCAD.openDocument(str(src))
    doc.saveAs(str(cache_fc))
    FreeCAD.closeDocument(doc.Name)
    snap0 = snapshot(cache_fc)
    oid0 = commit_doc(store, cache_fc, "baseline")
    print(f"baseline commit: {oid0[:12]}")

    # ---- 1. ADD a computed primitive (Part::Box) ----
    print("\n=== ADD computed primitive (Part::Box) ===")
    doc = FreeCAD.openDocument(str(cache_fc))
    try:
        box = doc.addObject("Part::Box", "AddedBox")
        box.Length = 20.0
        box.Width = 30.0
        box.Height = 40.0
        added_box_name = box.Name
        doc.recompute()
        doc.saveAs(str(cache_fc))
    finally:
        FreeCAD.closeDocument(doc.Name)
    snap1 = snapshot(cache_fc)
    oid1 = commit_doc(store, cache_fc, f"add {added_box_name}")
    print(f"  commit: {oid1[:12]}")
    deltas = reconciler.diff_documents(snap0[0], snap1[0], snap0[1], snap1[1])
    show_deltas("baseline", "+box", deltas)

    # ---- 2. ADD an imported geometry (raw Part::Feature) ----
    print("\n=== ADD imported geometry (raw Part::Feature with a Shape) ===")
    doc = FreeCAD.openDocument(str(cache_fc))
    try:
        # Build a shape outside of any parametric feature, then bake it into
        # a bare Part::Feature -- this is what FreeCAD does for STEP imports.
        shape = Part.makeSphere(15.0)
        feature = doc.addObject("Part::Feature", "ImportedSphere")
        feature.Shape = shape
        added_feat_name = feature.Name
        doc.recompute()
        doc.saveAs(str(cache_fc))
    finally:
        FreeCAD.closeDocument(doc.Name)
    snap2 = snapshot(cache_fc)
    oid2 = commit_doc(store, cache_fc, f"add {added_feat_name}")
    print(f"  commit: {oid2[:12]}")
    deltas = reconciler.diff_documents(snap1[0], snap2[0], snap1[1], snap2[1])
    show_deltas("+box", "+box+feature", deltas)

    # Verify the new imported feature is in the git tree as a blob
    _, blobs_in_git = store.read_tree_at(oid2)
    print(f"  blobs in git after commit: {sorted(blobs_in_git)}")
    feature_was_committed = any(added_feat_name in b for b in blobs_in_git)
    print(f"  ImportedSphere .brp committed? {feature_was_committed}")

    # ---- 3. DELETE the imported geometry ----
    print("\n=== DELETE imported geometry ===")
    doc = FreeCAD.openDocument(str(cache_fc))
    try:
        doc.removeObject(added_feat_name)
        doc.recompute()
        doc.saveAs(str(cache_fc))
    finally:
        FreeCAD.closeDocument(doc.Name)
    snap3 = snapshot(cache_fc)
    oid3 = commit_doc(store, cache_fc, f"remove {added_feat_name}")
    print(f"  commit: {oid3[:12]}")
    deltas = reconciler.diff_documents(snap2[0], snap3[0], snap2[1], snap3[1])
    show_deltas("+box+feature", "+box", deltas)

    _, blobs_in_git = store.read_tree_at(oid3)
    print(f"  blobs in git after deletion: {sorted(blobs_in_git)}")
    print(f"  ImportedSphere still in git? "
          f"{any(added_feat_name in b for b in blobs_in_git)}")

    # ---- 4. DELETE the computed primitive ----
    print("\n=== DELETE computed primitive ===")
    doc = FreeCAD.openDocument(str(cache_fc))
    try:
        doc.removeObject(added_box_name)
        doc.recompute()
        doc.saveAs(str(cache_fc))
    finally:
        FreeCAD.closeDocument(doc.Name)
    snap4 = snapshot(cache_fc)
    oid4 = commit_doc(store, cache_fc, f"remove {added_box_name}")
    print(f"  commit: {oid4[:12]}")
    deltas = reconciler.diff_documents(snap3[0], snap4[0], snap3[1], snap4[1])
    show_deltas("+box", "baseline", deltas)

    # ---- 5. Verify final state matches baseline (after detected diff) ----
    print("\n=== Final state vs baseline ===")
    deltas = reconciler.diff_documents(snap0[0], snap4[0], snap0[1], snap4[1])
    print(f"  end-to-end diff: {len(deltas)} object(s) (should reflect doc-level metadata only)")

    del store
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
