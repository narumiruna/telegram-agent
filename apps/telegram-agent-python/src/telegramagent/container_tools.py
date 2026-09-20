from __future__ import annotations

import asyncio
import fnmatch
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai import Tool

_CONTAINER_MARKERS = (Path("/.dockerenv"), Path("/run/.containerenv"))
_CONTAINER_CGROUP_MARKERS = ("docker", "containerd", "kubepods")
_DEFAULT_BASH = "/bin/bash"


class ContainerToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContainerToolConfig:
    root: Path
    timeout_seconds: float = 10.0
    max_output_chars: int = 12000
    max_read_chars: int = 20000
    max_results: int = 200


class ContainerToolRuntime:
    def __init__(self, config: ContainerToolConfig) -> None:
        self.config = config
        self.root = config.root.resolve(strict=False)

    async def bash(self, command: str) -> str:
        """Run a non-interactive bash command inside the configured container tool root.

        Do not run interactive editors, pagers, REPLs, long-running services, or commands
        that wait for user input. Output is captured and truncated.
        """
        if not command.strip():
            return "Error: command is empty"
        try:
            process = await asyncio.create_subprocess_exec(
                _DEFAULT_BASH,
                "-lc",
                command,
                cwd=self.root,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return f"Error: bash executable not found: {exc}"
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=self.config.timeout_seconds
            )
        except TimeoutError:
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
            return _truncate(
                _format_command_output(
                    exit_code=None,
                    stdout=stdout_bytes.decode(errors="replace"),
                    stderr=stderr_bytes.decode(errors="replace"),
                    prefix=f"command timed out after {self.config.timeout_seconds:g}s",
                ),
                self.config.max_output_chars,
            )
        return _truncate(
            _format_command_output(
                exit_code=process.returncode,
                stdout=stdout_bytes.decode(errors="replace"),
                stderr=stderr_bytes.decode(errors="replace"),
            ),
            self.config.max_output_chars,
        )

    async def read(self, path: str) -> str:
        """Read a UTF-8 text file inside the configured container tool root."""
        try:
            target = self._resolve_file(path)
            return self._read_limited(target)
        except (ContainerToolError, OSError) as exc:
            return f"Error: {exc}"

    async def write(self, path: str, content: str) -> str:
        """Write UTF-8 text to a file inside the configured container tool root, creating parents."""
        try:
            if len(content) > self.config.max_read_chars:
                raise ContainerToolError(
                    f"content exceeds max write size ({len(content)} > {self.config.max_read_chars} chars)"
                )
            target = self._resolve_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"wrote {len(content)} chars to {self._display_path(target)}"
        except (ContainerToolError, OSError) as exc:
            return f"Error: {exc}"

    async def edit(self, path: str, old_text: str, new_text: str) -> str:
        """Replace one exact text occurrence in a UTF-8 file inside the configured container tool root."""
        try:
            if not old_text:
                raise ContainerToolError("old_text must not be empty")
            target = self._resolve_file(path)
            content = target.read_text(encoding="utf-8")
            count = content.count(old_text)
            if count == 0:
                raise ContainerToolError("old_text was not found")
            if count > 1:
                raise ContainerToolError(f"old_text matched {count} times; expected exactly 1")
            updated = content.replace(old_text, new_text, 1)
            if len(updated) > self.config.max_read_chars:
                raise ContainerToolError(
                    f"edited content exceeds max write size ({len(updated)} > {self.config.max_read_chars} chars)"
                )
            target.write_text(updated, encoding="utf-8")
            return f"edited {self._display_path(target)}"
        except (ContainerToolError, OSError, UnicodeDecodeError) as exc:
            return f"Error: {exc}"

    async def ls(self, path: str = ".") -> str:
        """List a file or directory inside the configured container tool root."""
        try:
            target = self._resolve_path(path)
            if not target.exists():
                raise ContainerToolError("path does not exist")
            if target.is_file():
                return f"{self._display_path(target)}\t{target.stat().st_size} bytes"
            if not target.is_dir():
                return f"{self._display_path(target)}"
            entries = [entry for entry in target.iterdir() if self._is_inside_root(entry)]
            entries = sorted(entries, key=lambda item: (not _is_display_dir(item), item.name.casefold()))
            lines = [_format_entry(entry, root=self.root) for entry in entries[: self.config.max_results]]
            return _join_results(lines, total=len(entries), limit=self.config.max_results)
        except (ContainerToolError, OSError) as exc:
            return f"Error: {exc}"

    async def find(self, path: str = ".", pattern: str = "*", max_results: int | None = None) -> str:
        """Find paths under a directory inside the configured container tool root using a glob pattern."""
        try:
            self._validate_glob_pattern(pattern)
            limit = self._result_limit(max_results)
            target = self._resolve_path(path)
            if not target.exists():
                raise ContainerToolError("path does not exist")
            if target.is_file():
                matches = [target] if fnmatch.fnmatch(target.name, pattern) else []
            elif target.is_dir():
                matches = sorted(
                    (match for match in target.rglob(pattern) if self._is_inside_root(match)),
                    key=lambda item: self._display_path(item),
                )
            else:
                matches = []
            lines = [self._display_path(match) + ("/" if _is_display_dir(match) else "") for match in matches[:limit]]
            return _join_results(lines, total=len(matches), limit=limit)
        except (ContainerToolError, OSError) as exc:
            return f"Error: {exc}"

    async def grep(
        self,
        pattern: str,
        path: str = ".",
        glob: str = "*",
        ignore_case: bool = False,
        max_results: int | None = None,
    ) -> str:
        """Search UTF-8 text files under the configured container tool root using a regular expression."""
        try:
            self._validate_glob_pattern(glob)
            flags = re.IGNORECASE if ignore_case else 0
            regex = re.compile(pattern, flags=flags)
            limit = self._result_limit(max_results)
            target = self._resolve_path(path)
            if not target.exists():
                raise ContainerToolError("path does not exist")
            files = (
                [target] if target.is_file() else sorted(target.rglob(glob), key=lambda item: self._display_path(item))
            )
            results: list[str] = []
            total_matches = 0
            for file_path in files:
                if not file_path.is_file() or not self._is_inside_root(file_path):
                    continue
                for line_number, line in self._matching_lines(file_path, regex):
                    total_matches += 1
                    if len(results) < limit:
                        results.append(f"{self._display_path(file_path)}:{line_number}:{line}")
            return _join_results(results, total=total_matches, limit=limit)
        except (ContainerToolError, OSError, re.error) as exc:
            return f"Error: {exc}"

    def _matching_lines(self, path: Path, regex: re.Pattern[str]) -> Iterable[tuple[int, str]]:
        read_chars = 0
        with path.open(encoding="utf-8", errors="replace") as file:
            for line_number, line in enumerate(file, start=1):
                read_chars += len(line)
                if read_chars > self.config.max_read_chars:
                    break
                stripped = line.rstrip("\n")
                if regex.search(stripped):
                    yield line_number, stripped

    def _read_limited(self, path: Path) -> str:
        with path.open(encoding="utf-8", errors="replace") as file:
            content = file.read(self.config.max_read_chars + 1)
        if len(content) <= self.config.max_read_chars:
            return content
        return f"{content[: self.config.max_read_chars]}\n[truncated by telegramagent container read limit]"

    def _resolve_file(self, path: str) -> Path:
        target = self._resolve_path(path)
        if not target.exists():
            raise ContainerToolError("file does not exist")
        if not target.is_file():
            raise ContainerToolError("path is not a file")
        return target

    def _resolve_path(self, path: str) -> Path:
        if not path.strip():
            raise ContainerToolError("path is empty")
        raw_path = Path(path)
        target = raw_path if raw_path.is_absolute() else self.root / raw_path
        resolved = target.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise ContainerToolError(f"path escapes container tool root: {path}")
        return resolved

    def _display_path(self, path: Path) -> str:
        if path == self.root:
            return "."
        return path.relative_to(self.root).as_posix()

    def _validate_glob_pattern(self, pattern: str) -> None:
        if not pattern:
            raise ContainerToolError("pattern must not be empty")
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            raise ContainerToolError("pattern must stay inside the container tool root")

    def _is_inside_root(self, path: Path) -> bool:
        return path.resolve(strict=False).is_relative_to(self.root)

    def _result_limit(self, requested: int | None) -> int:
        if requested is None:
            return self.config.max_results
        return max(1, min(requested, self.config.max_results))


