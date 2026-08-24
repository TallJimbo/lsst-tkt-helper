# Copyright 2020-2026 Jim Bosch
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from __future__ import annotations

from tkt._environment import Tool
from tkt.openspec import OpenSpec


def test_tool_default_remove_is_noop(tmp_path):
    """Ensure the default ``Tool.remove`` is a no-op that does not raise."""

    class _Tool(Tool):
        @classmethod
        def from_json_data(cls, data):
            return cls()

        def write(self, ticket, directory, packages, workspace, environment):
            pass

    tool = _Tool()
    tool.remove(str(tmp_path))  # must not raise
    assert list(tmp_path.iterdir()) == []


def test_tool_default_eups_env_lines_empty():
    """Ensure the default ``Tool.eups_env_lines`` returns an empty tuple."""

    class _Tool(Tool):
        @classmethod
        def from_json_data(cls, data):
            return cls()

        def write(self, ticket, directory, packages, workspace, environment):
            pass

    assert _Tool().eups_env_lines("DM-1") == ()


def test_openspec_remove_cleans_artifacts(tmp_path):
    """Ensure OpenSpec artifacts are removed; unrelated skills are kept."""
    (tmp_path / "openspec").mkdir()
    (tmp_path / "openspec" / "config.yaml").write_text("x")
    skills = tmp_path / ".opencode" / "skills"
    (skills / "openspec-apply-change" / "SKILL.md").parent.mkdir(parents=True)
    (skills / "openspec-apply-change" / "SKILL.md").write_text("x")
    (skills / "other" / "SKILL.md").parent.mkdir(parents=True)
    (skills / "other" / "SKILL.md").write_text("x")
    OpenSpec(store="lsst").remove(str(tmp_path))
    assert not (tmp_path / "openspec").exists()
    assert not (skills / "openspec-apply-change").exists()
    assert (skills / "other").exists()  # unrelated skills kept


def test_openspec_remove_empties_skills_dir(tmp_path):
    """Ensure the skills dir is removed once it becomes empty."""
    (tmp_path / "openspec").mkdir()
    skills = tmp_path / ".opencode" / "skills"
    (skills / "openspec-apply-change" / "SKILL.md").parent.mkdir(parents=True)
    (skills / "openspec-apply-change" / "SKILL.md").write_text("x")
    OpenSpec(store="lsst").remove(str(tmp_path))
    assert not skills.exists()  # removed once empty


def test_openspec_remove_missing_ok(tmp_path):
    """Ensure remove is a no-op when no artifacts are present."""
    OpenSpec(store="lsst").remove(str(tmp_path))  # no error if nothing present
