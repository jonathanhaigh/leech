import subprocess

from leech import errors


def run_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["leech", *(str(a) for a in args)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_success_infers_output_path(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 42;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.NOTE
    assert proc.stdout == ""
    assert proc.stderr == ""
    ll_path = src_path.with_suffix(".ll")
    assert ll_path.exists()
    assert "ret i32 42" in ll_path.read_text()


def test_cli_success_with_explicit_o(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 0;
}
""")
    out_path = tmp_path / "custom_out.ll"

    proc = run_cli(src_path, "-o", out_path)

    assert proc.returncode == errors.NOTE
    assert out_path.exists()


def test_cli_ll_suffix_requires_explicit_o(tmp_path):
    src_path = tmp_path / "main.ll"
    src_path.write_text("""pub fn main() i32 {
    return 0;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr == "-o option must be given if source file name ends in '.ll'\n"


def test_cli_error_renders_message_and_does_not_write_output(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return true + 1;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.ERROR
    assert proc.stdout == ""
    assert proc.stderr == (
        'ERROR: Left operand of binary operation "+" has invalid type "bool", '
        'expecting "an integer type"\n'
        "2|     return true + 1;\n"
        "--------------^\n"
        'NOTE: For "+" operation here\n'
        "2|     return true + 1;\n"
        "-------------------^\n"
    )
    assert not src_path.with_suffix(".ll").exists()


def test_cli_warning_still_writes_output(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 1;
    return 2;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.WARNING
    assert proc.stdout == ""
    assert proc.stderr == ("WARNING: return statement is unreachable\n3|     return 2;\n-------^\n")
    ll_path = src_path.with_suffix(".ll")
    assert ll_path.exists()
    assert "ret i32 1" in ll_path.read_text()


def test_cli_unexpected_character_error(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 0 @ 1;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.ERROR
    assert proc.stdout == ""
    assert proc.stderr == (
        'ERROR: Unexpected character "@"\n2|     return 0 @ 1;\n----------------^\n'
    )
    assert not src_path.with_suffix(".ll").exists()


def test_cli_unexpected_token_error(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 0
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.ERROR
    assert proc.stdout == ""
    assert proc.stderr == ('ERROR: Unexpected token "}"\n3| }\n---^\nNOTE: Expected one of: ";"\n')
    assert not src_path.with_suffix(".ll").exists()


def test_cli_unexpected_end_of_input_error(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 0;
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.ERROR
    assert proc.stdout == ""
    # The exact set of expected tokens is an implementation detail of the
    # grammar (e.g. it grows whenever a new prefix operator is added), so
    # only the diagnostic's kind and source position are asserted exactly.
    assert proc.stderr.startswith(
        "ERROR: Unexpected end of input\n"
        "2|     return 0;\n"
        "---------------^\n"
        "NOTE: Expected one of: "
    )
    assert not src_path.with_suffix(".ll").exists()


def test_cli_warning_fires_once_for_multiple_dead_statements(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 1;
    let y = 2;
    return y;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.WARNING
    assert proc.stdout == ""
    assert proc.stderr == ("WARNING: let statement is unreachable\n3|     let y = 2;\n-------^\n")
