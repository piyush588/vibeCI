"""Netlify configuration checks and their verification/doctor integration."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from vibeci.cli import build_parser, handle_doctor
from vibeci.detectors.netlify import audit_netlify_config
from vibeci.engine.coordinator import VerificationEngine
from vibeci.models import CheckStatus, OverallStatus


def test_other_projects_have_no_netlify_check(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert audit_netlify_config(tmp_path) is None
    report = VerificationEngine(tmp_path).verify(run_build=False, run_tests=False)
    assert "platform:netlify" not in [check.name for check in report.checks]


@pytest.mark.parametrize("filename", ["netlify.toml", "_redirects", "public/_redirects"])
def test_each_netlify_marker_enables_the_check(tmp_path: Path, filename: str):
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    text = '[build]\npublish = "public"\n' if filename.endswith(".toml") else "/old /new 301\n"
    path.write_text(text, encoding="utf-8")

    result = audit_netlify_config(str(tmp_path))
    assert result is not None
    assert result.status == CheckStatus.PASSED
    assert result.exit_code == 0
    assert result.file_locations == []
    assert result.failure_details == []


@pytest.mark.parametrize("prefix", ["", "\ufeff"])
def test_valid_redirect_syntax_is_preserved(tmp_path: Path, prefix: str):
    (tmp_path / "_redirects").write_text(
        prefix + "\n  # A comment\n"
        "/home /\n"
        "/old /new 301 # An inline comment\n"
        "/temporary https://example.com/new 302\n"
        "/* /index.html 200!\n"
        "/en/* /en/404.html 404\n"
        "/store id=:id /blog/:id 301\n"
        "/search q=:term https://example.com/?q=:term\n"
        "/news/* /blog/:splat\n"
        "/ /anz 302 Country=au,nz\n"
        "/private /login 302 Role=admin\n"
        "/old-anchor /new#anchor 301\n"
        "/gone 410\n",
        encoding="utf-8",
    )
    result = audit_netlify_config(tmp_path)
    assert result is not None
    assert result.status == CheckStatus.PASSED


@pytest.mark.parametrize("filename", ["_redirects", "public/_redirects"])
@pytest.mark.parametrize("status", ["99", "600", "999", "30x", "301!!", "301.5", "invalid"])
def test_invalid_status_reports_exact_location(tmp_path: Path, filename: str, status: str):
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"  /old\t/new\t{status}"
    path.write_text(f"# Redirects\n{line}\n/valid /target 302\n", encoding="utf-8")

    result = audit_netlify_config(tmp_path)
    assert result is not None
    assert result.status == CheckStatus.FAILED
    assert result.exit_code == 1
    assert len(result.file_locations) == 1
    location = result.file_locations[0]
    assert (location.file, location.line, location.column) == (
        filename, 2, line.index(status) + 1,
    )
    assert "100 to 599" in location.message
    assert f"{filename}:2:{location.column}:" in result.failure_details[0]


@pytest.mark.parametrize("rule", ["/old", "/old nowhere"])
def test_missing_or_invalid_destination_fails(tmp_path: Path, rule: str):
    (tmp_path / "_redirects").write_text(rule, encoding="utf-8")
    result = audit_netlify_config(tmp_path)
    assert result is not None
    assert result.status == CheckStatus.FAILED
    assert "destination" in result.file_locations[0].message


def test_query_parameters_do_not_hide_an_invalid_status(tmp_path: Path):
    line = "/store id=:id /blog/:id 999 Country=us"
    (tmp_path / "_redirects").write_text(line, encoding="utf-8")
    result = audit_netlify_config(tmp_path)
    assert result is not None
    assert result.status == CheckStatus.FAILED
    assert result.file_locations[0].column == line.index("999") + 1


@pytest.mark.parametrize(
    ("text", "line", "column"),
    [("[build\n", 1, 7), ("[build]\npublish = [\n", 3, 1)],
)
def test_invalid_toml_reports_parser_location(tmp_path: Path, text: str, line: int, column: int):
    (tmp_path / "netlify.toml").write_text(text, encoding="utf-8")
    result = audit_netlify_config(tmp_path)
    assert result is not None
    assert result.status == CheckStatus.FAILED
    location = result.file_locations[0]
    assert (location.file, location.line, location.column) == ("netlify.toml", line, column)
    assert "Invalid TOML" in location.message


def test_errors_from_all_netlify_files_are_reported(tmp_path: Path):
    (tmp_path / "netlify.toml").write_text("[build\n", encoding="utf-8")
    (tmp_path / "_redirects").write_text("/old /new 999\n", encoding="utf-8")
    (tmp_path / "public").mkdir()
    (tmp_path / "public/_redirects").write_text("/old /new invalid\n", encoding="utf-8")
    result = audit_netlify_config(tmp_path)
    assert result is not None
    assert [location.file for location in result.file_locations] == [
        "netlify.toml", "_redirects", "public/_redirects",
    ]


def test_invalid_utf8_is_reported_without_crashing(tmp_path: Path):
    (tmp_path / "_redirects").write_bytes(b"\xff")
    result = audit_netlify_config(tmp_path)
    assert result is not None
    assert result.status == CheckStatus.FAILED
    assert result.file_locations[0].file == "_redirects"
    assert "UTF-8" in result.failure_details[0]


def test_unreadable_configuration_is_reported(tmp_path: Path, monkeypatch):
    path = tmp_path / "netlify.toml"
    path.write_text("", encoding="utf-8")

    def deny_read(self, *args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "read_text", deny_read)
    result = audit_netlify_config(tmp_path)
    assert result is not None
    assert result.status == CheckStatus.FAILED
    assert "access denied" in result.failure_details[0]


def test_verification_runs_and_reports_netlify_preflight(tmp_path: Path):
    (tmp_path / "_redirects").write_text("/old /new 999\n", encoding="utf-8")
    started, completed = [], []
    report = VerificationEngine(tmp_path).verify(
        run_build=False, run_tests=False, on_start=started.append, on_complete=completed.append,
    )
    assert report.status == OverallStatus.FAILED
    check = next(check for check in report.checks if check.name == "platform:netlify")
    assert check.status == CheckStatus.FAILED
    assert check.layer == "static_health"
    assert "platform:netlify" in [check.name for check in started]
    assert check in completed


def test_disabling_static_checks_skips_netlify(tmp_path: Path):
    (tmp_path / "netlify.toml").write_text("[build\n", encoding="utf-8")
    report = VerificationEngine(tmp_path).verify(run_static=False, run_build=False, run_tests=False)
    assert "platform:netlify" not in [check.name for check in report.checks]


def test_doctor_reports_netlify_errors(tmp_path: Path, capsys):
    (tmp_path / "_redirects").write_text("/old /new 999\n", encoding="utf-8")
    args = build_parser().parse_args(["doctor", "-C", str(tmp_path)])
    assert handle_doctor(args) == 0
    output = capsys.readouterr().out
    assert "Platform Pre-Flight (Netlify)" in output
    assert "_redirects:1:11:" in output


@pytest.mark.parametrize(
    ("status", "exit_code", "overall_status"),
    [("999", 1, "failed"), ("301", 0, "passed")],
)
def test_cli_reports_netlify_status(tmp_path: Path, status: str, exit_code: int, overall_status: str):
    (tmp_path / "_redirects").write_text(f"/old /new {status}\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "vibeci.cli", "verify", "--no-build", "--json", "-C", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == exit_code, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == overall_status
    check = next(check for check in report["checks"] if check["name"] == "platform:netlify")
    assert check["status"] == overall_status
    if exit_code:
        assert check["file_locations"][0]["file"] == "_redirects"
