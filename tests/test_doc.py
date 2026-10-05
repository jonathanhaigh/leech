# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib
import re
import shutil

import pytest

from leech import errors
from tests import doc, harness

_PAGE = pathlib.Path("docs/guide/test.md")


def parse_doc_page(markdown: str) -> list[doc.DocCase]:
    return doc.parse_doc_page(_PAGE, markdown)


def test_parse_single_and_multifile_cases():
    cases = parse_doc_page(
        """Before.

```leech test=hello file=main.leech mode=run exit=7
import helper;
pub fn main() i32 { return helper::answer(); }
```

Between the files.

```leech test=hello file=helper.leech
pub fn answer() i32 { return 7; }
```

```text output=hello
hello
```

```leech test=library file=main.leech mode=compile
pub fn answer() i32 { return 1; }
```
"""
    )

    assert [case.test_id for case in cases] == ["hello", "library"]
    hello = cases[0]
    assert hello.loc == doc.FenceLoc(_PAGE, 3)
    assert hello.mode == "run"
    assert hello.expected_exit == 7
    assert hello.expected_output == "hello\n"
    assert (hello.program.root.name, hello.program.root.path) == (
        "main",
        pathlib.Path("main.leech"),
    )
    assert [(mod.name, mod.path) for mod in hello.program.mods] == [
        ("helper", pathlib.Path("helper.leech"))
    ]
    assert cases[1].mode == "compile"


def test_parse_output_without_final_newline():
    (case,) = parse_doc_page(
        """```leech test=hello file=main.leech mode=run
pub fn main() i32 { return 0; }
```
```text output=hello newline=no
hello
```
"""
    )

    assert case.expected_output == "hello"


def test_parse_expected_error_and_warning():
    cases = parse_doc_page(
        """```leech test=bad file=main.leech mode=error error=InvalidRetTypError
pub fn main() i32 { return true; }
```
```text diagnostic=bad
Return expression has invalid type
```
```leech test=warning file=main.leech mode=compile warning=UnreachableCodeWarning
pub fn main() i32 { return 1; return 2; }
```
```text diagnostic=warning
return statement is unreachable
```
"""
    )

    assert cases[0].diag_type is errors.InvalidRetTypError
    assert not cases[0].expects_warning
    assert cases[1].diag_type is errors.UnreachableCodeWarning
    assert cases[1].expects_warning


def test_markdown_parser_does_not_collect_nested_example_fences():
    assert (
        parse_doc_page(
            """````markdown
```leech test=illustration file=main.leech mode=run
pub fn main() i32 { return 0; }
```
````
"""
        )
        == []
    )


