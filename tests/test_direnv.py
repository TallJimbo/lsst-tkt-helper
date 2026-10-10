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

import json
import os
import re

import pytest
from click.testing import CliRunner

from tkt._cli import cli
from tkt.direnv import DirEnv

_SETUP_MARKER = "TKT_TEST_SETUP_MARKER"
_SCRIPT_MARKER = "TKT_TEST_SCRIPT_MARKER"


def _fake_product(tmp_path, monkeypatch, *, capture_fails: bool = False) -> str:
    """Create a fake EUPS product checkout; return the capture script path.

    Builds ``ups/`` (marking the directory as an EUPS product), a fake
    ``setup`` executable prepended to ``PATH``, and a fake ``loadLSST``
    script to source in the capture subprocess.  Like the real EUPS ``setup``
    (a shell function), the fake script defines a ``setup`` function that
    exports a marker variable into the capture shell — an executable could
    not, since subprocess exports do not propagate to the parent.  The
    failing variant instead lets the fake executable fail (nonzero exit plus
    stderr) with ``set -e`` in the sourced script to abort the capture.
    ``XDG_DATA_HOME`` (direnv <= 2.32) and ``XDG_STATE_HOME`` (direnv >=
    2.33) are redirected into ``tmp_path`` so a successful ``direnv allow``
    never touches the real allow-list
    (``~/.local/share/direnv`` or ``~/.local/state/direnv``).
    """
    (tmp_path / "ups").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    setup = bin_dir / "setup"
    if capture_fails:
        setup.write_text('#!/bin/bash\necho "setup exploded" >&2\nexit 1\n')
    else:
        # Never runs on the success path (the function below shadows it);
        # a harmless fallback that keeps `setup -r .` resolvable.
        setup.write_text("#!/bin/bash\nexit 0\n")
    setup.chmod(0o755)
    # _pristine_env copies PATH from os.environ, so the fake setup is found.
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    script = tmp_path / "loadLSST.bash"
    if capture_fails:
        # The capture command has no `set -e`, so a nonzero exit from the
        # failing setup alone would not fail the capture (bash exits with the
        # status of the last command, `env`).  Sourcing `set -e` makes the
        # capture shell abort as soon as setup fails.
        script.write_text("set -e\n")
    else:
        script.write_text(
            f"export {_SCRIPT_MARKER}=script_value\nsetup() {{ export {_SETUP_MARKER}=setup_value; }}\n"
        )
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    return str(script)


def _env_config(tmp_path, script: str) -> str:
    """Write a minimal tkt environment JSON configuring the direnv tool."""
    repos_yaml = tmp_path / "repos.yaml"
    repos_yaml.write_text("{}\n")
    config = {
        "module": "tkt.rubin",
        "cls": "RubinEnvironment",
        "workspace_path": str(tmp_path),
        "default_tools": [],
        "repos_yaml": str(repos_yaml),
        "tools": {
            "direnv": {
                "module": "tkt.direnv",
                "cls": "DirEnv",
                "scripts": [script],
                "env": [],
            }
        },
    }
    env_file = tmp_path / "env.json"
    env_file.write_text(json.dumps(config))
    return str(env_file)


def test_direnv_write_envrc_captures_exports(tmp_path, monkeypatch):
    """write_envrc captures the fake exports as sorted lines."""
    script = _fake_product(tmp_path, monkeypatch)
    count = DirEnv([script], []).write_envrc(str(tmp_path), "/bin/bash")
    lines = (tmp_path / ".envrc").read_text().splitlines()
    assert count == len(lines)
    assert all(line.startswith("export ") for line in lines)
    assert f"export {_SETUP_MARKER}=setup_value" in lines
    assert f"export {_SCRIPT_MARKER}=script_value" in lines
    assert lines == sorted(lines)


def test_direnv_requires_ups(tmp_path, monkeypatch):
    """write_envrc refuses directories without an ups/ subdirectory."""
    script = tmp_path / "loadLSST.bash"
    script.write_text(f"export {_SCRIPT_MARKER}=script_value\n")
    with pytest.raises(FileNotFoundError, match=re.escape(f"{tmp_path} is not an EUPS product")):
        DirEnv([str(script)], []).write_envrc(str(tmp_path), "/bin/bash")
    assert not (tmp_path / ".envrc").exists()


def test_direnv_capture_failure(tmp_path, monkeypatch):
    """A nonzero capture exit raises RuntimeError and leaves a stale .envrc."""
    script = _fake_product(tmp_path, monkeypatch, capture_fails=True)
    (tmp_path / ".envrc").write_text("stale\n")
    with pytest.raises(RuntimeError, match="setup exploded"):
        DirEnv([script], []).write_envrc(str(tmp_path), "/bin/bash")
    assert (tmp_path / ".envrc").read_text() == "stale\n"


def test_direnv_command_writes_envrc(tmp_path, monkeypatch):
    """``tkt direnv`` in a fake product directory writes .envrc."""
    script = _fake_product(tmp_path, monkeypatch)
    env_file = _env_config(tmp_path, script)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["direnv", "--environment", env_file])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".envrc").exists()
    assert "wrote .envrc with" in result.output


def test_direnv_dry_run_writes_nothing(tmp_path, monkeypatch):
    """``tkt direnv --dry-run`` reports the count without writing .envrc."""
    script = _fake_product(tmp_path, monkeypatch)
    env_file = _env_config(tmp_path, script)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["direnv", "--environment", env_file, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert re.search(r"would write \.envrc with \d+ export line", result.output)
    assert not (tmp_path / ".envrc").exists()
