"""End-to-end test: detector -> cache -> git_store -> readback.

    & "C:\\Program Files\\FreeCAD 1.x\\bin\\python.exe" test_roundtrip.py path/to/file.FCStd
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

import FreeCAD  # type: ignore[import-not-found]

from freecad_git import cache, detector
from freecad_git.git_store import Author, GitStore


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def odb_size(repo_path: Path) -> int:
    return sum(p.stat().st_size for p in repo_path.rglob("*") if p.is_file())


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    fcstd = Path(argv[1]).resolve()
    if not fcstd.is_file():
        print(f"file not found: {fcstd}")
        return 1

    repo_path = Path(tempfile.mkdtemp(prefix="freecad-git-test-"))
    print(f"repo path: {repo_path}")
    author = Author("Fred", "frederic.vicher@laposte.net")
    store = GitStore(repo_path)

    # ---- 1. Classify + extract ----
    doc = FreeCAD.openDocument(str(fcstd))
    try:
        classifications = detector.classify_document(doc)
    finally:
        FreeCAD.closeDocument(doc.Name)
    imported_names = [c.name for c in detector.imported_objects(classifications)]
    print(f"classified: {len(imported_names)} imported object(s)")

    xml = cache.read_document_xml(fcstd)
    blobs = cache.read_files_for(fcstd, imported_names)
    print(f"extracted:  Document.xml ({len(xml)} bytes), {len(blobs)} blob(s)")

    # ---- 2. First commit ----
    c1 = store.commit(
        document_xml=xml, blobs=blobs,
        message="Initial commit", author=author,
    )
    print(f"\ncommit 1: {c1}")
    print(f"  ODB size after commit 1: {odb_size(repo_path):>10d} bytes")

    # ---- 3. Read back, verify byte-perfect roundtrip ----
    xml_back, blobs_back = store.read_tree_at()
    assert xml_back == xml, "Document.xml roundtrip mismatch"
    assert blobs_back == dict(blobs), "blob roundtrip mismatch"
    print(f"  roundtrip OK: xml sha={sha256(xml)}, {len(blobs_back)} blob(s) match")

    # ---- 4. Re-commit identical content -> verify dedup ----
    c2 = store.commit(
        document_xml=xml, blobs=blobs,
        message="Idempotent re-commit", author=author,
    )
    print(f"\ncommit 2 (identical content): {c2}")
    print(f"  ODB size after commit 2: {odb_size(repo_path):>10d} bytes")
    parent_oid = str(store.repo[c2].parents[0].id)
    print(f"  parent of commit 2: {parent_oid} (should be {c1})")
    assert parent_oid == c1, "commit 2 should chain from commit 1"

    # ---- 5. Modify Document.xml -> dedup test on .brp ----
    modified_xml = xml + b"\n<!-- noop edit -->"
    c3 = store.commit(
        document_xml=modified_xml, blobs=blobs,
        message="Edit Document.xml only", author=author,
    )
    print(f"\ncommit 3 (only Document.xml changed): {c3}")
    print(f"  ODB size after commit 3: {odb_size(repo_path):>10d} bytes")

    changed = store.diff_paths_between(c2, c3)
    print(f"  diff_paths(c2, c3): {changed}")
    assert changed == ["Document.xml"], "expected only Document.xml to differ"

    # ---- 6. Summary ----
    print(f"\nfull .FCStd source size: {fcstd.stat().st_size} bytes")
    print(f"ODB final size (3 commits, blobs deduped): {odb_size(repo_path)} bytes")

    # libgit2 keeps file handles on Windows; release then best-effort cleanup.
    del store
    shutil.rmtree(repo_path, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