def build_container_tools(config: ContainerToolConfig) -> tuple[Tool[Any], ...]:
    runtime = ContainerToolRuntime(config)
    return (
        Tool(runtime.bash, name="bash", description="Run a bounded non-interactive bash command in the container."),
        Tool(runtime.edit, name="edit", description="Replace one exact text occurrence in a file under the tool root."),
        Tool(
            runtime.find, name="find", description="Find files or directories under the tool root using a glob pattern."
        ),
        Tool(
            runtime.grep, name="grep", description="Search text files under the tool root using a regular expression."
        ),
        Tool(runtime.ls, name="ls", description="List files or directories under the tool root."),
        Tool(runtime.read, name="read", description="Read a UTF-8 text file under the tool root."),
        Tool(runtime.write, name="write", description="Write a UTF-8 text file under the tool root."),
    )


def is_running_in_container(
    *, marker_paths: Iterable[Path] = _CONTAINER_MARKERS, cgroup_path: Path = Path("/proc/1/cgroup")
) -> bool:
    if any(path.exists() for path in marker_paths):
        return True
    try:
        cgroup = cgroup_path.read_text(encoding="utf-8", errors="ignore").casefold()
    except OSError:
        return False
    return any(marker in cgroup for marker in _CONTAINER_CGROUP_MARKERS)


def _format_command_output(*, exit_code: int | None, stdout: str, stderr: str, prefix: str = "") -> str:
    parts = []
    if prefix:
        parts.append(prefix)
    if exit_code is not None:
        parts.append(f"exit_code: {exit_code}")
    if stdout:
        parts.append(f"stdout:\n{stdout.rstrip()}")
    if stderr:
        parts.append(f"stderr:\n{stderr.rstrip()}")
    return "\n".join(parts) or "exit_code: 0"


def _format_entry(path: Path, *, root: Path) -> str:
    relative = "." if path == root else path.relative_to(root).as_posix()
    suffix = "/" if _is_display_dir(path) else ""
    size = "" if _is_display_dir(path) else f"\t{path.lstat().st_size} bytes"
    return f"{relative}{suffix}{size}"


def _is_display_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _join_results(lines: list[str], *, total: int, limit: int) -> str:
    if not lines and total == 0:
        return "no matches"
    if total > limit:
        return "\n".join([*lines, f"[truncated by telegramagent: {total} results -> {limit} results]"])
    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[truncated by telegramagent: {len(text)} -> {limit} chars]"
