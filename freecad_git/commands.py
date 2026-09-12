"""FreeCAD GUI commands for the Git workbench.

Registered by InitGui at FreeCAD startup. Each command is a small Qt
wrapper around the corresponding workflow function.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import FreeCAD       # type: ignore[import-not-found]
import FreeCADGui    # type: ignore[import-not-found]
import pygit2        # type: ignore[import-not-found]
from PySide6 import QtWidgets, QtGui, QtCore  # type: ignore[import-not-found]

from . import workflow
from .git_store import Author, GitStore


_ICONS_DIR = os.path.join(os.path.dirname(__file__), "icons")
_TRANSLATION_CONTEXT = "freecad_git"


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


def _tr(text: str) -> str:
    if hasattr(FreeCAD, "Qt") and hasattr(FreeCAD.Qt, "translate"):
        return FreeCAD.Qt.translate(_TRANSLATION_CONTEXT, text)
    return QtCore.QCoreApplication.translate(_TRANSLATION_CONTEXT, text)


_RECENT_LOG_SCAN_PARAM = "RecentLogScanDirs"
_RECENT_LOG_REPO_PARAM = "RecentLogRepos"
_AUTO_START_WORKBENCH_PARAM = "AutoStartWorkbench"
_MAX_RECENT_LOG_SCAN_DIRS = 8
_MAX_RECENT_LOG_REPOS = 16


def _prefs():
    return FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/freecad-git")


def _recent_log_scan_dirs() -> list[str]:
    raw = _prefs().GetString(_RECENT_LOG_SCAN_PARAM, "")
    dirs = [d.strip() for d in raw.split("\n") if d.strip()]
    return [d for d in dirs if Path(d).exists()]


def _remember_log_scan_dir(path: str | Path) -> None:
    selected = str(Path(path))
    norm_selected = os.path.normcase(selected)
    recent = [selected]
    for existing in _recent_log_scan_dirs():
        if os.path.normcase(existing) != norm_selected:
            recent.append(existing)
    _prefs().SetString(_RECENT_LOG_SCAN_PARAM, "\n".join(recent[:_MAX_RECENT_LOG_SCAN_DIRS]))


def _recent_log_repos() -> list[Path]:
    raw = _prefs().GetString(_RECENT_LOG_REPO_PARAM, "")
    repos: list[Path] = []
    seen: set[str] = set()

    def add_repo(path: str | Path) -> None:
        repo_path = Path(path)
        if not repo_path.exists() or not repo_path.is_dir():
            return
        if not repo_path.name.lower().endswith(".fcstd.git"):
            return
        norm = os.path.normcase(str(repo_path))
        if norm in seen:
            return
        seen.add(norm)
        repos.append(repo_path)

    for entry in [d.strip() for d in raw.split("\n") if d.strip()]:
        add_repo(entry)

    for scan_dir in _recent_log_scan_dirs():
        for repo_path in _discover_freecad_git_archives(Path(scan_dir)):
            add_repo(repo_path)

    return repos[:_MAX_RECENT_LOG_REPOS]


def _remember_log_repo(path: str | Path) -> None:
    selected = Path(path)
    if not selected.exists() or not selected.is_dir() or not selected.name.lower().endswith(".fcstd.git"):
        return

    selected_str = str(selected)
    norm_selected = os.path.normcase(selected_str)
    recent = [selected_str]
    for existing in _recent_log_repos():
        existing_str = str(existing)
        if os.path.normcase(existing_str) != norm_selected:
            recent.append(existing_str)
    _prefs().SetString(_RECENT_LOG_REPO_PARAM, "\n".join(recent[:_MAX_RECENT_LOG_REPOS]))
    _remember_log_scan_dir(selected.parent)


def _discover_freecad_git_archives(root: Path) -> list[Path]:
    archives = [
        p for p in root.rglob("*.git")
        if p.is_dir() and p.name.lower().endswith(".fcstd.git")
    ]
    return sorted(archives)


def _doc_has_unsaved_changes(doc) -> bool:
    if doc is None:
        return False
    try:
        if hasattr(doc, "isModified"):
            return bool(doc.isModified())
    except Exception:
        pass
    try:
        return bool(getattr(doc, "Modified", False))
    except Exception:
        return False


class _CommitDialog(QtWidgets.QDialog):
    """Dialog for committing with branch selection and creation."""

    def __init__(self, parent, store, current_branch, needs_new_branch, allow_new_branch=False):
        super().__init__(parent)
        self.store = store
        self.current_branch = current_branch
        self.needs_new_branch = needs_new_branch
        self.allow_new_branch = allow_new_branch
        self.selected_branch = current_branch
        self.commit_message = ""
        self.create_new = needs_new_branch
        self.new_branch_name = ""
        self.setWindowTitle(_tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Commit")))
        self.setMinimumWidth(400)
        self._build_ui()

    def _toggle_branch_input(self, checked):
        if self.new_branch_input is not None:
            self.new_branch_input.setEnabled(checked)

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout()

        # Current branch display
        info_label = QtWidgets.QLabel(
            _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Current branch: {branch}")).format(
                branch=self.current_branch
            )
        )
        info_font = info_label.font()
        info_font.setBold(True)
        info_label.setFont(info_font)
        layout.addWidget(info_label)

        # If needs new branch, show that info and ask for branch name
        self.new_branch_input = None
        self.new_branch_checkbox = None

        if self.needs_new_branch:
            warning = QtWidgets.QLabel(
                _tr(
                    QtCore.QT_TRANSLATE_NOOP(
                        "freecad_git",
                        "This commit has descendants.\nCreating a new branch...",
                    )
                )
            )
            warning.setStyleSheet("color: #FF8800; font-weight: bold;")
            layout.addWidget(warning)

            # Branch name input
            branch_layout = QtWidgets.QHBoxLayout()
            branch_layout.addWidget(
                QtWidgets.QLabel(_tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "New branch name:")))
            )
            self.new_branch_input = QtWidgets.QLineEdit()
            branch_layout.addWidget(self.new_branch_input)
            layout.addLayout(branch_layout)
        elif self.allow_new_branch:
            self.new_branch_checkbox = QtWidgets.QCheckBox(
                _tr(
                    QtCore.QT_TRANSLATE_NOOP(
                        "freecad_git",
                        "Create a new branch before committing",
                    )
                )
            )
            self.new_branch_checkbox.toggled.connect(self._toggle_branch_input)
            layout.addWidget(self.new_branch_checkbox)

            branch_layout = QtWidgets.QHBoxLayout()
            branch_layout.addWidget(
                QtWidgets.QLabel(_tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "New branch name:")))
            )
            self.new_branch_input = QtWidgets.QLineEdit()
            self.new_branch_input.setEnabled(False)
            self.new_branch_input.setPlaceholderText(
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "branch-name"))
            )
            branch_layout.addWidget(self.new_branch_input)
            layout.addLayout(branch_layout)

        # Commit message
        layout.addWidget(QtWidgets.QLabel(_tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Commit message:"))))
        self.message_input = QtWidgets.QPlainTextEdit()
        self.message_input.setMinimumHeight(80)
        layout.addWidget(self.message_input)

        # Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        commit_btn = QtWidgets.QPushButton(_tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Commit")))
        cancel_btn = QtWidgets.QPushButton(_tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Cancel")))
        commit_btn.clicked.connect(self._on_commit)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(commit_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def _on_commit(self):
        message = self.message_input.toPlainText().strip()
        if not message:
            QtWidgets.QMessageBox.warning(
                self,
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Commit")),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Commit message cannot be empty.")),
            )
            return

        should_create_branch = self.needs_new_branch
        if self.new_branch_checkbox is not None:
            should_create_branch = self.new_branch_checkbox.isChecked()

        if should_create_branch:
            new_name = self.new_branch_input.text().strip() if self.new_branch_input is not None else ""
            if not new_name:
                QtWidgets.QMessageBox.warning(
                    self,
                    _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Commit")),
                    _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Branch name cannot be empty.")),
                )
                return
            self.create_new = True
            self.new_branch_name = new_name
        else:
            self.create_new = False
            self.new_branch_name = ""

        self.commit_message = message
        self.accept()


def _normalize_commit_oid(store: GitStore, commit_oid: str | None) -> str | None:
    """Resolve a commit identifier (short/full) to a full OID when possible."""
    if not commit_oid:
        return None
    try:
        return str(store.repo.revparse_single(commit_oid).peel(pygit2.Commit).id)
    except Exception:
        return commit_oid


def _commit_has_descendants_on_branch(store: GitStore, branch_name: str, commit_oid: str | None) -> bool:
    """Return True if commit_oid has children in the specified branch history."""
    normalized_oid = _normalize_commit_oid(store, commit_oid)
    if not normalized_oid:
        return False

    branch_ref = f"refs/heads/{branch_name}"
    if branch_ref not in store.repo.references:
        return False

    target = store.repo.references[branch_ref].target
    for commit in store.repo.walk(target, pygit2.GIT_SORT_TIME):
        if normalized_oid in {str(parent_id) for parent_id in commit.parent_ids}:
            return True
    return False


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
            "MenuText": _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Commit")),
            "ToolTip": _tr(
                QtCore.QT_TRANSLATE_NOOP(
                    "freecad_git",
                    "Save the current document and commit Document.xml plus imported geometry to the git repository",
                )
            ),
        }

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        return doc is not None and bool(doc.FileName)

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        if not doc or not doc.FileName:
            QtWidgets.QMessageBox.critical(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Commit")),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "No document open or document has no filename.")),
            )
            return

        store = GitStore(_repo_path_for(doc))  # auto-inits on first call

        # If CURRENT_COMMIT doesn't exist (document opened without pull),
        # initialize it with the current branch HEAD
        if not store.current_commit():
            head_oid = store.head_oid()
            if head_oid:
                marker = store.path / "CURRENT_COMMIT"
                marker.write_text(head_oid)
                _log(f"git: initialized CURRENT_COMMIT with HEAD {head_oid[:12]}")

        current_branch = store.current_branch()
        current_oid = _normalize_commit_oid(store, store.current_commit())

        # If the current version already has descendants, keep it on a new branch.
        # If it is the branch HEAD and has no descendants, offer the user an optional
        # checkbox to create a new branch before the commit.
        needs_new_branch = False
        allow_new_branch = False
        if current_oid and current_branch:
            try:
                branch_ref = f"refs/heads/{current_branch}"
                if branch_ref in store.repo.references:
                    branch_head = str(store.repo.references[branch_ref].target)
                    has_descendants = _commit_has_descendants_on_branch(store, current_branch, current_oid)
                    if has_descendants or current_oid != branch_head:
                        needs_new_branch = True
                    else:
                        allow_new_branch = True

                    _log(
                        "git commit: branch-decision "
                        f"branch={current_branch} "
                        f"current={current_oid[:8]} "
                        f"head={branch_head[:8]} "
                        f"has_descendants={has_descendants} "
                        f"needs_new_branch={needs_new_branch} "
                        f"allow_new_branch={allow_new_branch}"
                    )
            except Exception:
                pass

        # Show commit dialog
        dialog = _CommitDialog(_mainwindow(), store, current_branch, needs_new_branch, allow_new_branch=allow_new_branch)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            _log("git commit: cancelled by user")
            return

        message = dialog.commit_message
        author = _resolve_author()

        # Determine which branch this commit will be on
        target_branch = dialog.new_branch_name if dialog.create_new else current_branch

        # Add branch name to commit message for tracking
        message_with_branch = f"{message}\n\n[branch: {target_branch}]"

        if dialog.create_new:
            branch_start_ref = current_oid or "HEAD"
            try:
                store.create_branch(dialog.new_branch_name, branch_start_ref)
                store.switch_branch(dialog.new_branch_name)
                _log(f"git branch: created '{dialog.new_branch_name}' from {branch_start_ref[:12]}")
            except Exception as exc:
                QtWidgets.QMessageBox.critical(
                    _mainwindow(),
                    _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Commit")),
                    _tr(
                        QtCore.QT_TRANSLATE_NOOP(
                            "freecad_git",
                            "Could not create branch '{branch}': {error}",
                        )
                    ).format(branch=dialog.new_branch_name, error=exc),
                )
                return

        try:
            oid = workflow.commit_doc(doc, store, message_with_branch, author)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Commit")),
                str(exc),
            )
            return

        try:
            if hasattr(FreeCADGui, "runCommand"):
                FreeCADGui.runCommand("Std_Save")
            else:
                doc.save()

            if hasattr(doc, "setModified"):
                doc.setModified(False)
        except Exception as exc:
            _log(f"git commit: WARNING - post-commit save failed: {exc}")
            QtWidgets.QMessageBox.warning(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Commit")),
                _tr(
                    QtCore.QT_TRANSLATE_NOOP(
                        "freecad_git",
                        "Commit created, but post-commit save failed: {error}",
                    )
                ).format(error=exc),
            )

        # Update CURRENT_COMMIT so next commit has correct parent
        # (Document in memory is already the correct state - it's what we just committed)
        try:
            marker = store.path / "CURRENT_COMMIT"
            marker.write_text(oid)
            _log(f"git commit: updated CURRENT_COMMIT to {oid[:12]}")
        except Exception as exc:
            _log(f"git commit: WARNING - could not update CURRENT_COMMIT: {exc}")

        _log(f"git commit {oid[:12]} ({store.current_branch()}) -- {message}")


class PullCommand:
    """Apply the HEAD commit of the repo to the cache .FCStd and reload."""

    def GetResources(self):
        return {
            "Pixmap": _icon("pull.svg"),
            "MenuText": _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Pull HEAD")),
            "ToolTip": _tr(
                QtCore.QT_TRANSLATE_NOOP(
                    "freecad_git",
                    "Reload the document state from the latest git commit",
                )
            ),
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
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Pull")),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "No document open or document has no filename.")),
            )
            return
        cache_path = Path(doc.FileName)
        store = GitStore(_repo_path_for(doc))

        if not store.has_head:
            QtWidgets.QMessageBox.warning(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Pull")),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "No commits found in the repository.")),
            )
            return

        current_oid = _normalize_commit_oid(store, store.current_commit())
        target_oid = store.resolve_ref("HEAD")
        if current_oid and current_oid == target_oid:
            _log(f"git pull: already at {target_oid[:12]}, skipping reload")
            return

        if _doc_has_unsaved_changes(doc):
            reply = QtWidgets.QMessageBox.question(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Pull")),
                _tr(
                    QtCore.QT_TRANSLATE_NOOP(
                        "freecad_git",
                        "Pull HEAD into the current document?\nAny unsaved in-memory changes will be discarded.",
                    )
                ),
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
            QtWidgets.QMessageBox.critical(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Pull")),
                str(exc),
            )
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
    """Dialog for viewing and pulling commits from log with branch info."""

    def __init__(self, parent, doc, store, log_data, branches, current_commit=None, cache_path=None):
        super().__init__(parent)
        self.doc = doc
        self.store = store
        self.log_data = log_data  # List of (short_oid, author, summary, branch, parents, is_branch_start, child_count, timestamp)
        self.branches = branches  # List of branch names
        self.current_commit = current_commit[:8] if current_commit else None
        self.cache_path = Path(cache_path) if cache_path else (Path(doc.FileName) if doc and getattr(doc, "FileName", "") else None)
        self.selected_commit = None
        self.selected_branch = None
        self._line_to_commit = {}  # Map row index to commit info
        self._current_highlighted_line = None  # Track current highlighted row
        self.setWindowTitle(_tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Log")))
        self.setMinimumSize(1000, 400)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout()

        self.log_display = QtWidgets.QTableWidget(0, 5)
        self.log_display.verticalHeader().setVisible(False)
        self.log_display.setHorizontalHeaderLabels([
            _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "OID")),
            _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Date")),
            _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Branch")),
            _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Author")),
            _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Message")),
        ])
        self.log_display.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.log_display.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.log_display.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.log_display.setAlternatingRowColors(True)
        self.log_display.setWordWrap(False)
        self.log_display.setTextElideMode(QtCore.Qt.ElideRight)
        self.log_display.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        self.log_display.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.log_display.horizontalHeader().setSectionsClickable(False)
        self.log_display.horizontalHeader().setMinimumSectionSize(80)

        for i, (short_oid, author, summary, branch, parents, is_branch_start, child_count, timestamp) in enumerate(self.log_data):
            self.log_display.insertRow(i)
            checkout_dt = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
            branch_info = f"[{branch}]"
            parent_str = ""
            if is_branch_start and parents:
                parent_str = f" <- {parents[0]}"
                branch_info = f"[{branch}{parent_str}]"

            self._line_to_commit[i] = (short_oid, branch)
            row_items = [
                QtWidgets.QTableWidgetItem(short_oid),
                QtWidgets.QTableWidgetItem(checkout_dt),
                QtWidgets.QTableWidgetItem(branch_info),
                QtWidgets.QTableWidgetItem(author[:20]),
                QtWidgets.QTableWidgetItem(summary),
            ]
            for col, item in enumerate(row_items):
                item.setToolTip(item.text())
                self.log_display.setItem(i, col, item)

        self.log_display.resizeColumnsToContents()
        header = self.log_display.horizontalHeader()
        for idx in range(self.log_display.columnCount() - 1):
            header.setSectionResizeMode(idx, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.log_display.columnCount() - 1, QtWidgets.QHeaderView.ResizeToContents)
        total_width = sum(self.log_display.columnWidth(c) for c in range(self.log_display.columnCount())) + 40
        if total_width > 1500:
            self.resize(min(total_width, 1500), self.height())
        else:
            self.resize(max(1000, total_width), self.height())
        self.log_display.setMinimumWidth(max(700, min(total_width, 1400)))
        layout.addWidget(self.log_display)

        # Info label
        self.info_label = QtWidgets.QLabel(
            _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Click a commit to select"))
        )
        layout.addWidget(self.info_label)

        self.log_display.itemSelectionChanged.connect(self._on_table_click)
        self._highlight_current_commit()

        # Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        pull_btn = QtWidgets.QPushButton(
            _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Pull selected commit"))
        )
        close_btn = QtWidgets.QPushButton(_tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Close")))

        pull_btn.clicked.connect(self._on_pull)
        close_btn.clicked.connect(self.close)

        btn_layout.addWidget(pull_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def _generate_log_display(self) -> str:
        """Generate commit list with branch info and parent references."""
        lines = []
        self._line_to_commit = {}  # Reset mapping

        # Header
        lines.append("=" * 100)
        lines.append(
            _tr(
                QtCore.QT_TRANSLATE_NOOP(
                    "freecad_git",
                    "COMMIT HISTORY - Click to select | 'Pull selected commit' to checkout",
                )
            )
        )
        lines.append(
            _tr(
                QtCore.QT_TRANSLATE_NOOP(
                    "freecad_git",
                    "OID      DATE/TIME        BRANCH                    AUTHOR               MESSAGE",
                )
            )
        )
        lines.append("=" * 100)

        for i, (short_oid, author, summary, branch, parents, is_branch_start, child_count, timestamp) in enumerate(self.log_data):
            # Show parent reference only if this commit creates a branch divergence
            parent_str = ""
            if is_branch_start and parents:
                parent_str = f" <- {parents[0]}"

            # Create commit line with branch info and parent reference
            checkout_dt = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
            branch_info = f"[{branch}{parent_str}]"
            commit_line = f"{short_oid} {checkout_dt:16s} {branch_info:<25s} {author[:20]:20s} {summary[:50]}"

            lines.append(commit_line)

            # Map line number (in the text display) to commit info for click handling
            # Line number is 4 (after headers) + i
            line_num = 4 + i
            self._line_to_commit[line_num] = (short_oid, branch)

        lines.append("=" * 100)

        return "\n".join(lines)


    def _highlight_current_commit(self):
        """Highlight the tracked current commit, if it exists in the log."""
        if not self.current_commit:
            return

        for row_idx, (short_oid, _) in self._line_to_commit.items():
            if short_oid == self.current_commit:
                self.log_display.selectRow(row_idx)
                for col in range(self.log_display.columnCount()):
                    item = self.log_display.item(row_idx, col)
                    if item is not None:
                        bg = QtGui.QBrush(QtGui.QColor(120, 180, 120))
                        item.setBackground(bg)
                self._current_highlighted_line = row_idx
                return

    def _on_table_click(self):
        """Handle selection in the commit table."""
        row = self.log_display.currentRow()
        if row < 0 or row not in self._line_to_commit:
            return
        short_oid, branch = self._line_to_commit[row]
        self.selected_commit = short_oid
        self.selected_branch = branch
        self.info_label.setText(
            _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Selected: {commit} from {branch}")).format(
                commit=short_oid,
                branch=branch,
            )
        )
        self._highlight_line(row)

    def _highlight_line(self, line_num):
        """Highlight the selected row and remove previous highlight."""
        for row_idx in range(self.log_display.rowCount()):
            for col in range(self.log_display.columnCount()):
                item = self.log_display.item(row_idx, col)
                if item is None:
                    continue
                if row_idx == self._current_highlighted_line and row_idx != line_num:
                    item.setBackground(QtGui.QBrush())
                elif row_idx == line_num:
                    item.setBackground(QtGui.QBrush(QtGui.QColor(100, 150, 200)))

        if self.current_commit and self.log_display.item(line_num, 0) and self.log_display.item(line_num, 0).text() == self.current_commit:
            for col in range(self.log_display.columnCount()):
                item = self.log_display.item(line_num, col)
                if item is not None:
                    item.setBackground(QtGui.QBrush(QtGui.QColor(120, 180, 120)))

        self._current_highlighted_line = line_num

    def _on_pull(self):
        commit_ref = self.selected_commit or self.current_commit
        branch_name = self.selected_branch or self.store.current_branch()

        if not commit_ref:
            head_oid = self.store.head_oid()
            if head_oid:
                commit_ref = head_oid[:8]

        if not commit_ref:
            QtWidgets.QMessageBox.warning(
                self,
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Pull")),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "No commit available to pull.")),
            )
            return

        self.close()
        self._do_pull(commit_ref, branch_name)

    def _do_pull(self, commit_ref: str, branch_name: str | None = None):
        """Pull the specified commit and track its branch when provided."""
        if not self.cache_path:
            QtWidgets.QMessageBox.critical(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Pull")),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "No target .FCStd path available for this repository.")),
            )
            return

        cache_path = Path(self.cache_path)

        current_oid = _normalize_commit_oid(self.store, self.store.current_commit())
        target_oid = _normalize_commit_oid(self.store, commit_ref)
        has_loaded_target_doc = bool(
            self.doc
            and getattr(self.doc, "FileName", "")
            and Path(self.doc.FileName) == cache_path
            and cache_path.exists()
        )
        if current_oid and target_oid and current_oid == target_oid and has_loaded_target_doc:
            if branch_name:
                try:
                    self.store.switch_branch(branch_name)
                    _log(f"git pull: switched current branch to {branch_name}")
                except Exception as branch_exc:
                    _log(f"git pull: WARNING - could not switch branch to {branch_name}: {branch_exc}")
            _log(f"git pull: already at {target_oid[:12]}, skipping reload")
            return

        had_open_doc = bool(self.doc and getattr(self.doc, "FileName", ""))
        if had_open_doc and _doc_has_unsaved_changes(self.doc):
            reply = QtWidgets.QMessageBox.question(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Pull")),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Pull {commit}? Unsaved changes will be lost.")).format(
                    commit=commit_ref
                ),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return
        if had_open_doc:
            FreeCAD.closeDocument(self.doc.Name)

        try:
            oid, touched = workflow.pull_doc(self.store, cache_path, ref=commit_ref)
            if branch_name:
                try:
                    self.store.switch_branch(branch_name)
                    _log(f"git pull: switched current branch to {branch_name}")
                except Exception as branch_exc:
                    _log(f"git pull: WARNING - could not switch branch to {branch_name}: {branch_exc}")
            FreeCAD.openDocument(str(cache_path))
            self.doc = FreeCAD.ActiveDocument
            self.cache_path = cache_path
            _log(f"Pulled {oid[:12]}")
        except Exception as exc:
            if had_open_doc:
                try:
                    FreeCAD.openDocument(str(cache_path))
                except Exception:
                    pass
            QtWidgets.QMessageBox.critical(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Pull")),
                str(exc),
            )


class LogCommand:
    """Display the commit history of the current document's repository."""

    def GetResources(self):
        return {
            "Pixmap": _icon("log.svg"),
            "MenuText": _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Log")),
            "ToolTip": _tr(
                QtCore.QT_TRANSLATE_NOOP(
                    "freecad_git",
                    "Show the git history for the current document",
                )
            ),
        }

    def IsActive(self):
        return True

    def _cache_path_for_repo(self, repo_path: Path) -> Path:
        return repo_path.with_suffix("") if repo_path.suffix == ".git" else repo_path

    def _pick_repo_from_list(self, repo_paths: list[Path]) -> Path | None:
        dialog = QtWidgets.QDialog(_mainwindow())
        dialog.setWindowTitle(_tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Log")))
        dialog.setMinimumWidth(560)

        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(
            QtWidgets.QLabel(_tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Select a FreeCAD git archive:")))
        )

        list_widget = QtWidgets.QListWidget(dialog)
        for repo_path in repo_paths:
            cache_path = self._cache_path_for_repo(repo_path)
            item = QtWidgets.QListWidgetItem(cache_path.name)
            item.setData(QtCore.Qt.UserRole, str(repo_path))
            item.setToolTip(str(cache_path))
            list_widget.addItem(item)
        layout.addWidget(list_widget)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            parent=dialog,
        )
        layout.addWidget(buttons)

        ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setEnabled(False)

        def _on_selection_changed():
            if ok_button is not None:
                ok_button.setEnabled(list_widget.currentItem() is not None)

        list_widget.itemSelectionChanged.connect(_on_selection_changed)
        list_widget.itemDoubleClicked.connect(lambda _: dialog.accept())
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return None
        selected = list_widget.currentItem()
        if selected is None:
            return None
        return Path(str(selected.data(QtCore.Qt.UserRole)))

    def _pick_repo_without_active_doc(self):
        recent_repos = _recent_log_repos()
        recent_dirs = _recent_log_scan_dirs()
        browse_choice = _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Browse folders..."))
        if recent_repos:
            recent_labels = [
                f"{self._cache_path_for_repo(repo_path).name}    [{self._cache_path_for_repo(repo_path).parent}]"
                for repo_path in recent_repos
            ]
            choices = recent_labels + [browse_choice]
            selected_label, ok = QtWidgets.QInputDialog.getItem(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Log")),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Select a FreeCAD git archive:")),
                choices,
                0,
                False,
            )
            if not ok:
                return None
            if selected_label != browse_choice:
                selected_index = recent_labels.index(selected_label)
                repo_path = recent_repos[selected_index]
                if repo_path.exists():
                    _remember_log_repo(repo_path)
                    return repo_path, self._cache_path_for_repo(repo_path)
                kept = [str(p) for p in recent_repos if os.path.normcase(str(p)) != os.path.normcase(str(repo_path))]
                _prefs().SetString(_RECENT_LOG_REPO_PARAM, "\n".join(kept[:_MAX_RECENT_LOG_REPOS]))

        start_dir = recent_dirs[0] if recent_dirs else (str(recent_repos[0].parent) if recent_repos else str(Path.home()))
        root_dir = QtWidgets.QFileDialog.getExistingDirectory(
            _mainwindow(),
            _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Select folder to scan for FreeCAD git archives")),
            start_dir,
        )

        if not root_dir:
            return None

        root = Path(root_dir)
        if root.is_dir() and root.name.lower().endswith(".fcstd.git"):
            repo_paths = [root]
        else:
            repo_paths = _discover_freecad_git_archives(root)
        if not repo_paths:
            QtWidgets.QMessageBox.information(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Log")),
                _tr(
                    QtCore.QT_TRANSLATE_NOOP(
                        "freecad_git",
                        "No FreeCAD git archives (*.FCStd.git) found in the selected folder.",
                    )
                ),
            )
            return None

        if len(repo_paths) == 1:
            repo_path = repo_paths[0]
            _remember_log_repo(repo_path)
            return repo_path, self._cache_path_for_repo(repo_path)

        repo_path = self._pick_repo_from_list(repo_paths)
        if repo_path is None:
            return None

        _remember_log_repo(repo_path)
        return repo_path, self._cache_path_for_repo(repo_path)

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        cache_path = None

        if doc and doc.FileName:
            repo_path = _repo_path_for(doc)
            if not repo_path.exists():
                QtWidgets.QMessageBox.information(
                    _mainwindow(),
                    _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Log")),
                    _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "No git archive found for this document.")),
                )
                return
            cache_path = Path(doc.FileName)
        else:
            picked = self._pick_repo_without_active_doc()
            if not picked:
                return
            repo_path, cache_path = picked
            doc = None

        store = GitStore(repo_path)
        if not store.has_head:
            QtWidgets.QMessageBox.information(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Log")),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "No commits in the repository.")),
            )
            return

        branches = store.list_branches()

        # Collect commits using full OIDs internally; short IDs are only for display.
        all_commits_info = {}  # full_oid -> (short_oid, author, summary, parent_full_oids, timestamp)

        try:
            for branch in branches:
                branch_ref = f"refs/heads/{branch}"
                if branch_ref not in store.repo.references:
                    continue
                target = store.repo.references[branch_ref].target

                for commit in store.repo.walk(target, pygit2.GIT_SORT_TIME):
                    full_oid = str(commit.id)
                    if full_oid not in all_commits_info:
                        short_oid = full_oid[:8]
                        author = commit.author.name
                        summary = commit.message.splitlines()[0] if commit.message else ""
                        parents = [str(parent_id) for parent_id in commit.parent_ids]
                        timestamp = commit.commit_time
                        all_commits_info[full_oid] = (short_oid, author, summary, parents, timestamp)

        except Exception as e:
            FreeCAD.Console.PrintError(
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Error collecting commits: {error}\n")).format(
                    error=e
                )
            )

        branch_heads = {}
        for branch in branches:
            branch_ref = f"refs/heads/{branch}"
            if branch_ref in store.repo.references:
                head_oid = str(store.repo.references[branch_ref].target)
                if head_oid not in branch_heads:
                    branch_heads[head_oid] = branch

        commit_branch_from_message = {}
        for full_oid in all_commits_info.keys():
            try:
                commit = store.repo[full_oid]
                if commit and commit.message:
                    for line in commit.message.split('\n'):
                        if line.strip().startswith('[branch:') and line.strip().endswith(']'):
                            branch_name = line.strip()[8:-1].strip()
                            if branch_name:
                                commit_branch_from_message[full_oid] = branch_name
                                break
            except Exception as exc:
                _log(f"git log: WARNING - could not inspect commit {full_oid[:8]}: {exc}")

        oid_to_branch = {}
        for full_oid in all_commits_info.keys():
            if full_oid in branch_heads:
                oid_to_branch[full_oid] = branch_heads[full_oid]
            elif full_oid in commit_branch_from_message:
                oid_to_branch[full_oid] = commit_branch_from_message[full_oid]
            else:
                oid_to_branch[full_oid] = "main"

        is_branch_start_map = {}
        for full_oid, (_, _, _, parents, _) in all_commits_info.items():
            is_branch_start = False
            if parents:
                parent_oid = parents[0]
                parent_branch = oid_to_branch.get(parent_oid)
                current_branch = oid_to_branch.get(full_oid)
                if parent_branch and current_branch and parent_branch != current_branch:
                    is_branch_start = True
            is_branch_start_map[full_oid] = is_branch_start

        commit_children = {}
        for full_oid, (_, _, _, parents, _) in all_commits_info.items():
            for parent_oid in parents:
                if parent_oid not in commit_children:
                    commit_children[parent_oid] = []
                commit_children[parent_oid].append(full_oid)

        log_data = []
        for full_oid, (short_oid, author, summary, parents, timestamp) in all_commits_info.items():
            branch = oid_to_branch.get(full_oid, 'main')
            is_branch_start = is_branch_start_map.get(full_oid, False)
            child_count = len(commit_children.get(full_oid, []))
            short_parents = [parent_oid[:8] for parent_oid in parents]
            log_data.append((short_oid, author, summary, branch, short_parents, is_branch_start, child_count, timestamp))

        log_data.sort(key=lambda x: x[7], reverse=True)

        current_commit = store.current_commit()
        dialog = _LogDialog(
            _mainwindow(), doc, store, log_data, branches,
            current_commit=current_commit, cache_path=cache_path)
        dialog.exec()


