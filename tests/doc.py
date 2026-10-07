# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Collection and execution of tested Leech examples in public Markdown."""

import dataclasses
import inspect
import pathlib
import shutil
import signal
import tempfile
from collections.abc import Mapping
from typing import Final, Optional

import markdown_it
import markdown_it.token
import pytest

from leech import diag, errors
from tests import harness

_SRC_KEYS: Final = frozenset({"test", "file", "mode", "exit", "error", "warning"})
_RESULT_KEYS: Final = frozenset({"output", "diagnostic", "newline"})
_KNOWN_KEYS: Final = _SRC_KEYS | _RESULT_KEYS

type _FenceMetadata = Mapping[str, str]


class DocExampleError(AssertionError):
    """A documentation example that did not compile or run as declared."""


@dataclasses.dataclass(frozen=True)
class FenceLoc:
    page: pathlib.Path
    line: int

    @classmethod
    def _from_token(cls, page: pathlib.Path, token: markdown_it.token.Token) -> FenceLoc:
        assert token.map is not None
        return cls(page, token.map[0] + 1)


class DocCollectionError(Exception):
    """A malformed documentation-example annotation."""

    def __init__(self, loc: FenceLoc, message: str) -> None:
        super().__init__(f"{loc.page}:{loc.line}: {message}")


@dataclasses.dataclass(frozen=True)
class _FenceInfo:
    language: str
    metadata: _FenceMetadata

    @classmethod
    def from_line(cls, loc: FenceLoc, line: str) -> _FenceInfo:
        words = line.strip().split()
        if not words:
            return cls(language="", metadata={})
        metadata: dict[str, str] = {}
        for word in words[1:]:
            key, separator, value = word.partition("=")
            if not separator or not key or not value:
                raise DocCollectionError(
                    loc, f"invalid fence metadata {word!r}; expected key=value"
                )
            if key in metadata:
                raise DocCollectionError(loc, f"duplicate metadata key {key!r}")
            metadata[key] = value
        return cls(language=words[0], metadata=metadata)


@dataclasses.dataclass(frozen=True)
class SrcFence:
    mod: harness.ModSrc
    loc: FenceLoc

    @staticmethod
    def _validate_file(loc: FenceLoc, file: str) -> pathlib.PurePosixPath:
        path = pathlib.PurePosixPath(file)
        if (
            path.is_absolute()
            or path.suffix != ".leech"
            or path.as_posix() != file
            or any(part in {"", ".", ".."} for part in file.split("/"))
            or "\\" in file
        ):
            raise DocCollectionError(
                loc, f"file must be a bounded relative .leech path, got {file!r}"
            )
        return path

    @classmethod
    def _from_src(cls, loc: FenceLoc, file: str, src: str) -> SrcFence:
        """Make the module that ``file`` names: ``pkg/helper.leech`` is ``pkg::helper``."""
        path = cls._validate_file(loc, file)
        try:
            mod = harness.ModSrc("::".join(path.with_suffix("").parts), src)
        except ValueError as err:
            raise DocCollectionError(loc, str(err)) from err
        return cls(mod, loc)


type _SrcFencesByFile = dict[str, SrcFence]


