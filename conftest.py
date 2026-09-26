# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib

import pytest

from tests import harness


@pytest.fixture
def compiler(tmp_path: pathlib.Path) -> harness.CompilerHarness:
    return harness.CompilerHarness(tmp_path)
