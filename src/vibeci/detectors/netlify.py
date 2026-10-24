"""Check Netlify TOML syntax and redirect status codes without external tools."""

from __future__ import annotations

import re
import time
import tomllib
from pathlib import Path

from vibeci.models import CheckLayer, CheckResult, CheckStatus, CheckType, FileLocation


NETLIFY_FILES = ("netlify.toml", "_redirects", "public/_redirects")
HTTP_STATUS = re.compile(r"[1-5][0-9]{2}!?")


def _toml_error_location(error: tomllib.TOMLDecodeError, text: str) -> tuple[int, int]:
    # Python 3.11-3.13 include coordinates in the message rather than attributes.
    match = re.search(r"\(at line (\d+), column (\d+)\)$", str(error))
    if match:
        return int(match[1]), int(match[2])
    return (
        getattr(error, "lineno", text.count("\n") + 1),
        getattr(error, "colno", len(text.rsplit("\n", 1)[-1]) + 1),
    )


def _redirect_errors(path: str, text: str) -> list[FileLocation]:
    errors: list[FileLocation] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        tokens = []
        for token in re.finditer(r"\S+", line):
            if token[0].startswith("#"):
                break
            tokens.append(token)
        if not tokens:
            continue

        if len(tokens) < 2:
            errors.append(FileLocation(
                file=path, line=line_number, column=tokens[0].start() + 1,
                message="Missing redirect destination. Add a path or URL after the source.",
            ))
            continue

        # Netlify also accepts forward rules such as `/gone 410` without a target.
        status = tokens[1]
        if not re.match(r"[0-9]", status[0]):
            destination = next((
                index for index, token in enumerate(tokens[1:], start=1)
                if token[0].startswith(("/", "http://", "https://"))
            ), None)
            if destination is None:
                errors.append(FileLocation(
                    file=path, line=line_number, column=status.start() + 1,
                    message="Redirect destination must start with '/', 'http://' or 'https://'.",
                ))
                continue
            if destination + 1 == len(tokens):
                continue  # An omitted status defaults to 301.
            status = tokens[destination + 1]

        if not HTTP_STATUS.fullmatch(status[0]):
            errors.append(FileLocation(
                file=path, line=line_number, column=status.start() + 1,
                message="Use an HTTP status code from 100 to 599 with an optional trailing '!'.",
            ))
    return errors


def audit_netlify_config(project_dir: Path | str) -> CheckResult | None:
    """Audit recognized Netlify files, returning None for other projects."""
    root = Path(project_dir).resolve()
    paths = [path for path in NETLIFY_FILES if (root / path).is_file()]
    if not paths:
        return None

    start_time = time.perf_counter()
    errors: list[FileLocation] = []
    for path in paths:
        try:
            encoding = "utf-8" if path == "netlify.toml" else "utf-8-sig"
            text = (root / path).read_text(encoding=encoding)
        except (OSError, UnicodeError) as error:
            errors.append(FileLocation(file=path, message=f"Could not read UTF-8 configuration: {error}"))
            continue

        if path == "netlify.toml":
            try:
                tomllib.loads(text)
            except tomllib.TOMLDecodeError as error:
                line, column = _toml_error_location(error, text)
                errors.append(FileLocation(
                    file=path, line=line, column=column, message=f"Invalid TOML: {error}",
                ))
        else:
            errors.extend(_redirect_errors(path, text))

    status = CheckStatus.FAILED if errors else CheckStatus.PASSED
    summary = (
        "Netlify configuration errors found"
        if errors else "Netlify syntax and redirect status checks passed"
    )
    return CheckResult(
        name="platform:netlify",
        layer=CheckLayer.STATIC_HEALTH.value,
        check_type=CheckType.ENV.value,
        command="vibeci-internal:netlify-config",
        status=status,
        exit_code=1 if errors else 0,
        duration_ms=max(1, int((time.perf_counter() - start_time) * 1000)),
        summary=summary,
        failure_details=[
            f"{error.file}:{error.line or 1}:{error.column or 1}: {error.message}"
            for error in errors
        ],
        file_locations=errors,
    )
