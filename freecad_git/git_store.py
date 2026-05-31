"""Selective git storage for FreeCAD documents.

A `GitStore` wraps a pygit2 Repository and commits Document.xml + a curated
set of .brp blobs as a flat tree, directly in the object database. No
working tree is used; all I/O is in-memory.

The repository is created bare on first use. Push/fetch with remotes works
normally; users never need to manipulate the working tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pygit2  # type: ignore[import-not-found]


DEFAULT_BRANCH = "main"


@dataclass(frozen=True)
class Author:
    name: str
    email: str

    def signature(self) -> "pygit2.Signature":
        return pygit2.Signature(self.name, self.email)


class GitStore:
    """In-memory git interface for selective FreeCAD versioning with multi-branch support."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        discovered = pygit2.discover_repository(str(self.path)) if self.path.exists() else None
        if discovered:
            self.repo = pygit2.Repository(discovered)
        else:
            self.path.mkdir(parents=True, exist_ok=True)
            self.repo = pygit2.init_repository(str(self.path), bare=True)
            # libgit2 defaults HEAD to refs/heads/master; we commit on
            # refs/heads/main, so point HEAD at our branch from the start.
            try:
                self.repo.set_head(self._branch_ref(DEFAULT_BRANCH))
            except Exception:
                # If the branch doesn't exist yet, create a symbolic ref
                # It will become real after the first commit on this branch
                pass

    def _branch_ref(self, branch_name: str) -> str:
        """Convert branch name to full ref path."""
        return f"refs/heads/{branch_name}"

    def _current_branch_name(self) -> str:
        """Get the name of the currently tracked branch."""
        marker = self.path / "CURRENT_BRANCH"
        if marker.exists():
            return marker.read_text().strip()
        return DEFAULT_BRANCH

    def _set_current_branch_name(self, name: str) -> None:
        """Persist the current branch name."""
        (self.path / "CURRENT_BRANCH").write_text(name)

    @property
    def has_head(self) -> bool:
        current_branch = self._current_branch_name()
        return self._branch_ref(current_branch) in self.repo.references

    def current_branch(self) -> str:
        """Return the name of the currently tracked branch."""
        return self._current_branch_name()

    def current_commit(self) -> str | None:
        """Return the currently loaded commit OID (from pull), or None."""
        marker = self.path / "CURRENT_COMMIT"
        if marker.exists():
            return marker.read_text().strip()
        return None

    def head_oid(self) -> str | None:
        if not self.has_head:
            return None
        current_branch = self._current_branch_name()
        return str(self.repo.references[self._branch_ref(current_branch)].target)

    def list_branches(self) -> list[str]:
        """Return all branch names (sorted, main first if it exists)."""
        branches = []
        for ref in self.repo.references:
            if ref.startswith("refs/heads/"):
                name = ref[len("refs/heads/"):]
                branches.append(name)

        # Sort with 'main' first, then alphabetically
        branches.sort()
        if DEFAULT_BRANCH in branches:
            branches.remove(DEFAULT_BRANCH)
            branches.insert(0, DEFAULT_BRANCH)
        return branches

    def create_branch(self, name: str, from_ref: str = "HEAD") -> str:
        """Create a new branch at the given ref, return the full branch ref.

        Note: Should be called after at least one commit exists, so from_ref can be resolved.
        """
        branch_ref = self._branch_ref(name)

        try:
            target_oid = self.repo.revparse_single(from_ref).peel(pygit2.Commit).id
        except KeyError:
            # Fall back to current branch HEAD if from_ref doesn't exist
            current_ref = self._branch_ref(self._current_branch_name())
            try:
                target_oid = self.repo.references[current_ref].target
            except KeyError:
                raise ValueError(f"Cannot resolve ref {from_ref} or current branch {current_ref}")

        self.repo.references.create(branch_ref, str(target_oid))
        return branch_ref

    def switch_branch(self, name: str) -> None:
        """Switch to a different branch."""
        branch_ref = self._branch_ref(name)
        if branch_ref not in self.repo.references:
            raise ValueError(f"Branch {name} does not exist")
        self._set_current_branch_name(name)
        self.repo.set_head(branch_ref)

    def resolve_ref(self, ref: str) -> str:
        """Return the commit OID hex string for the given ref (HEAD, branch, oid).

        Falls back to the tracked branch when "HEAD" points at a non-existent
        ref (e.g. legacy repos initialized with HEAD->refs/heads/master while
        we commit on refs/heads/main).
        """
        try:
            return str(self.repo.revparse_single(ref).peel(pygit2.Commit).id)
        except KeyError:
            if ref == "HEAD" and self.has_head:
                # Re-point HEAD so future lookups are consistent.
                current_branch = self._current_branch_name()
                self.repo.set_head(self._branch_ref(current_branch))
                return str(self.repo.references[self._branch_ref(current_branch)].target)
            raise

    def set_branch_to(self, oid: str) -> None:
        """Move the tracked branch to the given commit OID (destructive)."""
        current_branch = self._current_branch_name()
        branch_ref = self._branch_ref(current_branch)
        if self.has_head:
            self.repo.references[branch_ref].set_target(oid)
        else:
            self.repo.references.create(branch_ref, oid)
        self.repo.set_head(branch_ref)

    def commit(
        self,
        *,
        document_xml: bytes,
        blobs: Mapping[str, bytes],
        message: str,
        author: Author,
    ) -> str:
        """Build a flat tree from the given content and commit it.

        The same `author` is used as committer. Returns the new commit OID
        as a hex string. If a commit already exists on the branch, it is
        used as the parent.
        """
        builder = self.repo.TreeBuilder()
        builder.insert(
            "Document.xml",
            self.repo.create_blob(document_xml),
            pygit2.GIT_FILEMODE_BLOB,
        )
        for filename, data in blobs.items():
            builder.insert(
                filename,
                self.repo.create_blob(data),
                pygit2.GIT_FILEMODE_BLOB,
            )
        tree_oid = builder.write()

        sig = author.signature()
        current_branch = self._current_branch_name()
        branch_ref = self._branch_ref(current_branch)
        parents = [self.repo.references[branch_ref].target] if self.has_head else []
        commit_oid = self.repo.create_commit(
            branch_ref, sig, sig, message, tree_oid, parents,
        )

        # Ensure reference is visible in repo.references for bare repos
        # pygit2 may not immediately update references on bare repos
        try:
            if branch_ref not in self.repo.references:
                self.repo.references.create(branch_ref, str(commit_oid))
        except (KeyError, ValueError):
            # Reference might already exist, that's fine
            pass

        return str(commit_oid)

    def read_tree_at(self, ref: str | None = None) -> tuple[bytes, dict[str, bytes]]:
        """Return (Document.xml bytes, {filename: bytes}) for the given ref.

        If ref is None, uses the current branch.
        """
        if ref is None:
            ref = self._branch_ref(self._current_branch_name())
        commit = self.repo.revparse_single(ref).peel(pygit2.Commit)
        tree = commit.tree
        xml = bytes(self.repo[tree["Document.xml"].id].data)
        blobs: dict[str, bytes] = {}
        for entry in tree:
            if entry.name == "Document.xml":
                continue
            blobs[entry.name] = bytes(self.repo[entry.id].data)
        return xml, blobs

    def document_xml_at(self, ref: str | None = None) -> bytes:
        """Return Document.xml bytes at the given ref (cheaper than read_tree_at).

        If ref is None, uses the current branch.
        """
        if ref is None:
            ref = self._branch_ref(self._current_branch_name())
        commit = self.repo.revparse_single(ref).peel(pygit2.Commit)
        return bytes(self.repo[commit.tree["Document.xml"].id].data)

    def diff_paths_between(self, old_ref: str, new_ref: str) -> list[str]:
        """Return the list of tree entries that differ between two commits."""
        old_tree = self.repo.revparse_single(old_ref).peel(pygit2.Commit).tree
        new_tree = self.repo.revparse_single(new_ref).peel(pygit2.Commit).tree
        diff = old_tree.diff_to_tree(new_tree)
        return [p.delta.new_file.path or p.delta.old_file.path for p in diff]
