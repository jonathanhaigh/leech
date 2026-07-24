import subprocess

from l0.l0errors import ERROR, NOTE, WARNING


def run_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["l0", *(str(a) for a in args)],
        capture_output=True,
        text=True,
    )


def test_cli_success_infers_output_path(tmp_path):
    src_path = tmp_path / "main.l0"
    src_path.write_text("""pub fn main() i32 {
    return 42;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == NOTE
    assert proc.stdout == ""
    assert proc.stderr == ""
    ll_path = src_path.with_suffix(".ll")
    assert ll_path.exists()
    assert "ret i32 42" in ll_path.read_text()


def test_cli_success_with_explicit_o(tmp_path):
    src_path = tmp_path / "main.l0"
    src_path.write_text("""pub fn main() i32 {
    return 0;
}
""")
    out_path = tmp_path / "custom_out.ll"

    proc = run_cli(src_path, "-o", out_path)

    assert proc.returncode == NOTE
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
    assert (
        proc.stderr
        == "-o option must be given if source file name ends in '.ll'\n"
    )


def test_cli_error_renders_message_and_does_not_write_output(tmp_path):
    src_path = tmp_path / "main.l0"
    src_path.write_text("""pub fn main() i32 {
    return true + 1;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == ERROR
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
    src_path = tmp_path / "main.l0"
    src_path.write_text("""pub fn main() i32 {
    return 1;
    return 2;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == WARNING
    assert proc.stdout == ""
    assert proc.stderr == (
        "WARNING: return statement is unreachable\n"
        "3|     return 2;\n"
        "-------^\n"
    )
    ll_path = src_path.with_suffix(".ll")
    assert ll_path.exists()
    assert "ret i32 1" in ll_path.read_text()