@pytest.mark.parametrize(
    ("markdown", "message"),
    (
        (
            "```leech test=x file=main.leech mode=compile mystery=yes\nfn f() {}\n```\n",
            "unsupported Leech fence metadata",
        ),
        (
            "```leech test=x test=x file=main.leech mode=compile\nfn f() {}\n```\n",
            "duplicate metadata key",
        ),
        (
            """```leech test=x file=main.leech mode=compile
fn f() {}
```
```leech test=x file=main.leech
fn g() {}
```
""",
            "duplicate file",
        ),
        (
            "```leech test=x file=../main.leech mode=compile\nfn f() {}\n```\n",
            "bounded relative .leech path",
        ),
        (
            "```leech test=x file=/main.leech mode=compile\nfn f() {}\n```\n",
            "bounded relative .leech path",
        ),
        (
            "```leech test=x file=main.txt mode=compile\nfn f() {}\n```\n",
            "bounded relative .leech path",
        ),
        (
            "```leech test=x file=helper.leech\nfn f() {}\n```\n",
            "has no main.leech root",
        ),
        (
            "```text output=x\nhello\n```\n",
            "orphan result fence",
        ),
        (
            """```leech test=x file=main.leech mode=run
pub fn main() i32 { return 0; }
```
```text outpt=x
hello
```
""",
            "metadata not allowed on a result fence",
        ),
        (
            """```leech test=x file=main.leech mode=run
pub fn main() i32 { return 0; }
```
```txt output=x
hello
```
""",
            "result fences must use text",
        ),
        (
            """```leech test=x file=main.leech mode=run
pub fn main() i32 { return 0; }
```
``` output=x
hello
```
""",
            "result fences must use text",
        ),
        (
            """```leech test=x file=main.leech mode=run
pub fn main() i32 { return 0; }
```
```text output=x
hello
```
```leech test=x file=helper.leech
fn f() {}
```
""",
            "source fence appears after a result",
        ),
        (
            """```leech test=x file=main.leech mode=run
pub fn main() i32 { return 0; }
```
```text output=y
hello
```
""",
            "does not belong to current test",
        ),
        (
            """```leech test=x file=main.leech mode=run
pub fn main() i32 { return 0; }
```
```leech test=y file=main.leech mode=run
pub fn main() i32 { return 0; }
```
```leech test=x file=main.leech mode=run
pub fn main() i32 { return 0; }
```
""",
            "duplicate or interleaved test ID",
        ),
        (
            "```leech test=x file=main.leech mode=error error=NoSuchError\nfn f() {}\n```\n",
            "unknown or nonconcrete",
        ),
        (
            "```leech test=x file=main.leech mode=error error=UserError\nfn f() {}\n```\n",
            "unknown or nonconcrete",
        ),
        (
            "```leech test=x file=main.leech mode=run std=io\nfn main() {}\n```\n",
            "unsupported Leech fence metadata",
        ),
        (
            "```leech test=x file=main.leech mode=compile exit=1\nfn f() {}\n```\n",
            "metadata not allowed with mode=compile",
        ),
        (
            """```leech test=x file=main.leech mode=error warning=UnreachableCodeWarning
fn f() {}
```
""",
            "metadata not allowed with mode=error",
        ),
        (
            "```leech test=x file=main.leech mode=run module=main\nfn main() {}\n```\n",
            "unsupported Leech fence metadata: module",
        ),
        (
            """```leech test=x file=main.leech mode=compile
fn main() {}
```
```leech test=x file=bad-name.leech
fn answer() {}
```
""",
            "invalid qualified module name",
        ),
        (
            """```leech test=x file=main.leech mode=compile
fn main() {}
```
```leech test=x file=if.leech
fn answer() {}
```
""",
            "reserved final module name segment",
        ),
        (
            """```leech test=x file=main.leech mode=compile warning=UnreachableCodeWarning
pub fn main() i32 { return 0; }
```
""",
            "require a diagnostic= fence",
        ),
        (
            """```leech test=x file=main.leech mode=error error=InvalidRetTypError
pub fn main() i32 { return true; }
```
```text diagnostic=x newline=no
invalid
```
""",
            "newline=no is not allowed on a diagnostic",
        ),
        (
            """```leech test=x file=main.leech mode=error error=InvalidRetTypError
pub fn main() i32 { return true; }
```
```text diagnostic=x
first
second
```
""",
            "one nonempty line",
        ),
        (
            "```leech test=x file=main.leech mode=run exit=256\nfn main() {}\n```\n",
            "exit status must be",
        ),
        (
            "```leech test=x file=main.leech mode=run exit=SIGABRT\nfn main() {}\n```\n",
            "requires a nonempty output= prefix",
        ),
    ),
)
def test_rejects_invalid_fence_contract(markdown, message):
    with pytest.raises(doc.DocCollectionError, match=message) as exc_info:
        parse_doc_page(markdown)

    assert "docs/guide/test.md:" in str(exc_info.value)