@dataclasses.dataclass(frozen=True)
class DocCase:
    test_id: str
    loc: FenceLoc
    mode: str
    program: harness.TestProgram
    expected_output: str
    expected_exit: int
    diag_type: Optional[type[errors.UserError]]
    diag_excerpt: Optional[str]
    expects_warning: bool

    def execute(self, tmp_path: pathlib.Path) -> None:
        """Compile or run this case in ``tmp_path``."""
        compiler = harness.CompilerHarness(tmp_path)
        try:
            self._execute_with(compiler)
        except Exception as err:
            raise self._execution_error(err, tmp_path) from err

    def _execute_with(self, compiler: harness.CompilerHarness) -> None:
        diags = diag.Diags()
        if self.mode == "run":
            self._execute_run(compiler, diags)
            self._check_emitted_diags(diags.all())
        elif self.mode == "compile":
            compiler.compile(self.program, diags=diags)
            self._check_emitted_diags(diags.all())
        else:
            self._execute_expected_error(compiler, diags)

    def _execute_run(self, compiler: harness.CompilerHarness, diags: diag.Diags) -> None:
        if self.expected_exit < 0:
            compiler.check_signal(
                self.program,
                expected_signal=signal.Signals(-self.expected_exit),
                stderr_prefix=self.expected_output,
                diags=diags,
            )
        else:
            compiler.check(
                self.program,
                stdout=self.expected_output,
                exit_status=self.expected_exit,
                diags=diags,
            )

    def _execution_error(self, err: Exception, tmp_path: pathlib.Path) -> DocExampleError:
        mod = _relative_mod_path_from_error(err, tmp_path)
        mod_text = f" (module {mod})" if mod is not None else ""
        return DocExampleError(
            f"{self.loc.page}:{self.loc.line}: test={self.test_id}{mod_text}: "
            f"{type(err).__name__}: {err}"
        )

    def _check_emitted_diags(self, emitted: tuple[errors.UserError, ...]) -> None:
        if self.expects_warning:
            self._check_emitted_warning(emitted)
        elif emitted:
            raise AssertionError(f"unexpected emitted diagnostics: {emitted!r}")

    def _check_emitted_warning(self, emitted: tuple[errors.UserError, ...]) -> None:
        assert self.diag_type is not None
        assert self.diag_excerpt is not None
        if len(emitted) != 1:
            raise AssertionError(f"expected one warning, got {len(emitted)} diagnostics")
        warning = emitted[0]
        if type(warning) is not self.diag_type:
            raise AssertionError(
                f"expected warning {self.diag_type.__name__}, got {type(warning).__name__}"
            )
        if warning.level != errors.WARNING:
            raise AssertionError(f"expected WARNING severity, got {warning.level.name}")
        if self.diag_excerpt not in warning.message.message:
            raise AssertionError(
                f"warning message does not contain {self.diag_excerpt!r}: "
                f"{warning.message.message!r}"
            )

    def _execute_expected_error(self, compiler: harness.CompilerHarness, diags: diag.Diags) -> None:
        assert self.diag_type is not None
        assert self.diag_excerpt is not None
        try:
            compiler.compile(self.program, diags=diags)
        except errors.UserError as err:
            self._check_expected_error(err, diags.all())
            return
        raise AssertionError(f"expected {self.diag_type.__name__}, but compilation succeeded")

    def _check_expected_error(
        self, err: errors.UserError, emitted: tuple[errors.UserError, ...]
    ) -> None:
        assert self.diag_type is not None
        assert self.diag_excerpt is not None
        if type(err) is not self.diag_type:
            raise AssertionError(
                f"expected {self.diag_type.__name__}, got {type(err).__name__}"
            ) from err
        if err.level != errors.ERROR:
            raise AssertionError(f"expected ERROR severity, got {err.level.name}") from err
        if self.diag_excerpt not in err.message.message:
            raise AssertionError(
                f"error message does not contain {self.diag_excerpt!r}: {err.message.message!r}"
            ) from err
        unexpected = tuple(other for other in emitted if other is not err)
        if unexpected:
            raise AssertionError(f"unexpected emitted diagnostics: {unexpected!r}") from err


@dataclasses.dataclass(frozen=True)
class _ResultFence:
    kind: str
    content: str
    loc: FenceLoc
    newline_no: bool

    @classmethod
    def from_token(
        cls,
        loc: FenceLoc,
        test_id: str,
        token: markdown_it.token.Token,
        metadata: _FenceMetadata,
    ) -> _ResultFence:
        kinds = {kind for kind in ("output", "diagnostic") if kind in metadata}
        if len(kinds) != 1:
            raise DocCollectionError(
                loc, "a result fence requires exactly one of output= or diagnostic="
            )
        kind = kinds.pop()
        if metadata[kind] != test_id:
            raise DocCollectionError(loc, f"{kind}= ID must match test={test_id}")
        newline_no = "newline" in metadata
        if newline_no and metadata["newline"] != "no":
            raise DocCollectionError(loc, "newline= only supports newline=no")
        if kind == "diagnostic" and newline_no:
            raise DocCollectionError(loc, "newline=no is not allowed on a diagnostic fence")
        return cls(kind, token.content, loc, newline_no)

    def diag_excerpt(self) -> str:
        excerpt = self.content.removesuffix("\n")
        if not excerpt or "\n" in excerpt or "\r" in excerpt:
            raise DocCollectionError(self.loc, "diagnostic excerpt must contain one nonempty line")
        return excerpt