class ToggleAutoStartCommand:
    """Enable/disable automatic activation of the Git workbench at startup."""

    def GetResources(self):
        return {
            "Pixmap": _icon("log.svg"),
            "MenuText": _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Toggle Git auto-start")),
            "ToolTip": _tr(
                QtCore.QT_TRANSLATE_NOOP(
                    "freecad_git",
                    "Enable or disable automatic Git workbench activation at FreeCAD startup",
                )
            ),
        }

    def IsActive(self):
        return True

    def Activated(self):
        enabled = _prefs().GetBool(_AUTO_START_WORKBENCH_PARAM, False)
        if enabled:
            reply = QtWidgets.QMessageBox.question(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Workbench Startup")),
                _tr(
                    QtCore.QT_TRANSLATE_NOOP(
                        "freecad_git",
                        "Disable automatic Git workbench activation at FreeCAD startup?",
                    )
                ),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return
            _prefs().SetBool(_AUTO_START_WORKBENCH_PARAM, False)
            _log("git startup: auto-start disabled")
            QtWidgets.QMessageBox.information(
                _mainwindow(),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Workbench Startup")),
                _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git auto-start disabled.")),
            )
            return

        reply = QtWidgets.QMessageBox.question(
            _mainwindow(),
            _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Workbench Startup")),
            _tr(
                QtCore.QT_TRANSLATE_NOOP(
                    "freecad_git",
                    "Enable automatic Git workbench activation at FreeCAD startup?",
                )
            ),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        _prefs().SetBool(_AUTO_START_WORKBENCH_PARAM, True)
        _log("git startup: auto-start enabled")
        QtWidgets.QMessageBox.information(
            _mainwindow(),
            _tr(QtCore.QT_TRANSLATE_NOOP("freecad_git", "Git Workbench Startup")),
            _tr(
                QtCore.QT_TRANSLATE_NOOP(
                    "freecad_git",
                    "Git auto-start enabled. It will apply on next FreeCAD startup.",
                )
            ),
        )


if hasattr(FreeCADGui, "addCommand"):
    FreeCADGui.addCommand("Git_Commit", CommitCommand())
    FreeCADGui.addCommand("Git_Pull", PullCommand())
    FreeCADGui.addCommand("Git_Log", LogCommand())
    FreeCADGui.addCommand("Git_ToggleAutoStart", ToggleAutoStartCommand())
