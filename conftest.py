# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib
from collections.abc import Iterator
from typing import Optional

import pytest

from tests import doc, harness

pytest_plugins = ("pytester",)


@pytest.fixture
def compiler(tmp_path: pathlib.Path) -> harness.CompilerHarness:
    return harness.CompilerHarness(tmp_path)


@pytest.fixture
def isolated_diagnostics() -> Iterator[None]:
    with harness.isolated_diagnostics():
        yield


def pytest_collect_file(file_path: pathlib.Path, parent: pytest.Collector) -> Optional[pytest.File]:
    if doc.is_public_doc_path(file_path, parent.config.rootpath):
        return doc.DocFile.from_parent(parent, path=file_path)
    return None
