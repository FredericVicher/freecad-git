# FreeCAD Git Workbench

A FreeCAD workbench that provides efficient version control for FreeCAD documents using Git. Track design changes, navigate your project history, and collaborate with selective commits that only store imported geometry and document structure—computed features are automatically recalculated.

## Features

### Commit
- **Selective Commit**: Save the current document state and commit only the essential parts to Git:
  - Document structure (Document.xml)
  - Imported geometry (.brp files of IMPORTED objects)
  - Computed features are **not** stored; they are recalculated on pull
- **Author Detection**: Automatically uses your Git global configuration (user.name, user.email)
- **Clean History**: Lightweight commits with only necessary content

### Pull
- **Load Any Commit**: Navigate your project history and pull any previous commit version
- **Smart Visibility**: 
  - Model objects and features are displayed
  - Construction geometry (Sketches, Datums, Planes, Points, Axes) is hidden by default
  - Visibility state is preserved for computed objects across recomputes
- **Selective Recalculation**: Only objects that changed or lack cached geometry are recomputed
- **Non-Destructive**: Pulling a commit doesn't modify your Git history—only the current working document

### Log
- **Interactive History**: View all commits with author name and message—easily visualize your entire design evolution
- **Current Commit Highlight**: The commit you've currently loaded is highlighted in blue so you always know where you are
- **Direct Pull**: Select and pull any commit from the history without closing FreeCAD—compare different versions instantly

## Installation

### Via Addon Manager (Recommended)
1. In FreeCAD, go to **Tools → Addon Manager**
2. Search for **freecad-git**
3. Click **Install**
4. Restart FreeCAD
5. The Git workbench will appear in the **View → Workbench** selector

### Manual Installation (Development)
Clone or download the repository to your FreeCAD Mod directory:
- **Windows**: `%APPDATA%\FreeCAD\v1-1\Mod\freecad-git`
- **Linux**: `~/.FreeCAD/v1-1/Mod/freecad-git`
- **macOS**: `~/Library/Application\ Support/FreeCAD/v1-1/Mod/freecad-git`

Restart FreeCAD.

## Usage

### Commit Your Work
1. Open a FreeCAD document and make changes
2. Switch to the **Git** workbench
3. Click **Commit** in the toolbar or use the menu
4. Enter your commit message
5. Your changes are saved to the Git repository

### Pull a Previous Version
1. Switch to the **Git** workbench
2. Click **Pull HEAD** to load the latest commit, or
3. Click **Log** to view the full history, then select a commit and click **Pull this commit**
4. Confirm the pull (any unsaved in-memory changes will be discarded)
5. The document reloads with the selected version
6. Computed features automatically recalculate

### View Commit History
1. Click **Log** to open the commit history dialog
2. The current loaded commit is shown with a blue background
3. Click on any commit to select it
4. Click **Pull this commit** to load that version

## How It Works

### What Gets Stored
- **Document.xml**: The FreeCAD document structure and object properties
- **Imported Geometry** (.brp files): Geometry imported from external sources
- **NOT stored**: Computed features (Sketches, Pads, Pockets, Revolutions, etc.)

### What Gets Recalculated
When you pull a commit:
1. The document structure is loaded from Document.xml
2. Imported geometry is restored from cached .brp files
3. Objects that changed or lack cached geometry are marked for recalculation
4. FreeCAD's recompute engine calculates features with the new structure
5. Visibility state is restored to match your previous session

This design ensures:
- **Minimal file size**: Only essential data is versioned
- **Automatic updates**: Computed features always reflect their definitions
- **Collaboration-friendly**: Merges focus on structural changes, not derived geometry

## Requirements

- **FreeCAD 1.1** or later
- **Python 3.8+** (included with FreeCAD)
- **pygit2** (included with FreeCAD 1.1+)

## Known Limitations

- **Construction axes** may remain visible in some cases after pull (minor rendering issue, does not affect functionality)
- **Repository location**: Each document's repository is stored as `<filename>.git` next to the .FCStd file
- **Single branch**: The workbench currently works on the `main` branch

## Architecture Overview

The workbench uses:
- **pygit2** for Git operations (in-memory blob/tree/commit construction)
- **FreeCAD's native ZIP handling** for .FCStd file I/O (no custom packer needed)
- **Document XML diffing** via Git's object database for change detection
- **Lazy recomputation** to recalculate only affected objects

All Git operations work in-memory without a traditional working tree, keeping your document directory clean.

## Contributing

Found a bug or have an idea? Contributions are welcome on GitHub.

## License

This workbench is released under the [**LGPL 2.1+**](https://opensource.org/licenses/LGPL-2.1) license.
