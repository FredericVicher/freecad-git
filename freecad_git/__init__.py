"""FreeCAD Git Integration package.

Modules:
    detector  -- classify document objects as computed vs imported
    commands  -- FreeCAD GUI commands (registered by InitGui)

.FCStd packing is delegated to FreeCAD's native `doc.save()`; git I/O goes
through pygit2's object database directly in RAM.
"""

from . import detector  # noqa: F401
