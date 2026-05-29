"""High-level orchestration for the FreeCAD git workflow.

commit_doc(doc, store, message, author)
    Save the FreeCAD document to its cache .FCStd, classify, extract, and
    commit Document.xml plus the .brp of every IMPORTED object.

pull_doc(store, cache_path, ref="HEAD")
    Apply the git commit at `ref`. Document.xml is the authoritative source
    (always from git); .brp files are sourced from git for IMPORTED objects,
    or from the existing cache for COMPUTED objects whose bytes are still
    valid. Objects with no sourceable .brp are touched so FreeCAD's
    recompute() materializes them. Returns (oid, touched_names).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import FreeCAD  # type: ignore[import-not-found]

from . import cache, detector, reconciler
from .git_store import Author, GitStore


def commit_doc(doc, store: GitStore, message: str, author: Author) -> str:
    """Persist the doc to its cache, then commit selected content to git."""
    if not doc.FileName:
        raise ValueError("document has no cache file set; saveAs first")

    doc.save()
    cache_path = Path(doc.FileName)

    classifications = detector.classify_document(doc)
    imported_names = [c.name for c in detector.imported_objects(classifications)]

    xml = cache.read_document_xml(cache_path)
    blobs = cache.read_files_for(cache_path, imported_names)

    return store.commit(
        document_xml=xml, blobs=blobs, message=message, author=author,
    )


def pull_doc(store: GitStore, cache_path: str | Path, ref: str = "HEAD"
             ) -> tuple[str, list[str]]:
    """Pull the commit at `ref` into the cache .FCStd.

    Returns (commit_oid, list_of_touched_object_names).
    """
    cache_path = Path(cache_path)
    target_oid = store.resolve_ref(ref)
    new_xml, git_blobs = store.read_tree_at(target_oid)
    new_refs = cache.parse_object_files(new_xml)
    new_obj_names = {r.object_name for r in new_refs}

    # Read whatever the existing cache (if any) can contribute:
    #   - old Document.xml for the XML-level diff
    #   - .brp files for objects still present in the new doc
    #   - auxiliary entries (GUI metadata, color arrays, StringHasher, ...)
    old_xml: bytes | None = None
    cache_blobs: dict[str, bytes] = {}
    aux_entries: dict[str, bytes] = {}
    if cache_path.exists():
        old_xml = cache.read_document_xml(cache_path)
        old_obj_names = {r.object_name for r in cache.parse_object_files(old_xml)}
        cache_blobs = cache.read_files_for(cache_path, old_obj_names)
        with zipfile.ZipFile(cache_path) as z:
            for n in z.namelist():
                if n == "Document.xml" or n in cache_blobs:
                    continue
                aux_entries[n] = z.read(n)

    # Diff at XML level. Empty when there's no prior cache (fresh checkout).
    deltas = reconciler.diff_documents(old_xml, new_xml) if old_xml else []
    changed_owners = {
        d.name for d in deltas
        if not d.removed and (d.properties_changed or d.added)
    }

    # Assemble final .brp set. Objects flagged as changed are not sourced
    # from the cache: their cached .brp encodes the previous geometry
    # (e.g. an old Placement) which FreeCAD would otherwise honour over
    # the new Document.xml when loading. They will be materialized by
    # recompute() below.
    final_blobs: dict[str, bytes] = {}
    missing_brp_owners: set[str] = set()
    for r in new_refs:
        if r.file_name in git_blobs:
            final_blobs[r.file_name] = git_blobs[r.file_name]
        elif r.object_name in changed_owners:
            missing_brp_owners.add(r.object_name)
        elif r.file_name in cache_blobs:
            final_blobs[r.file_name] = cache_blobs[r.file_name]
        else:
            missing_brp_owners.add(r.object_name)

    # Drop aux entries owned by objects no longer in the new document.
    if old_xml:
        file_to_owner = {r.file_name: r.object_name
                         for r in cache.parse_object_files(old_xml)}
        aux_entries = {
            n: data for n, data in aux_entries.items()
            if file_to_owner.get(n) is None or file_to_owner[n] in new_obj_names
        }

    _write_fcstd(cache_path, new_xml, final_blobs, aux_entries)

    # Open the rebuilt cache and force recompute where needed.
    doc = FreeCAD.openDocument(str(cache_path))
    try:
        # After loading from zip, make all objects visible except construction helpers
        for obj in doc.Objects:
            if hasattr(obj, 'ViewObject') and obj.ViewObject:
                # Hide construction geometry: Sketches, Datums, Planes, Points, Axes
                should_hide = any(x in obj.TypeId for x in ['Sketch', 'Datum', 'Plane', 'Point', 'Axis'])
                obj.ViewObject.Visibility = not should_hide

        kind_of = {c.name: c.kind for c in detector.classify_document(doc)}
        touched: list[str] = []

        # Save visibility of COMPUTED objects before recompute (they will be recalculated)
        computed_visibility = {}
        for name, kind in kind_of.items():
            if kind is detector.Kind.COMPUTED:
                obj = doc.getObject(name)
                if obj and hasattr(obj, 'ViewObject') and obj.ViewObject:
                    computed_visibility[name] = obj.ViewObject.Visibility

        # Targets: changed objects from the diff + objects without a .brp source.
        targets: set[str] = set(missing_brp_owners)
        for d in deltas:
            if d.removed:
                continue
            if d.properties_changed or d.added:
                targets.add(d.name)

        for name in targets:
            if kind_of.get(name) is not detector.Kind.COMPUTED:
                continue
            obj = doc.getObject(name)
            if obj is None:
                continue
            obj.touch()
            touched.append(name)

        if touched:
            doc.recompute()
            # Restore visibility for COMPUTED objects after recompute
            for name, was_visible in computed_visibility.items():
                obj = doc.getObject(name)
                if obj and hasattr(obj, 'ViewObject') and obj.ViewObject:
                    obj.ViewObject.Visibility = was_visible
            doc.save()
    finally:
        FreeCAD.closeDocument(doc.Name)

    # Save the current commit to a marker file (don't modify the branch)
    (store.path / "CURRENT_COMMIT").write_text(target_oid)
    return target_oid, touched


def _write_fcstd(path: Path, document_xml: bytes,
                 brp_blobs: dict[str, bytes],
                 aux_entries: dict[str, bytes]) -> None:
    """Write a .FCStd zip with Document.xml first, then .brp blobs and
    auxiliary entries. Matches FreeCAD's native convention (no Mimetype,
    all entries DEFLATED, Document.xml as the first entry).
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Document.xml", document_xml)
        for name, data in brp_blobs.items():
            z.writestr(name, data)
        for name, data in aux_entries.items():
            z.writestr(name, data)
    tmp.replace(path)