@pytest.mark.parametrize(
    "markdown",
    (
        """```leech test=hello file=main.leech mode=run
import std::io;
pub fn main() i32 { io::println("hello"); return 0; }
```
```text output=hello
hello
```
""",
        """```leech test=print file=main.leech mode=run
import std::io;
pub fn main() i32 { io::print("hello"); return 0; }
```
```text output=print newline=no
hello
```
""",
        """```leech test=multi file=main.leech mode=run exit=7
import helper;
pub fn main() i32 { return helper::answer(); }
```
```leech test=multi file=helper.leech
pub fn answer() i32 { return 7; }
```
""",
        """```leech test=compile file=main.leech mode=compile
pub fn answer() i32 { return 7; }
```
""",
        """```leech test=error file=main.leech mode=error error=InvalidRetTypError
pub fn main() i32 { return true; }
```
```text diagnostic=error
Return expression has invalid type
```
""",
        """```leech test=warning file=main.leech mode=compile warning=UnreachableCodeWarning
pub fn main() i32 { return 1; return 2; }
```
```text diagnostic=warning
return statement is unreachable
```
""",
        """```leech test=abort file=main.leech mode=run exit=SIGABRT
pub fn main() i32 { panic("boom"); }
```
```text output=abort
boom
```
""",
        """```leech test=nested file=main.leech mode=run exit=11
import pkg::a;
pub fn main() i32 { return a::answer(); }
```
```leech test=nested file=pkg/a.leech
import pkg::sub::helper;
pub fn answer() i32 { return helper::answer() + 1; }
```
```leech test=nested file=pkg/sub/helper.leech
pub fn answer() i32 { return 10; }
```
""",
        """```leech test=run-warning file=main.leech mode=run exit=1 warning=UnreachableCodeWarning
pub fn main() i32 { return 1; return 2; }
```
```text diagnostic=run-warning
return statement is unreachable
```
""",
        """```leech test=nested-compile file=main.leech mode=compile
import pkg::a;
pub fn main() i32 { return a::answer(); }
```
```leech test=nested-compile file=pkg/a.leech
import pkg::sub::helper;
pub fn answer() i32 { return helper::answer(); }
```
```leech test=nested-compile file=pkg/sub/helper.leech
pub fn answer() i32 { return 10; }
```
""",
    ),
)
def test_execute_valid_case(tmp_path, markdown):
    (case,) = parse_doc_page(markdown)

    case.execute(tmp_path)


@pytest.mark.parametrize(
    ("markdown", "message"),
    (
        (
            """```leech test=broken file=main.leech mode=compile
pub fn main() i32 { return 0 }
```
""",
            "UnexpectedTokenError",
        ),
        (
            """```leech test=warning file=main.leech mode=compile
pub fn main() i32 { return 1; return 2; }
```
""",
            "unexpected registered diagnostics",
        ),
        (
            """```leech test=wrong file=main.leech mode=error error=InvalidRetTypError
pub fn main() i32 { return 0 @ 1; }
```
```text diagnostic=wrong
Return expression has invalid type
```
""",
            "expected InvalidRetTypError, got UnexpectedCharacterError",
        ),
        (
            """```leech test=wrong-message file=main.leech mode=error error=InvalidRetTypError
pub fn main() i32 { return true; }
```
```text diagnostic=wrong-message
different message
```
""",
            "error message does not contain",
        ),
        (
            "```leech test=wrong-warning file=main.leech mode=compile "
            "warning=UnreachableMatchArmWarning\n"
            "pub fn main() i32 { return 1; return 2; }\n"
            "```\n"
            "```text diagnostic=wrong-warning\n"
            "return statement is unreachable\n"
            "```\n",
            "expected warning UnreachableMatchArmWarning, got UnreachableCodeWarning",
        ),
        (
            "```leech test=wrong-warning-message file=main.leech mode=compile "
            "warning=UnreachableCodeWarning\n"
            "pub fn main() i32 { return 1; return 2; }\n"
            "```\n"
            "```text diagnostic=wrong-warning-message\n"
            "different message\n"
            "```\n",
            "warning message does not contain",
        ),
        (
            """```leech test=wrong-output file=main.leech mode=run
import std::io;
pub fn main() i32 { io::println("world"); return 0; }
```
```text output=wrong-output
hello
```
""",
            "unexpected stdout: expected 'hello\\n', got 'world\\n'",
        ),
        (
            """```leech test=wrong-exit file=main.leech mode=run
pub fn main() i32 { return 2; }
```
""",
            "unexpected exit status: expected 0, got 2",
        ),
    ),
)
def test_execute_reports_failure_with_page_and_test(tmp_path, markdown, message):
    (case,) = parse_doc_page(markdown)

    with pytest.raises(doc.DocExampleError, match=re.escape(message)) as exc_info:
        case.execute(tmp_path)

    assert str(exc_info.value).startswith(f"{case.loc.page}:{case.loc.line}: test={case.test_id}")


