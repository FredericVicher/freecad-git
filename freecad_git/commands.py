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
        self.setWindowTitle("Git Commit")
        self.setMinimumWidth(400)
        self._build_ui()

    def _toggle_branch_input(self, checked):
        if self.new_branch_input is not None:
            self.new_branch_input.setEnabled(checked)

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout()

        # Current branch display
        info_label = QtWidgets.QLabel(f"Current branch: {self.current_branch}")
        info_font = info_label.font()
        info_font.setBold(True)
        info_label.setFont(info_font)
        layout.addWidget(info_label)

        # If needs new branch, show that info and ask for branch name
        self.new_branch_input = None
        self.new_branch_checkbox = None

        if self.needs_new_branch:
            warning = QtWidgets.QLabel("This commit has descendants.\nCreating a new branch...")
            warning.setStyleSheet("color: #FF8800; font-weight: bold;")
            layout.addWidget(warning)

            # Branch name input
            branch_layout = QtWidgets.QHBoxLayout()
            branch_layout.addWidget(QtWidgets.QLabel("New branch name:"))
            self.new_branch_input = QtWidgets.QLineEdit()
            branch_layout.addWidget(self.new_branch_input)
            layout.addLayout(branch_layout)
        elif self.allow_new_branch:
            self.new_branch_checkbox = QtWidgets.QCheckBox("Create a new branch before committing")
            self.new_branch_checkbox.toggled.connect(self._toggle_branch_input)
            layout.addWidget(self.new_branch_checkbox)

            branch_layout = QtWidgets.QHBoxLayout()
            branch_layout.addWidget(QtWidgets.QLabel("New branch name:"))
            self.new_branch_input = QtWidgets.QLineEdit()
            self.new_branch_input.setEnabled(False)
            self.new_branch_input.setPlaceholderText("branch-name")
            branch_layout.addWidget(self.new_branch_input)
            layout.addLayout(branch_layout)

        # Commit message
        layout.addWidget(QtWidgets.QLabel("Commit message:"))
        self.message_input = QtWidgets.QPlainTextEdit()
        self.message_input.setMinimumHeight(80)
        layout.addWidget(self.message_input)

        # Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        commit_btn = QtWidgets.QPushButton("Commit")
        cancel_btn = QtWidgets.QPushButton("Cancel")
        commit_btn.clicked.connect(self._on_commit)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(commit_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def _on_commit(self):
        message = self.message_input.toPlainText().strip()
        if not message:
            QtWidgets.QMessageBox.warning(self, "Git Commit", "Commit message cannot be empty.")
            return

        should_create_branch = self.needs_new_branch
        if self.new_branch_checkbox is not None:
            should_create_branch = self.new_branch_checkbox.isChecked()

        if should_create_branch:
            new_name = self.new_branch_input.text().strip() if self.new_branch_input is not None else ""
            if not new_name:
                QtWidgets.QMessageBox.warning(self, "Git Commit", "Branch name cannot be empty.")
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
                    _mainwindow(), "Git Commit",
                    f"Could not create branch '{dialog.new_branch_name}': {exc}")
                return

        try:
            oid = workflow.commit_doc(doc, store, message_with_branch, author)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(_mainwindow(), "Git Commit", str(exc))
            return

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

        current_oid = _normalize_commit_oid(store, store.current_commit())
        target_oid = store.resolve_ref("HEAD")
        if current_oid and current_oid == target_oid:
            _log(f"git pull: already at {target_oid[:12]}, skipping reload")
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
    """Dialog for viewing and pulling commits from log with branch info."""

    def __init__(self, parent, doc, store, log_data, branches, current_commit=None):
        super().__init__(parent)
        self.doc = doc
        self.store = store
        self.log_data = log_data  # List of (short_oid, author, summary, branch, parents, is_branch_start, child_count, timestamp)
        self.branches = branches  # List of branch names
        self.current_commit = current_commit[:8] if current_commit else None
        self.selected_commit = None
        self.selected_branch = None
        self._line_to_commit = {}  # Map line number to commit info
        self._current_highlighted_line = None  # Track current highlighted line
        self.setWindowTitle("Git Log")
        self.setMinimumSize(900, 400)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout()

        # Create display area with branch tree + commit list
        self.log_display = QtWidgets.QPlainTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setFont(QtGui.QFont("Courier", 9))

        # Generate and display the log
        display_text = self._generate_log_display()
        self.log_display.setPlainText(display_text)
        self._highlight_current_commit()

        # Enable click selection
        self.log_display.mousePressEvent = self._on_text_click
        layout.addWidget(self.log_display)

        # Info label
        self.info_label = QtWidgets.QLabel("Click a commit to select")
        layout.addWidget(self.info_label)

        # Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        pull_btn = QtWidgets.QPushButton("Pull selected commit")
        close_btn = QtWidgets.QPushButton("Close")

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
        lines.append("COMMIT HISTORY - Click to select | 'Pull selected commit' to checkout")
        lines.append("OID      DATE/TIME        BRANCH                    AUTHOR               MESSAGE")
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

        for line_num, (short_oid, _) in self._line_to_commit.items():
            if short_oid == self.current_commit:
                cursor = self.log_display.textCursor()
                cursor.movePosition(QtGui.QTextCursor.Start)
                for _ in range(line_num):
                    cursor.movePosition(QtGui.QTextCursor.Down)
                cursor.select(QtGui.QTextCursor.LineUnderCursor)

                fmt = QtGui.QTextCharFormat()
                fmt.setBackground(QtGui.QColor(120, 180, 120))
                fmt.setForeground(QtGui.QColor(0, 0, 0))
                cursor.mergeCharFormat(fmt)
                return

    def _on_text_click(self, event):
        """Handle click on commit line."""
        cursor = self.log_display.cursorForPosition(event.pos())
        line_num = cursor.blockNumber()

        if line_num in self._line_to_commit:
            short_oid, branch = self._line_to_commit[line_num]
            self.selected_commit = short_oid
            self.selected_branch = branch
            self.info_label.setText(f"Selected: {short_oid} from {branch}")

            # Highlight selected line
            self._highlight_line(line_num)

    def _highlight_line(self, line_num):
        """Highlight the selected line and remove previous highlight."""
        cursor = self.log_display.textCursor()

        # Remove previous highlight if exists
        if self._current_highlighted_line is not None:
            cursor.movePosition(QtGui.QTextCursor.Start)
            for _ in range(self._current_highlighted_line):
                cursor.movePosition(QtGui.QTextCursor.Down)
            cursor.select(QtGui.QTextCursor.LineUnderCursor)

            # Clear format
            fmt = QtGui.QTextCharFormat()
            cursor.setCharFormat(fmt)

        # Highlight new line
        cursor = self.log_display.textCursor()
        cursor.movePosition(QtGui.QTextCursor.Start)
        for _ in range(line_num):
            cursor.movePosition(QtGui.QTextCursor.Down)
        cursor.select(QtGui.QTextCursor.LineUnderCursor)

        fmt = QtGui.QTextCharFormat()
        fmt.setBackground(QtGui.QColor(100, 150, 200))
        cursor.mergeCharFormat(fmt)

        self._current_highlighted_line = line_num

    def _on_pull(self):
        if not self.selected_commit:
            QtWidgets.QMessageBox.warning(self, "Git Pull", "Select a commit first.")
            return
        self.close()
        self._do_pull(self.selected_commit, self.selected_branch)

    def _do_pull(self, commit_ref: str, branch_name: str | None = None):
        """Pull the specified commit and track its branch when provided."""
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

        current_oid = _normalize_commit_oid(self.store, self.store.current_commit())
        target_oid = _normalize_commit_oid(self.store, commit_ref)
        if current_oid and target_oid and current_oid == target_oid:
            if branch_name:
                try:
                    self.store.switch_branch(branch_name)
                    _log(f"git pull: switched current branch to {branch_name}")
                except Exception as branch_exc:
                    _log(f"git pull: WARNING - could not switch branch to {branch_name}: {branch_exc}")
            _log(f"git pull: already at {target_oid[:12]}, skipping reload")
            return

        doc_name = self.doc.Name
        FreeCAD.closeDocument(doc_name)
        try:
            oid, touched = workflow.pull_doc(self.store, cache_path, ref=commit_ref)
            if branch_name:
                try:
                    self.store.switch_branch(branch_name)
                    _log(f"git pull: switched current branch to {branch_name}")
                except Exception as branch_exc:
                    _log(f"git pull: WARNING - could not switch branch to {branch_name}: {branch_exc}")
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
            FreeCAD.Console.PrintError(f"Error collecting commits: {e}\n")

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
        dialog = _LogDialog(_mainwindow(), doc, store, log_data, branches, current_commit=current_commit)
        dialog.exec()


if hasattr(FreeCADGui, "addCommand"):
    FreeCADGui.addCommand("Git_Commit", CommitCommand())
    FreeCADGui.addCommand("Git_Pull", PullCommand())
    FreeCADGui.addCommand("Git_Log", LogCommand())
