from __future__ import annotations

from pathlib import Path

import pytest

from telegramagent.container_tools import ContainerToolConfig
from telegramagent.container_tools import ContainerToolRuntime
from telegramagent.container_tools import build_container_tools
from telegramagent.container_tools import is_running_in_container


@pytest.mark.asyncio
async def test_container_tools_read_write_edit_and_reject_path_escape(tmp_path: Path) -> None:
    runtime = ContainerToolRuntime(ContainerToolConfig(root=tmp_path, max_read_chars=100))

    assert await runtime.write("dir/file.txt", "hello old") == "wrote 9 chars to dir/file.txt"
    assert await runtime.read("dir/file.txt") == "hello old"
    assert await runtime.edit("dir/file.txt", "old", "new") == "edited dir/file.txt"
    assert await runtime.read("dir/file.txt") == "hello new"

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "outside-link.txt").symlink_to(outside)

    assert (await runtime.read("../outside.txt")).startswith("Error: path escapes container tool root")
    assert (await runtime.read("outside-link.txt")).startswith("Error: path escapes container tool root")
    assert (await runtime.write(str(outside), "x")).startswith("Error: path escapes container tool root")


@pytest.mark.asyncio
async def test_container_tools_edit_requires_one_exact_match(tmp_path: Path) -> None:
    runtime = ContainerToolRuntime(ContainerToolConfig(root=tmp_path))
    (tmp_path / "file.txt").write_text("same same", encoding="utf-8")

    assert await runtime.edit("file.txt", "missing", "x") == "Error: old_text was not found"
    assert await runtime.edit("file.txt", "same", "x") == "Error: old_text matched 2 times; expected exactly 1"


@pytest.mark.asyncio
async def test_container_tools_ls_find_and_grep_are_sandboxed_and_bounded(tmp_path: Path) -> None:
    runtime = ContainerToolRuntime(ContainerToolConfig(root=tmp_path, max_results=2))
    (tmp_path / "b.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("nope\nneedle again\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.md").write_text("needle in markdown\n", encoding="utf-8")

    assert await runtime.ls(".") == "sub/\na.txt\t18 bytes\n[truncated by telegramagent: 3 results -> 2 results]"
    assert await runtime.find(".", "*.txt") == "a.txt\nb.txt"
    assert await runtime.find("../outside", "*") == "Error: path escapes container tool root: ../outside"
    assert await runtime.find(".", "../*") == "Error: pattern must stay inside the container tool root"

    grep_result = await runtime.grep("needle", ".", "*")

    assert grep_result == ("a.txt:2:needle again\nb.txt:1:needle\n[truncated by telegramagent: 3 results -> 2 results]")
    assert await runtime.grep("needle", ".", "../*") == "Error: pattern must stay inside the container tool root"


@pytest.mark.asyncio
async def test_container_tools_read_and_bash_truncate_output(tmp_path: Path) -> None:
    runtime = ContainerToolRuntime(ContainerToolConfig(root=tmp_path, max_output_chars=20, max_read_chars=5))
    (tmp_path / "long.txt").write_text("abcdef", encoding="utf-8")

    assert await runtime.read("long.txt") == "abcde\n[truncated by telegramagent container read limit]"
    bash_output = await runtime.bash("printf 'abcdefghijklmnopqrstuvwxyz'")
    assert bash_output.startswith("exit_code: 0\nstdout:")
    assert "[truncated by telegramagent:" in bash_output


@pytest.mark.asyncio
async def test_container_tools_bash_timeout(tmp_path: Path) -> None:
    timeout_runtime = ContainerToolRuntime(ContainerToolConfig(root=tmp_path, timeout_seconds=0.05))
    assert (await timeout_runtime.bash("sleep 1")).startswith("command timed out after 0.05s")


def test_build_container_tools_exposes_requested_tool_names(tmp_path: Path) -> None:
    tools = build_container_tools(ContainerToolConfig(root=tmp_path))

    assert [tool.name for tool in tools] == ["bash", "edit", "find", "grep", "ls", "read", "write"]


def test_container_detection_uses_marker_or_cgroup(tmp_path: Path) -> None:
    marker = tmp_path / ".dockerenv"
    cgroup = tmp_path / "cgroup"

    cgroup.write_text("0::/user.slice\n", encoding="utf-8")
    assert is_running_in_container(marker_paths=[marker], cgroup_path=cgroup) is False

    marker.write_text("", encoding="utf-8")
    assert is_running_in_container(marker_paths=[marker], cgroup_path=cgroup) is True

    marker.unlink()
    cgroup.write_text("0::/docker/test\n", encoding="utf-8")
    assert is_running_in_container(marker_paths=[marker], cgroup_path=cgroup) is True