def test_execute_reports_relative_module_for_compiler_error(tmp_path):
    (case,) = parse_doc_page(
        """```leech test=helper-error file=main.leech mode=compile
import helper;
pub fn main() i32 { return helper::answer(); }
```
```leech test=helper-error file=helper.leech
pub fn answer() i32 { return true; }
```
"""
    )

    with pytest.raises(doc.DocExampleError, match=r"module helper\.leech"):
        case.execute(tmp_path)


def test_execute_restores_existing_diags_after_failure(tmp_path):
    (case,) = parse_doc_page(
        """```leech test=broken file=main.leech mode=compile
pub fn main() i32 { return 0 }
```
"""
    )
    sentinel = errors.UnreachableCodeWarning("sentinel", None)

    with harness.isolated_diags():
        errors.register_error(sentinel)
        with pytest.raises(doc.DocExampleError):
            case.execute(tmp_path)
        assert errors.all_errors() == [sentinel]
        assert errors.error_level() == errors.WARNING


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        ("README.md", True),
        ("docs/guide/index.md", True),
        ("docs/guide/tour/basics.md", True),
        ("docs/guide/example.txt", False),
        ("docs/plans/example.md", False),
        ("docs/specs/example.md", False),
    ),
)
def test_public_doc_scope(tmp_path, path, expected):
    assert doc.is_public_doc_path(tmp_path / path, tmp_path) is expected


def install_repository_conftest(pytester):
    conftest = pathlib.Path(__file__).parents[1] / "conftest.py"
    pytester.makeconftest(conftest.read_text())


def test_pytest_discovers_public_markdown_case(pytester):
    install_repository_conftest(pytester)
    page = pytester.path / "docs" / "guide" / "example.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        """```leech test=discovered file=main.leech mode=compile
pub fn answer() i32 { return 1; }
```
"""
    )

    result = pytester.runpytest("-q")

    result.assert_outcomes(passed=1)


def test_pytest_reports_broken_page_as_collection_error(pytester):
    install_repository_conftest(pytester)
    page = pytester.path / "docs" / "guide" / "broken.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        """Before

```leech test=broken file=main.leech mode=compile mystery=yes
pub fn answer() i32 { return 1; }
```
"""
    )

    result = pytester.runpytest("--collect-only", "-q")

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*broken.md:3: unsupported Leech fence metadata: mystery*"])


def test_pytest_retains_failed_example_workspace(pytester):
    install_repository_conftest(pytester)
    page = pytester.path / "docs" / "guide" / "failure.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        """```leech test=failed file=main.leech mode=run
pub fn main() i32 { return 1; }
```
"""
    )

    result = pytester.runpytest("-q")

    result.assert_outcomes(failed=1)
    match = re.search(r"workspace: (/[^;\s'\"]+)", result.stdout.str())
    assert match is not None
    workspace = pathlib.Path(match.group(1))
    try:
        assert workspace.is_dir()
        assert {path.name for path in workspace.iterdir()} >= {
            "main.leech",
            "main.ll",
            ".link",
        }
    finally:
        if workspace.exists():
            shutil.rmtree(workspace)
