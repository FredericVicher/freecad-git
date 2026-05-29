"""FreeCAD GUI commands for the Git workbench.

Registered by InitGui at FreeCAD startup. Each command is a small Qt
wrapper around the corresponding workflow function.
"""

from __future__ import annotations

import os
from pathlib import Path

import FreeCAD       # type: ignore[import-not-found]
import FreeCADGui    # type: ignore[import-not-found]
import pygit2        # type: ignore[import-not-found]
from PySide6 import QtWidgets, QtGui  # type: ignore[import-not-found]

from . import workflow
from .git_store import Author, GitStore


_ICONS_DIR = os.path.join(os.path.dirname(__file__), "icons")


def _icon(name: str) -> str:
    return os.path.join(_ICONS_DIR, name)


def _repo_path_for(doc) -> Path:
    """Convention: the bare repo lives next to the document as `<file>.git/`."""
    return Path(doc.FileName + ".git")


def _mainwindow():
    """Returns FreeCAD's main window if available, else None."""
    return FreeCADGui.getMainWindow() if hasattr(FreeCADGui, "getMainWindow") else None


def _log(msg: str) -> None:
    FreeCAD.Console.PrintMessage(msg if msg.endswith("\n") else msg + "\n")


def _resolve_author() -> Author:
    """Resolve commit author from the user's global git config.

    Falls back to a generic identity when neither user.name nor user.email
    is set -- the user can correct it via `git config --global` afterwards.
    """
    try:
        cfg = pygit2.Config.get_global_config()
    except Exception:
        return Author("FreeCAD User", "user@local")
    name = email = None
    try:
        name = cfg["user.name"]
    except KeyError:
        pass
    try:
        email = cfg["user.email"]
    except KeyError:
        pass
    return Author(name or "FreeCAD User", email or "user@local")


