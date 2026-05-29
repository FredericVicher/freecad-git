"""Integration stress test: full chain over multiple modify-commit cycles.

    & "C:\\Program Files\\FreeCAD 1.x\\bin\\python.exe" test_stress.py path/to/file.FCStd
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

import FreeCAD  # type: ignore[import-not-found]

from freecad_git import cache, detector, reconciler
from freecad_git.git_store import Author, GitStore


AUTHOR = Author("Fred", "frederic.vicher@laposte.net")


def odb_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def commit_doc(store: GitStore, cache_path: Path, message: str) -> str:
    """Extract from cache + commit to store. Returns (oid, payload_size)."""
    doc = FreeCAD.openDocument(str(cache_path))
    try:
        classifications = detector.classify_document(doc)
    finally:
        FreeCAD.closeDocument(doc.Name)

    imported_names = [c.name for c in detector.imported_objects(classifications)]
    xml = cache.read_document_xml(cache_path)
    blobs = cache.read_files_for(cache_path, imported_names)
    oid = store.commit(document_xml=xml, blobs=blobs, message=message, author=AUTHOR)
    return oid


def reconcile_between(store: GitStore, old_oid: str, new_oid: str,
                     old_files: dict[str, bytes], new_files: dict[str, bytes]) -> list:
    old_xml = store.document_xml_at(old_oid)
    new_xml = store.document_xml_at(new_oid)
    return reconciler.diff_documents(old_xml, new_xml, old_files, new_files)


def pick_target(doc, type_filter: str | None = None):
    """Pick a parametric object with a Placement, optionally filtered by TypeId."""
    for obj in doc.Objects:
        if "Placement" not in obj.PropertiesList:
            continue
        if detector.classify(obj) is not detector.Kind.COMPUTED:
            continue
        if type_filter and type_filter not in obj.TypeId:
            continue
        return obj
    return None


def step(label: str):
    print(f"\n=== {label} ===")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    src = Path(argv[1]).resolve()
    work = Path(tempfile.mkdtemp(prefix="freecad-git-stress-"))
    repo = work / "repo"
    cache_fc = work / "cache.FCStd"
    store = GitStore(repo)

    print(f"workdir: {work}")
    print(f"source : {src.name} ({src.stat().st_size:,} bytes)")

    # ---------- normalize baseline ----------
    step("Step 0: normalize baseline (open + saveAs without modif)")
    doc = FreeCAD.openDocument(str(src))
    doc.saveAs(str(cache_fc))
    FreeCAD.closeDocument(doc.Name)
    print(f"  baseline cache: {cache_fc.stat().st_size:,} bytes")

    history: list[tuple[str, str]] = []  # (label, oid)
    # Per-commit snapshots: full file set (for inter-commit reconciliation)
    # plus imported-only subset (what's actually stored in git).
    full_snaps: dict[str, tuple[bytes, dict[str, bytes]]] = {}
    git_snaps: dict[str, tuple[bytes, dict[str, bytes]]] = {}

    def snapshot_cache() -> tuple[tuple[bytes, dict[str, bytes]],
                                   tuple[bytes, dict[str, bytes]]]:
        """Return (full, imported-only) snapshots of the current cache."""
        xml = cache.read_document_xml(cache_fc)
        all_names = {r.object_name for r in cache.list_object_files(cache_fc)}
        full = cache.read_files_for(cache_fc, all_names)
        doc = FreeCAD.openDocument(str(cache_fc))
        try:
            cls = detector.classify_document(doc)
        finally:
            FreeCAD.closeDocument(doc.Name)
        imported_names = {c.name for c in detector.imported_objects(cls)}
        imported_only = cache.read_files_for(cache_fc, imported_names)
        return (xml, full), (xml, imported_only)

    # ---------- commit 1: baseline ----------
    step("Step 1: commit baseline")
    t0 = time.perf_counter()
    oid1 = commit_doc(store, cache_fc, "baseline")
    print(f"  oid: {oid1[:12]}  ({time.perf_counter()-t0:.2f}s)  ODB: {odb_size(repo):,} bytes")
    history.append(("baseline", oid1))
    full_snaps[oid1], git_snaps[oid1] = snapshot_cache()

    # ---------- commit 2: modify a Body Placement ----------
    step("Step 2: modify a Body.Placement.x, recompute, save, commit")
    target_name = None
    target_type = None
    doc = FreeCAD.openDocument(str(cache_fc))
    try:
        target = pick_target(doc, type_filter="PartDesign::Body")
        if not target:
            target = pick_target(doc)
        target_name = target.Name
        target_type = target.TypeId
        print(f"  target: {target_name} ({target_type})")
        target.Placement.Base.x += 5.0
        doc.recompute()
        doc.saveAs(str(cache_fc))
    finally:
        FreeCAD.closeDocument(doc.Name)
    oid2 = commit_doc(store, cache_fc, "shift body +5mm")
    print(f"  oid: {oid2[:12]}  ODB: {odb_size(repo):,} bytes")
    history.append(("shift body", oid2))
    full_snaps[oid2], git_snaps[oid2] = snapshot_cache()

    # ---------- commit 3: modify the same body again ----------
    step("Step 3: shift the same body again, save, commit")
    doc = FreeCAD.openDocument(str(cache_fc))
    try:
        target = doc.getObject(target_name)
        target.Placement.Base.y += 10.0
        doc.recompute()
        doc.saveAs(str(cache_fc))
    finally:
        FreeCAD.closeDocument(doc.Name)
    oid3 = commit_doc(store, cache_fc, "shift body +10mm in Y")
    print(f"  oid: {oid3[:12]}  ODB: {odb_size(repo):,} bytes")
    history.append(("shift body Y", oid3))
    full_snaps[oid3], git_snaps[oid3] = snapshot_cache()

    # ---------- commit 4: NO-OP save (edge case) ----------
    step("Step 4: open + saveAs without modification (edge case)")
    doc = FreeCAD.openDocument(str(cache_fc))
    doc.saveAs(str(cache_fc))
    FreeCAD.closeDocument(doc.Name)
    oid4 = commit_doc(store, cache_fc, "no-op save")
    print(f"  oid: {oid4[:12]}  ODB: {odb_size(repo):,} bytes")
    history.append(("no-op", oid4))
    full_snaps[oid4], git_snaps[oid4] = snapshot_cache()

    # ---------- reconciliations between consecutive commits ----------
    step("Step 5: reconciliation between consecutive commits (using full files)")
    for (la, oa), (lb, ob) in zip(history, history[1:]):
        old_xml, old_files = full_snaps[oa]
        new_xml, new_files = full_snaps[ob]
        deltas = reconciler.diff_documents(old_xml, new_xml, old_files, new_files)
        touched = reconciler.names_to_touch(deltas)
        file_chg = reconciler.files_to_refresh(deltas)
        print(f"  {la:18s} -> {lb:18s}: {len(deltas):3d} object(s) changed, "
              f"touch={len(touched)}, file_chg={len(file_chg)}")
        if 0 < len(deltas) <= 10:
            for d in deltas:
                flags = []
                if d.added: flags.append("ADD")
                if d.removed: flags.append("DEL")
                if d.properties_changed: flags.append("props")
                if d.file_changes: flags.append(f"files({len(d.file_changes)})")
                print(f"     - [{','.join(flags):20s}] {d.name} ({d.type_id})")
        elif len(deltas) == 0:
            print("     (clean: cache reuse possible for everything)")

    # ---------- roundtrip: read back every commit, compare ----------
    step("Step 6: roundtrip read of each commit vs stored git-snapshot")
    all_ok = True
    for label, oid in history:
        xml_back, blobs_back = store.read_tree_at(oid)
        xml_stored, blobs_stored = git_snaps[oid]
        xml_ok = xml_back == xml_stored
        blobs_ok = blobs_back == dict(blobs_stored)
        print(f"  {label:18s} ({oid[:8]}): xml={xml_ok}  blobs={blobs_ok}  "
              f"({len(blobs_back)} blob(s))")
        if not (xml_ok and blobs_ok):
            all_ok = False
    assert all_ok, "roundtrip mismatch detected"

    # ---------- final summary ----------
    step("Step 7: final summary")
    print(f"  source .FCStd:    {src.stat().st_size:>10,} bytes")
    print(f"  baseline cache:   {cache_fc.stat().st_size:>10,} bytes")
    print(f"  ODB ({len(history)} commits): {odb_size(repo):>10,} bytes")

    del store
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
