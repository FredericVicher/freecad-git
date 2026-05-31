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

    def __init__(self, parent, store, current_branch, needs_new_branch):
        super().__init__(parent)
        self.store = store
        self.current_branch = current_branch
        self.needs_new_branch = needs_new_branch
        self.selected_branch = current_branch
        self.commit_message = ""
        self.create_new = needs_new_branch
        self.new_branch_name = ""
        self.setWindowTitle("Git Commit")
        self.setMinimumWidth(400)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout()

        # Current branch display
        info_label = QtWidgets.QLabel(f"Current branch: {self.current_branch}")
        info_font = info_label.font()
        info_font.setBold(True)
        info_label.setFont(info_font)
        layout.addWidget(info_label)

        # If needs new branch, show that info and ask for branch name
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
        else:
            self.new_branch_input = None

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

        if self.needs_new_branch:
            new_name = self.new_branch_input.text().strip()
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
        current_oid = store.current_commit()

        # Check if current commit is the HEAD of the current branch
        # Need to create a branch only if committing on a non-HEAD commit
        needs_new_branch = False
        if current_oid and current_branch:
            current_short = current_oid[:8]
            try:
                # Get the HEAD of the current branch
                branch_ref = f"refs/heads/{current_branch}"
                if branch_ref in store.repo.references:
                    branch_head = str(store.repo.references[branch_ref].target)[:8]
                    # If current commit is not the branch HEAD, need a new branch
                    if current_short != branch_head:
                        needs_new_branch = True
            except Exception:
                pass

        # Show commit dialog
        dialog = _CommitDialog(_mainwindow(), store, current_branch, needs_new_branch)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            _log("git commit: cancelled by user")
            return

        message = dialog.commit_message
        author = _resolve_author()

        # Determine which branch this commit will be on
        target_branch = dialog.new_branch_name if dialog.create_new else current_branch

        # Add branch name to commit message for tracking
        message_with_branch = f"{message}\n\n[branch: {target_branch}]"

        try:
            oid = workflow.commit_doc(doc, store, message_with_branch, author)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(_mainwindow(), "Git Commit", str(exc))
            return

        # Create new branch if needed
        if dialog.create_new:
            try:
                store.create_branch(dialog.new_branch_name, "HEAD")
                store.switch_branch(dialog.new_branch_name)
                _log(f"git branch: created '{dialog.new_branch_name}' at {oid[:12]}")
            except Exception as exc:
                _log(f"git branch: WARNING - could not create '{dialog.new_branch_name}': {exc}")

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

    def __init__(self, parent, doc, store, log_data, branches):
        super().__init__(parent)
        self.doc = doc
        self.store = store
        self.log_data = log_data  # List of (short_oid, author, summary, branch, parents, is_branch_start, child_count)
        self.branches = branches  # List of branch names
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
        lines.append("=" * 100)

        for i, (short_oid, author, summary, branch, parents, is_branch_start, child_count) in enumerate(self.log_data):
            # Show parent reference only if this commit creates a branch divergence
            parent_str = ""
            if is_branch_start and parents:
                parent_str = f" <- {parents[0]}"

            # Create commit line with branch info and parent reference
            branch_info = f"[{branch}{parent_str}]"
            commit_line = f"{short_oid} {branch_info:<25s} {author[:20]:20s} {summary[:50]}"

            lines.append(commit_line)

            # Map line number (in the text display) to commit info for click handling
            # Line number is 3 (after headers) + i
            line_num = 3 + i
            self._line_to_commit[line_num] = (short_oid, branch)

        lines.append("=" * 100)

        return "\n".join(lines)


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
        self._do_pull(self.selected_commit)

    def _do_pull(self, commit_ref: str):
        """Pull the specified commit."""
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

        branches = store.list_branches()

        # Collect all commits and their containing branches in one pass
        all_commits_info = {}  # short_oid -> (author, summary, parents, timestamp)
        commit_to_branches = {}  # short_oid -> set of branch names

        try:
            # Walk each branch once, collect all commits and their branch membership
            for branch in branches:
                branch_ref = f"refs/heads/{branch}"
                if branch_ref not in store.repo.references:
                    continue
                target = store.repo.references[branch_ref].target

                for commit in store.repo.walk(target, pygit2.GIT_SORT_TIME):
                    short_oid = str(commit.id)[:8]

                    # Collect commit info (only once)
                    if short_oid not in all_commits_info:
                        author = commit.author.name
                        summary = commit.message.splitlines()[0] if commit.message else ""
                        parents = [str(p)[:8] for p in commit.parent_ids]
                        timestamp = commit.commit_time
                        all_commits_info[short_oid] = (author, summary, parents, timestamp)

                    # Track which branches contain this commit
                    if short_oid not in commit_to_branches:
                        commit_to_branches[short_oid] = set()
                    commit_to_branches[short_oid].add(branch)

        except Exception as e:
            FreeCAD.Console.PrintError(f"Error collecting commits: {e}\n")

        # Build map of branch HEADs
        branch_heads = {}  # short_oid -> branch_name
        for branch in branches:
            branch_ref = f"refs/heads/{branch}"
            if branch_ref in store.repo.references:
                head_oid = str(store.repo.references[branch_ref].target)[:8]
                branch_heads[head_oid] = branch

        # Extract branch info from commit messages (stored as [branch: name] in message)
        commit_branch_from_message = {}  # short_oid -> branch_name
        for short_oid, (author, summary, parents, timestamp) in all_commits_info.items():
            try:
                commit = store.repo[short_oid]
                if commit and commit.message:
                    # Look for [branch: name] pattern in commit message
                    for line in commit.message.split('\n'):
                        if line.strip().startswith('[branch:') and line.strip().endswith(']'):
                            branch_name = line.strip()[8:-1].strip()  # Extract name from [branch: name]
                            if branch_name:
                                commit_branch_from_message[short_oid] = branch_name
                                break
            except Exception:
                pass

        # Assign each commit to exactly one branch
        # Priority: 1) branch from commit message, 2) if it's a branch HEAD, use that branch, 3) default to main
        # (commits without [branch: name] tag are from before branch tracking was added, they were all on main)
        oid_to_branch = {}
        for short_oid in all_commits_info.keys():
            # First: check if branch is stored in commit message
            if short_oid in commit_branch_from_message:
                oid_to_branch[short_oid] = commit_branch_from_message[short_oid]
            # Second: check if this is a branch HEAD
            elif short_oid in branch_heads:
                oid_to_branch[short_oid] = branch_heads[short_oid]
            else:
                # Old commits without [branch: name] tag default to main
                oid_to_branch[short_oid] = "main"

        # Calculate is_branch_start: True if branch differs from parent's branch
        is_branch_start_map = {}
        for short_oid, (author, summary, parents, timestamp) in all_commits_info.items():
            is_branch_start = False
            if parents:
                parent_oid = parents[0]
                parent_branch = oid_to_branch.get(parent_oid)
                current_branch = oid_to_branch.get(short_oid)
                if parent_branch and current_branch and parent_branch != current_branch:
                    is_branch_start = True
            is_branch_start_map[short_oid] = is_branch_start

        # Count children for each commit
        commit_children = {}
        for short_oid, (author, summary, parents, timestamp) in all_commits_info.items():
            for parent_oid in parents:
                if parent_oid not in commit_children:
                    commit_children[parent_oid] = []
                commit_children[parent_oid].append(short_oid)

        # Build log data
        log_data = []
        for short_oid, (author, summary, parents, timestamp) in all_commits_info.items():
            branch = oid_to_branch.get(short_oid, 'main')
            is_branch_start = is_branch_start_map.get(short_oid, False)
            child_count = len(commit_children.get(short_oid, []))
            log_data.append((short_oid, author, summary, branch, parents, is_branch_start, child_count))

        # Sort by timestamp (newest first)
        log_data.sort(key=lambda x: all_commits_info[x[0]][3], reverse=True)

        dialog = _LogDialog(_mainwindow(), doc, store, log_data, branches)
        dialog.exec()


if hasattr(FreeCADGui, "addCommand"):
    FreeCADGui.addCommand("Git_Commit", CommitCommand())
    FreeCADGui.addCommand("Git_Pull", PullCommand())
    FreeCADGui.addCommand("Git_Log", LogCommand())