@dataclasses.dataclass(frozen=True)
class _RootOptions:
    mode: str
    exit_value: Optional[str]
    error_name: Optional[str]
    warning_name: Optional[str]

    def diag_type(self, loc: FenceLoc) -> Optional[type[errors.UserError]]:
        name = self.error_name
        if self.mode == "error" and name is None:
            raise DocCollectionError(loc, "mode=error requires error=UserErrorSubclass")
        if name is None:
            name = self.warning_name
        if name is None:
            return None
        value = getattr(errors, name, None)
        if (
            not isinstance(value, type)
            or value is errors.UserError
            or not issubclass(value, errors.UserError)
            or inspect.isabstract(value)
        ):
            raise DocCollectionError(loc, f"unknown or nonconcrete UserError subclass {name!r}")
        return value

    def exit_status(self, loc: FenceLoc) -> int:
        if self.exit_value is None:
            return 0
        if self.exit_value == "SIGABRT":
            return -signal.SIGABRT
        try:
            status = int(self.exit_value)
        except ValueError as err:
            raise DocCollectionError(loc, f"invalid exit status {self.exit_value!r}") from err
        if not 0 <= status <= 255:
            raise DocCollectionError(
                loc, f"exit status must be from 0 through 255, got {self.exit_value!r}"
            )
        return status


