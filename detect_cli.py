"""Standalone classifier + cache extractor.

Invoke with FreeCAD's bundled Python so `import FreeCAD` resolves:

    & "C:\\Program Files\\FreeCAD 1.x\\bin\\python.exe" detect_cli.py path/to/file.FCStd
"""

from __future__ import annotations

import sys
from pathlib import Path

import FreeCAD  # type: ignore[import-not-found]  # provided by FreeCAD's Python

from freecad_git import cache, detector


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    fcstd_path = Path(argv[1]).resolve()
    if not fcstd_path.is_file():
        print(f"file not found: {fcstd_path}")
        return 1

    doc = FreeCAD.openDocument(str(fcstd_path))
    try:
        classifications = detector.classify_document(doc)
    finally:
        FreeCAD.closeDocument(doc.Name)

    imported = detector.imported_objects(classifications)
    print(detector.format_report(classifications))
    print()
    print(f"=> {len(imported)} imported object(s) require .brp tracking.")
    print()

    # Compose with cache reader -- this is what the commit path will do.
    imported_names = [c.name for c in imported]
    blobs = cache.read_files_for(fcstd_path, imported_names)
    xml_blob = cache.read_document_xml(fcstd_path)

    print("=== git payload for selective commit ===")
    print(f"  Document.xml                                 {len(xml_blob):>10d} bytes")
    total = len(xml_blob)
    for entry_name, data in sorted(blobs.items()):
        print(f"  {entry_name:42s} {len(data):>10d} bytes")
        total += len(data)
    print(f"  {'TOTAL (uncompressed)':42s} {total:>10d} bytes")
    print()
    print("  Note: the win isn't raw size -- git's zlib pack will compress")
    print("  these blobs and dedup unchanged .brp by content hash across commits.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
