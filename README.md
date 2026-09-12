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
  - The rebuilt document keeps the visibility state loaded from the `.FCStd` archive instead of forcing heuristic defaults
  - Visibility is restored after recompute so derived objects keep their loaded state
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
2. If no .FCStd document is active, pick a recent scan folder (or browse), then select a `*.FCStd.git` archive
3. The current loaded commit is shown with a blue background
4. Click on any commit to select it
5. Click **Pull this commit** to load that version

### Startup Behavior
1. In the **Git** menu, click **Toggle Git auto-start**
2. Choose whether Git should become the active workbench automatically at FreeCAD startup
3. Restart FreeCAD for the change to take effect

### Localization
- User-facing strings in the workbench use FreeCAD/Qt translation hooks.
- Starter translation sources are included in `freecad_git/translations`.
- When translation files are present, `InitGui.py` registers that language path with FreeCAD at startup.
- The workbench follows FreeCAD's current UI language automatically.
- If no translation exists for the current language, English source strings are used.

#### Included starter translations
- `freecad_git/translations/freecad_git_fr.ts`
- `freecad_git/translations/freecad_git_de.ts`

These are source files for Qt Linguist. They must be compiled to `.qm` files before FreeCAD can use them at runtime.

#### Adding a new language
1. Create a Qt translation source file named `freecad_git_<locale>.ts` in `freecad_git/translations` (examples: `freecad_git_fr.ts`, `freecad_git_de.ts`).
2. Add translations for the `freecad_git` context (all source strings are in English).
3. Compile the `.ts` file into a `.qm` file with Qt Linguist tools (`lrelease`), e.g. `freecad_git_fr.qm`.
4. Ship both `.ts` (optional, for maintenance) and `.qm` (required at runtime) in `freecad_git/translations`.
5. Restart FreeCAD. The plugin will automatically use the same language as FreeCAD when a matching `.qm` exists.

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
5. Visibility state loaded from the rebuilt archive is restored after recompute

This design ensures:
- **Minimal file size**: Only essential data is versioned
- **Automatic updates**: Computed features always reflect their definitions
- **Collaboration-friendly**: Merges focus on structural changes, not derived geometry

## Requirements

- **FreeCAD 1.1** or later
- **Python 3.8+** (included with FreeCAD)
- **pygit2** (included with FreeCAD 1.1+)

## Known Limitations

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