@dataclasses.dataclass
class _CaseBuilder:
    page: pathlib.Path
    test_id: str
    srcs: _SrcFencesByFile = dataclasses.field(default_factory=dict)
    root_options: Optional[_RootOptions] = None
    output: Optional[_ResultFence] = None
    diag: Optional[_ResultFence] = None
    results_started: bool = False

    def add_src_fence(self, token: markdown_it.token.Token, metadata: _FenceMetadata) -> None:
        loc = FenceLoc._from_token(self.page, token)
        self._validate_src_metadata(loc, metadata)
        file = metadata["file"]
        root_options = self._options_for_src(loc, file, metadata)
        self.srcs[file] = SrcFence._from_src(loc, file, token.content)
        if root_options is not None:
            assert self.root_options is None
            self.root_options = root_options

    def _validate_src_metadata(self, loc: FenceLoc, metadata: _FenceMetadata) -> None:
        unknown = metadata.keys() - _SRC_KEYS
        if unknown:
            raise DocCollectionError(
                loc, f"unsupported Leech fence metadata: {', '.join(sorted(unknown))}"
            )
        missing = {"test", "file"} - metadata.keys()
        if missing:
            raise DocCollectionError(
                loc, f"missing Leech fence metadata: {', '.join(sorted(missing))}"
            )
        if metadata["test"] != self.test_id:
            raise DocCollectionError(loc, "internal case grouping mismatch")
        file = metadata["file"]
        SrcFence._validate_file(loc, file)
        if file in self.srcs:
            raise DocCollectionError(loc, f"duplicate file {file!r} in test {self.test_id!r}")

    def _options_for_src(
        self, loc: FenceLoc, file: str, metadata: _FenceMetadata
    ) -> Optional[_RootOptions]:
        if file == "main.leech":
            return self._root_options_from_metadata(loc, metadata)
        invalid = metadata.keys() - {"test", "file"}
        if invalid:
            raise DocCollectionError(
                loc,
                f"metadata not allowed on a module fence: {', '.join(sorted(invalid))}",
            )
        return None

    @staticmethod
    def _root_options_from_metadata(loc: FenceLoc, metadata: _FenceMetadata) -> _RootOptions:
        mode = metadata.get("mode")
        if mode not in {"run", "compile", "error"}:
            raise DocCollectionError(
                loc, "main.leech requires mode=run, mode=compile, or mode=error"
            )
        allowed = {
            "run": {"test", "file", "mode", "exit", "warning"},
            "compile": {"test", "file", "mode", "warning"},
            "error": {"test", "file", "mode", "error"},
        }[mode]
        invalid = metadata.keys() - allowed
        if invalid:
            raise DocCollectionError(
                loc, f"metadata not allowed with mode={mode}: {', '.join(sorted(invalid))}"
            )
        return _RootOptions(
            mode=mode,
            exit_value=metadata.get("exit"),
            error_name=metadata.get("error"),
            warning_name=metadata.get("warning"),
        )

    def add_result_fence(self, token: markdown_it.token.Token, metadata: _FenceMetadata) -> None:
        loc = FenceLoc._from_token(self.page, token)
        result = _ResultFence.from_token(loc, self.test_id, token, metadata)
        if result.kind == "output":
            if self.output is not None:
                raise DocCollectionError(loc, f"duplicate output fence for test {self.test_id!r}")
            self.output = result
        else:
            if self.diag is not None:
                raise DocCollectionError(
                    loc, f"duplicate diagnostic fence for test {self.test_id!r}"
                )
            self.diag = result
        self.results_started = True

    def build_doc_case(self) -> DocCase:
        root = self._root_src()
        options = self.root_options
        assert options is not None
        self._validate_case_shape(root, options)
        diag_type = options.diag_type(root.loc)
        expects_warning = options.warning_name is not None
        self._validate_result_fences(root, options.mode, expects_warning)
        expected_exit = options.exit_status(root.loc)
        expected_output = self._expected_output(root, expected_exit)
        return DocCase(
            test_id=self.test_id,
            loc=root.loc,
            mode=options.mode,
            program=self._program(root),
            expected_output=expected_output,
            expected_exit=expected_exit,
            diag_type=diag_type,
            diag_excerpt=self._diag_excerpt(),
            expects_warning=expects_warning,
        )

    def _root_src(self) -> SrcFence:
        root = self.srcs.get("main.leech")
        line = min((src.loc.line for src in self.srcs.values()), default=1)
        if root is None:
            raise DocCollectionError(
                FenceLoc(self.page, line),
                f"test {self.test_id!r} has no main.leech root fence",
            )
        return root

    def _validate_case_shape(self, root: SrcFence, options: _RootOptions) -> None:
        if options.mode == "error" and len(self.srcs) != 1:
            raise DocCollectionError(root.loc, "mode=error is limited to a single main.leech file")
        if options.warning_name is not None and len(self.srcs) != 1:
            raise DocCollectionError(
                root.loc, "warning examples are limited to a single main.leech file"
            )

    def _validate_result_fences(self, root: SrcFence, mode: str, expects_warning: bool) -> None:
        if self.output is not None and mode != "run":
            raise DocCollectionError(self.output.loc, "output= is only allowed with mode=run")
        needs_diag = mode == "error" or expects_warning
        if self.diag is not None and not needs_diag:
            raise DocCollectionError(self.diag.loc, "diagnostic= requires error= or warning=")
        if self.diag is None and needs_diag:
            raise DocCollectionError(root.loc, "error= and warning= require a diagnostic= fence")

    def _expected_output(self, root: SrcFence, expected_exit: int) -> str:
        if self.output is None:
            expected_output = ""
        elif self.output.newline_no:
            if not self.output.content.endswith("\n"):
                raise DocCollectionError(
                    self.output.loc, "newline=no requires a structural final newline"
                )
            expected_output = self.output.content[:-1]
        else:
            expected_output = self.output.content
        if expected_exit < 0:
            if self.output is None or not expected_output:
                raise DocCollectionError(
                    root.loc, "exit=SIGABRT requires a nonempty output= prefix"
                )
            if self.output.newline_no:
                raise DocCollectionError(
                    self.output.loc, "newline=no is not allowed with exit=SIGABRT"
                )
        return expected_output

    def _diag_excerpt(self) -> Optional[str]:
        if self.diag is None:
            return None
        return self.diag.diag_excerpt()

    def _program(self, root: SrcFence) -> harness.TestProgram:
        srcs = tuple(self.srcs.values())
        try:
            return harness.TestProgram(
                root.mod,
                tuple(src.mod for src in srcs if src is not root),
            )
        except ValueError as err:
            raise DocCollectionError(root.loc, str(err)) from err


