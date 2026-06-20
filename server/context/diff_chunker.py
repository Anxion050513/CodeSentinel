"""Diff chunker — splits a git diff into semantically meaningful chunks.

Chunks are split on file boundaries within a diff. Within each file,
chunks are split on hunk boundaries (git's own diff structure).
"""
import logging
import re

logger = logging.getLogger(__name__)


class DiffChunker:
    """Splits a unified diff into reviewable chunks.

    Each chunk targets one file's changes. Large files are further
    split by hunk. The goal is to keep each chunk small enough to
    fit in an LLM context window while maintaining semantic coherence.
    """

    # Max tokens per chunk (approx 4 chars = 1 token, leave room for prompt + response)
    MAX_CHUNK_CHARS = 8000

    def split(self, diff_text: str, pr_files: list[dict] | None = None) -> list[dict]:
        """Split a unified diff into reviewable chunks.

        Args:
            diff_text: Raw unified diff from GitHub
            pr_files: Optional list of changed files with metadata

        Returns:
            List of chunk dicts with: file_path, content, line_start, line_end,
            patch_position, additions, deletions
        """
        if not diff_text.strip():
            return []

        # Split by file headers (diff --git a/... b/...)
        file_sections = self._split_by_file(diff_text)

        chunks = []
        for file_path, content, start_line in file_sections:
            # Find file metadata from pr_files if available
            meta = self._find_file_meta(file_path, pr_files) if pr_files else {}

            if len(content) <= self.MAX_CHUNK_CHARS:
                chunks.append({
                    "file_path": file_path,
                    "content": content,
                    "line_start": start_line,
                    "line_end": start_line + content.count("\n"),
                    "additions": meta.get("additions", content.count("\n+")),
                    "deletions": meta.get("deletions", content.count("\n-")),
                    "status": meta.get("status", "modified"),
                })
            else:
                # Split large file by hunks
                sub_chunks = self._split_by_hunk(file_path, content, start_line, meta)
                chunks.extend(sub_chunks)

        logger.info("Diff split into %d chunks across %d files", len(chunks), len(file_sections))
        return chunks

    def _split_by_file(self, diff_text: str) -> list[tuple[str, str, int]]:
        """Split diff into per-file sections."""
        # Match diff --git a/<path> b/<path> headers
        file_pattern = re.compile(
            r'^diff --git a/(.+?) b/(.+?)$', re.MULTILINE
        )

        # Find all file boundaries
        boundaries = []
        for m in file_pattern.finditer(diff_text):
            boundaries.append((m.start(), m.group(1)))

        sections = []
        for i, (start, file_path) in enumerate(boundaries):
            end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(diff_text)
            content = diff_text[start:end].strip()

            # Extract the starting line number from @@ headers
            start_line = 0
            hunk_match = re.search(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@', content, re.MULTILINE)
            if hunk_match:
                start_line = int(hunk_match.group(1))

            sections.append((file_path, content, start_line))

        return sections

    def _split_by_hunk(
        self, file_path: str, content: str, base_line: int, meta: dict
    ) -> list[dict]:
        """Split a large file diff into per-hunk chunks."""
        hunk_pattern = re.compile(
            r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@.*$', re.MULTILINE
        )

        header_end = 0
        # Find where the diff header ends (after index line)
        header_match = re.search(r'^@@.*@@\s*\n', content, re.MULTILINE)
        if header_match:
            header_end = header_match.end()

        diff_header = content[:header_end]

        hunks = []
        current_chunk = [diff_header]
        current_size = len(diff_header)
        current_line = base_line

        lines = content[header_end:].split("\n")
        hunk_groups = []
        current_group = []

        for line in lines:
            # Check for hunk header
            hunk_match = hunk_pattern.match(line)
            if hunk_match and current_group:
                hunk_groups.append(current_group)
                current_group = [line]
            else:
                current_group.append(line)

        if current_group:
            hunk_groups.append(current_group)

        # Now merge hunk groups into size-limited chunks
        for group in hunk_groups:
            group_text = diff_header + "\n".join(group)
            group_size = len(group_text)

            # Extract the line start from the hunk header
            line_start = current_line

            if current_size + group_size > self.MAX_CHUNK_CHARS and current_chunk:
                # Flush current chunk
                chunk_text = "\n".join(current_chunk)
                hunks.append({
                    "file_path": file_path,
                    "content": chunk_text,
                    "line_start": line_start,
                    "line_end": line_start + chunk_text.count("\n"),
                    "additions": meta.get("additions", 0),
                    "deletions": meta.get("deletions", 0),
                    "status": meta.get("status", "modified"),
                })
                current_chunk = [diff_header]
                current_size = len(diff_header)

            current_chunk.extend(group)
            current_size += group_size

            # Update line tracker
            for gline in group:
                if not gline.startswith("-"):
                    current_line += 1

        # Flush remaining
        if len(current_chunk) > 1:
            chunk_text = "\n".join(current_chunk)
            hunks.append({
                "file_path": file_path,
                "content": chunk_text,
                "line_start": line_start,
                "line_end": line_start + chunk_text.count("\n"),
                "additions": meta.get("additions", 0),
                "deletions": meta.get("deletions", 0),
                "status": meta.get("status", "modified"),
            })

        return hunks if hunks else [{
            "file_path": file_path,
            "content": content,
            "line_start": base_line,
            "line_end": base_line + content.count("\n"),
            "additions": meta.get("additions", 0),
            "deletions": meta.get("deletions", 0),
            "status": meta.get("status", "modified"),
        }]

    def _find_file_meta(self, file_path: str, pr_files: list[dict]) -> dict:
        """Find metadata for a file from the PR files list."""
        for f in pr_files:
            if f.get("filename") == file_path:
                return {
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0),
                    "status": f.get("status", "modified"),
                }
        return {}
