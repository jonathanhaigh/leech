# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The ``leech`` command: build Leech programs from their root modules."""

import argparse
import pathlib
import sys

from leech import build, driver, errors, ll_emit, parse


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="leech", description="Build Leech programs.", allow_abbrev=False
    )
    parser.add_argument("--version", action="version", version=driver.version_text("leech"))
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    build_parser = subparsers.add_parser(
        "build",
        help="build a program into an executable",
        description=(
            "Build a program and every module it imports into an executable. Intermediate "
            f"files go to {build.OUT_DIR_NAME}/ beside ROOT."
        ),
        allow_abbrev=False,
    )
    build_parser.add_argument(
        "root",
        help="the program's root module, whose directory is the program's package",
        metavar="ROOT",
        type=pathlib.Path,
    )
    build_parser.add_argument(
        "-o",
        help=f"executable to write (default: {build.OUT_DIR_NAME}/<ROOT stem> beside ROOT)",
        metavar="EXE",
        type=pathlib.Path,
    )
    build_parser.add_argument(
        "-O",
        choices=ll_emit.OPT_LEVELS,
        default=0,
        type=int,
        help="optimization level (default: %(default)s)",
        dest="opt_level",
    )

    args = parser.parse_args()
    _check_root(build_parser, args.root)
    return args


def _check_root(parser: argparse.ArgumentParser, root: pathlib.Path) -> None:
    """Reject a root that isn't an existing ``.leech`` file named by one identifier."""
    if not root.is_file():
        parser.error(f"root module {str(root)!r} is not a file")
    if root.suffix != ".leech":
        parser.error(f"root module file name must end in '.leech': {str(root)!r}")
    segments = parse.parse_qualified_name(root.stem)
    if segments is None or len(segments) != 1:
        parser.error(
            f"root module file name must be an identifier followed by '.leech': {str(root)!r}"
        )


def main() -> None:
    """Build the requested program, render diagnostics, and exit 0 only on success."""
    args = _parse_args()
    result = build.build(args.root, output=args.o, opt_level=args.opt_level)
    errors.TextErrorRenderer().display_errors(list(result.diags))
    if result.exe is None:
        sys.exit(1)
    sys.exit(0)