@dataclasses.dataclass
class _DocPageParser:
    page: pathlib.Path
    cases: list[DocCase] = dataclasses.field(default_factory=list)
    completed_ids: set[str] = dataclasses.field(default_factory=set)
    current: Optional[_CaseBuilder] = None

    def parse(self, markdown: str) -> list[DocCase]:
        for token in markdown_it.MarkdownIt("commonmark").parse(markdown):
            if token.type == "fence":
                self._add_fence(token)
        self._finish_current_case()
        return self.cases

    def _add_fence(self, token: markdown_it.token.Token) -> None:
        loc = FenceLoc._from_token(self.page, token)
        if not self._is_relevant_fence(loc, token.info):
            return
        info = _FenceInfo.from_line(loc, token.info)
        if info.language == "leech":
            self._add_src_fence(loc, token, info.metadata)
        else:
            self._add_result_fence(loc, token, info.metadata)

    @staticmethod
    def _is_relevant_fence(loc: FenceLoc, info_line: str) -> bool:
        words = info_line.strip().split()
        language = words[0] if words else ""
        has_result_key = any(word.partition("=")[0] in _RESULT_KEYS for word in words)
        if language == "leech":
            return True
        if language != "text" and has_result_key:
            raise DocCollectionError(loc, "output= and diagnostic= result fences must use text")
        return language == "text" and any("=" in word or word in _KNOWN_KEYS for word in words[1:])

    def _add_src_fence(
        self,
        loc: FenceLoc,
        token: markdown_it.token.Token,
        metadata: _FenceMetadata,
    ) -> None:
        test_id = metadata.get("test")
        if test_id is None:
            raise DocCollectionError(loc, "every Leech fence requires test=ID")
        if self.current is None or self.current.test_id != test_id:
            self._start_case(loc, test_id)
        elif self.current.results_started:
            raise DocCollectionError(
                loc, f"source fence appears after a result for test {test_id!r}"
            )
        assert self.current is not None
        self.current.add_src_fence(token, metadata)

    def _start_case(self, loc: FenceLoc, test_id: str) -> None:
        self._finish_current_case()
        if test_id in self.completed_ids:
            raise DocCollectionError(loc, f"duplicate or interleaved test ID {test_id!r}")
        self.current = _CaseBuilder(self.page, test_id)

    def _add_result_fence(
        self,
        loc: FenceLoc,
        token: markdown_it.token.Token,
        metadata: _FenceMetadata,
    ) -> None:
        unknown = metadata.keys() - _RESULT_KEYS
        if unknown:
            raise DocCollectionError(
                loc,
                f"metadata not allowed on a result fence: {', '.join(sorted(unknown))}",
            )
        result_ids = [metadata[key] for key in ("output", "diagnostic") if key in metadata]
        if self.current is None:
            raise DocCollectionError(loc, "orphan result fence without a preceding Leech test")
        if result_ids and any(test_id != self.current.test_id for test_id in result_ids):
            raise DocCollectionError(
                loc, f"result fence does not belong to current test {self.current.test_id!r}"
            )
        self.current.add_result_fence(token, metadata)

    def _finish_current_case(self) -> None:
        if self.current is None:
            return
        self.cases.append(self.current.build_doc_case())
        self.completed_ids.add(self.current.test_id)
        self.current = None


def parse_doc_page(page: pathlib.Path, markdown: str) -> list[DocCase]:
    """Parse and validate all tested Leech examples in one Markdown page."""
    return _DocPageParser(page).parse(markdown)


def _relative_mod_path_from_error(err: BaseException, tmp_path: pathlib.Path) -> Optional[str]:
    if not isinstance(err, errors.UserError) or err.message.span is None:
        return None
    try:
        return str(err.message.span.file.path.resolve().relative_to(tmp_path.resolve()))
    except ValueError:
        return None


def is_public_doc_path(path: pathlib.Path, root: pathlib.Path) -> bool:
    """Return whether ``path`` is a Markdown page governed by the snippet contract."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return relative == pathlib.Path("README.md") or (
        relative.suffix == ".md" and relative.parts[:2] == ("docs", "guide")
    )


class DocFile(pytest.File):
    def collect(self):
        try:
            cases = parse_doc_page(self.path, self.path.read_text(encoding="utf-8"))
        except DocCollectionError as err:
            raise self.CollectError(str(err)) from err
        for case in cases:
            name = f"{case.test_id} (line {case.loc.line})"
            yield DocItem.from_parent(self, name=name, case=case)


class DocItem(pytest.Item):
    case: Final[DocCase]

    def __init__(self, *, case: DocCase, **kwargs) -> None:
        super().__init__(**kwargs)
        self.case = case

    def runtest(self) -> None:
        directory = pathlib.Path(tempfile.mkdtemp(prefix="leech-doc-"))
        self.case.execute(directory)
        shutil.rmtree(directory)

    def reportinfo(self):
        return (
            self.path,
            self.case.loc.line - 1,
            f"Leech documentation example {self.case.test_id}",
        )
