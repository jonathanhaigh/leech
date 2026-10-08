# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib
from typing import Optional

import pytest

from tests import doc, harness

pytest_plugins = ("pytester",)


@pytest.fixture(autouse=True)
def _private_cache_home(monkeypatch, tmp_path_factory) -> None:
    """Keep ``leech run``'s executables, in this process and its children, out of the user's
    cache."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("cache")))


@pytest.fixture
def compiler(tmp_path: pathlib.Path) -> harness.CompilerHarness:
    return harness.CompilerHarness(tmp_path)


def pytest_collect_file(file_path: pathlib.Path, parent: pytest.Collector) -> Optional[pytest.File]:
    if doc.is_public_doc_path(file_path, parent.config.rootpath):
        return doc.DocFile.from_parent(parent, path=file_path)
    return None
