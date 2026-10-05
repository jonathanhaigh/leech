# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import configparser
import email.parser
import os
import pathlib
import shutil
import subprocess
import tarfile
import zipfile

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_STD_ROOT = _REPO_ROOT / "src" / "leech" / "std"


def _uv() -> str:
    # `uv run` exports its own executable as UV; fall back to PATH for other launchers.
    uv = os.environ.get("UV") or shutil.which("uv")
    assert uv is not None, "packaging tests need uv on PATH or in the UV environment variable"
    return uv


@pytest.fixture(scope="module")
def dist_dir(tmp_path_factory) -> pathlib.Path:
    out_dir = tmp_path_factory.mktemp("dist")
    proc = subprocess.run(
        [_uv(), "build", "--out-dir", str(out_dir), str(_REPO_ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"uv build failed:\n{proc.stderr}"
    # uv still builds when its own version is outside the uv_build bound, but only warns.
    assert "does not contain the current uv version" not in proc.stderr, proc.stderr
    return out_dir


def _only[T](items: list[T]) -> T:
    assert len(items) == 1, items
    return items[0]


@pytest.fixture(scope="module")
def wheel(dist_dir) -> zipfile.ZipFile:
    return zipfile.ZipFile(_only(list(dist_dir.glob("leech-*.whl"))))


def _dist_info_file(wheel: zipfile.ZipFile, name: str) -> str:
    path = _only([n for n in wheel.namelist() if n.endswith(f".dist-info/{name}")])
    return wheel.read(path).decode()


def test_wheel_contains_compiler_data_files(wheel):
    names = set(wheel.namelist())
    assert "leech/leech.lark" in names
    std_srcs = {f"leech/std/{path.name}" for path in _STD_ROOT.glob("*.leech")}
    assert std_srcs
    assert std_srcs <= names


def test_wheel_contains_license_files(wheel):
    names = wheel.namelist()
    assert any(n.endswith(".dist-info/licenses/LICENSE") for n in names)
    assert any(n.endswith(".dist-info/licenses/LICENSES/MPL-2.0.txt") for n in names)


def test_wheel_declares_console_scripts(wheel):
    entry_points = configparser.ConfigParser()
    entry_points.read_string(_dist_info_file(wheel, "entry_points.txt"))
    assert dict(entry_points["console_scripts"]) == {
        "leech": "leech.cli:main",
        "leechc": "leech.driver:main",
    }


def test_wheel_metadata_blocks_upload_and_bounds_llvmlite(wheel):
    metadata = email.parser.Parser().parsestr(_dist_info_file(wheel, "METADATA"))
    assert "Private :: Do Not Upload" in metadata.get_all("Classifier", [])
    assert metadata["License-Expression"] == "MPL-2.0"
    llvmlite_reqs = [
        req for req in metadata.get_all("Requires-Dist", []) if req.startswith("llvmlite")
    ]
    assert llvmlite_reqs == ["llvmlite>=0.47.0,<0.48"]


def test_sdist_contains_license_files(dist_dir):
    with tarfile.open(_only(list(dist_dir.glob("leech-*.tar.gz")))) as sdist:
        names = {name.split("/", 1)[-1] for name in sdist.getnames()}
    assert "LICENSE" in names
    assert "LICENSES/MPL-2.0.txt" in names