class CommitCommand:
    """Persist current document state and commit selected content to git."""

    def GetResources(self):
        return {
            "Pixmap": _icon("commit.svg"),
            "MenuText": "Commit",
            "ToolTip": "Save the current document and commit Document.xml "
                       "plus imported geometry to the git repository",
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        return doc is not None and bool(doc.FileName)

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        if not doc or not doc.FileName:
            QtWidgets.QMessageBox.critical(
                _mainwindow(), "Git Commit",
                "No document open or document has no filename.")
            return

        message, ok = QtWidgets.QInputDialog.getText(
            _mainwindow(), "Git Commit", "Commit message:")
        if not ok or not message.strip():
            _log("git commit: cancelled by user")
            return

        store = GitStore(_repo_path_for(doc))  # auto-inits on first call
        author = _resolve_author()
        try:
            oid = workflow.commit_doc(doc, store, message, author)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(_mainwindow(), "Git Commit", str(exc))
            return
        _log(f"git commit {oid[:12]} -- {message}")


class PullCommand:
    """Apply the HEAD commit of the repo to the cache .FCStd and reload."""

    def GetResources(self):
        return {
            "Pixmap": _icon("pull.svg"),
            "MenuText": "Pull HEAD",
            "ToolTip": "Reload the document state from the latest git commit",
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        if not doc or not doc.FileName:
            return False
        return _repo_path_for(doc).exists()

    def Activated(self):
        _log("git pull: command triggered")
        doc = FreeCAD.ActiveDocument
        if not doc or not doc.FileName:
            QtWidgets.QMessageBox.critical(
                _mainwindow(), "Git Pull",
                "No document open or document has no filename.")
            return
        cache_path = Path(doc.FileName)
        store = GitStore(_repo_path_for(doc))

        if not store.has_head:
            QtWidgets.QMessageBox.warning(
                _mainwindow(), "Git Pull", "No commits found in the repository.")
            return

        reply = QtWidgets.QMessageBox.question(
            _mainwindow(), "Git Pull",
            "Pull HEAD into the current document?\n"
            "Any unsaved in-memory changes will be discarded.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            _log(f"git pull: cancelled by user (reply={reply})")
            return
        _log("git pull: confirmed, closing document")

        doc_name = doc.Name
        try:
            FreeCAD.closeDocument(doc_name)
            _log("git pull: document closed")
        except Exception as exc:
            _log(f"git pull: closeDocument raised {type(exc).__name__}: {exc}")
            return

        try:
            _log(f"git pull: calling workflow.pull_doc with ref=HEAD")
            oid, touched = workflow.pull_doc(store, cache_path, ref="HEAD")
            _log(f"git pull: pull_doc returned {oid[:12]}")
        except Exception as exc:
            _log(f"git pull: workflow.pull_doc raised {type(exc).__name__}: {exc}")
            try:
                FreeCAD.openDocument(str(cache_path))
            except Exception as open_exc:
                _log(f"git pull: reopenDocument failed: {open_exc}")
            QtWidgets.QMessageBox.critical(_mainwindow(), "Git Pull", str(exc))
            return

        try:
            _log(f"git pull: reopening document from {cache_path}")
            FreeCAD.openDocument(str(cache_path))
            _log(f"git pull: document reopened")
        except Exception as exc:
            _log(f"git pull: openDocument raised {type(exc).__name__}: {exc}")
            return

        _log(f"git pull {oid[:12]} -- {len(touched)} object(s) recomputed")


class _LogDialog(QtWidgets.QDialog):
    """Dialog for viewing and pulling commits from log."""

    def __init__(self, parent, doc, store, commits):
        super().__init__(parent)
        self.doc = doc
        self.store = store
        self.commits = commits
        self.setWindowTitle("Git Log")
        self.setMinimumSize(500, 300)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout()

        self.list_widget = QtWidgets.QListWidget()
        current = self.store.current_commit()
        for short_oid, author, summary in self.commits:
            text = f"{short_oid}  {author:20s}  {summary}"
            item = QtWidgets.QListWidgetItem(text)
            # Highlight current commit
            if current and short_oid in current:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setBackground(QtGui.QBrush(QtGui.QColor(200, 200, 255)))
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)

        btn_layout = QtWidgets.QHBoxLayout()
        pull_btn = QtWidgets.QPushButton("Pull this commit")
        close_btn = QtWidgets.QPushButton("Close")

        pull_btn.clicked.connect(self._on_pull)
        close_btn.clicked.connect(self.close)

        btn_layout.addWidget(pull_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def _on_pull(self):
        idx = self.list_widget.currentRow()
        if idx < 0:
            QtWidgets.QMessageBox.warning(self, "Git Pull", "Select a commit first.")
            return
        short_oid, _, _ = self.commits[idx]
        self.close()
        self._do_pull(short_oid)

    def _do_pull(self, commit_ref: str):
        if not self.doc or not self.doc.FileName:
            QtWidgets.QMessageBox.critical(
                _mainwindow(), "Git Pull",
                "Document was closed. Cannot pull.")
            return
        cache_path = Path(self.doc.FileName)
        reply = QtWidgets.QMessageBox.question(
            _mainwindow(), "Git Pull",
            f"Pull {commit_ref}? Unsaved changes will be lost.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        doc_name = self.doc.Name
        FreeCAD.closeDocument(doc_name)
        try:
            oid, touched = workflow.pull_doc(self.store, cache_path, ref=commit_ref)
            FreeCAD.openDocument(str(cache_path))
            _log(f"Pulled {oid[:12]}")
        except Exception as exc:
            FreeCAD.openDocument(str(cache_path))
            QtWidgets.QMessageBox.critical(_mainwindow(), "Git Pull", str(exc))


class LogCommand:
    """Display the commit history of the current document's repository."""

    def GetResources(self):
        return {
            "Pixmap": _icon("log.svg"),
            "MenuText": "Log",
            "ToolTip": "Show the git history for the current document",
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        if not doc or not doc.FileName:
            return False
        return _repo_path_for(doc).exists()

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        store = GitStore(_repo_path_for(doc))
        if not store.has_head:
            QtWidgets.QMessageBox.information(
                _mainwindow(), "Git Log", "No commits in the repository.")
            return

        commits = []
        for commit in store.repo.walk(store.repo.references["refs/heads/main"].target,
                                       pygit2.GIT_SORT_TIME):
            short_oid = str(commit.id)[:8]
            author = commit.author.name
            summary = commit.message.splitlines()[0] if commit.message else ""
            commits.append((short_oid, author, summary))

        dialog = _LogDialog(_mainwindow(), doc, store, commits)
        dialog.exec()


if hasattr(FreeCADGui, "addCommand"):
    FreeCADGui.addCommand("Git_Commit", CommitCommand())
    FreeCADGui.addCommand("Git_Pull", PullCommand())
    FreeCADGui.addCommand("Git_Log", LogCommand())
