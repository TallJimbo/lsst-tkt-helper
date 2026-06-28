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

__all__ = ("read_json_file", "write_json_file")

import json
import logging
from typing import Any

import json5  # used to read to allow trailling commas, never to write.


def _merge(
    target: dict[str, Any], source: dict[str, Any], base_path: str | None = None, context: str = "JSON entry"
) -> None:
    """Merge ``source`` into ``target``, combining dictionaries recursively
    when the same keys are present.

    Modifies ``target`` in-place, and entries from ``source`` take precedence.
    """
    for k, v in source.items():
        d = target.setdefault(k, v)
        path = f"{base_path}.{k}" if base_path is not None else k
        if d is not v:
            if isinstance(d, dict) and isinstance(v, dict):
                _merge(d, v, base_path=path)
            elif v != d:
                target[k] = v
                logging.warning(f"{context} {path}={d!r} differs from tkt default of {v!r}.")
        else:
            logging.warning(f"{context} {path}={d!r} is not set in tkt defaults.")


def read_json_file(filename: str, *, target: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read JSON data from the file with the given name.

    Parameters
    ----------
    filename
        Name of the file to read.
    target
        If provided, merge the loaded data into this dictionary, given
        precedence to the loaded data and warning about differences.
    """
    with open(filename) as stream:
        data = json5.load(stream)
    if target is not None:
        _merge(target, data, context=filename)
        return target
    else:
        return data


def write_json_file(data: dict[str, Any], filename: str) -> None:
    """Write JSON data to the file with the given name."""
    with open(filename, "w") as stream:
        json.dump(data, stream, indent=2)
