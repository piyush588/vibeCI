"""Command line interface for VibeCI."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

from vibeci.config import VibeConfig, create_starter_config
from vibeci.detectors import discover_checks
from vibeci.detectors.netlify import audit_netlify_config
from vibeci.detectors.vercel import audit_vercel_functions
from vibeci.engine.coordinator import VerificationEngine
from vibeci.formatters.console import ConsoleFormatter
from vibeci.formatters.json_output import format_json_report
from vibeci.models import OverallStatus, VerificationReport
from vibeci.runner import CheckRunner

VERSION = "0.3.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibeci",
        description="VibeCI — Local Verification for AI-Written Code. Fast feedback. Minimal setup.",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"vibeci {VERSION}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Command: verify (Flagship software verification engine)
    verify_parser = subparsers.add_parser(
        "verify",
        help="Run comprehensive multi-layer verification (static health, build, tests, runtime)",
    )
    verify_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output structured JSON for AI coding agents",
    )
    verify_parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip production build verification",
    )
    verify_parser.add_argument(
        "--no-static",
        action="store_true",
        help="Skip static health and environment preflight",
    )
    verify_parser.add_argument(
        "--diff",
        "--changed-only",
        action="store_true",
        dest="changed_only",
        help="Sub-second verification: verify only files touched in git diff/working tree",
    )
    verify_parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Compare changes against a git reference (e.g. main, HEAD~1)",
    )
    verify_parser.add_argument(
        "--smoke",
        action="store_true",
        help="Exercise application boot and runtime HTTP probing",
    )
    verify_parser.add_argument(
        "--build-only",
        action="store_true",
        help="Run only production build verification",
    )
    verify_parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Timeout in seconds for checks",
    )
    verify_parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop immediately upon the first failed check",
    )
    verify_parser.add_argument(
        "-C",
        "--project-dir",
        type=str,
        default=".",
        help="Target project directory to verify (default: current directory)",
    )

    # Command: check
    check_parser = subparsers.add_parser(
        "check",
        help="Run discovered project test/lint checks",
    )
    check_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output structured JSON for AI coding agents",
    )
    check_parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Timeout in seconds for each check (default: 60s)",
    )
    check_parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop immediately upon the first failed check",
    )
    check_parser.add_argument(
        "--only",
        type=str,
        nargs="+",
        help="Run only the specified check name(s) (e.g. --only pytest ruff)",
    )
    check_parser.add_argument(
        "--failed-only",
        action="store_true",
        help="Rerun only checks that failed in the previous run",
    )
    check_parser.add_argument(
        "--list",
        action="store_true",
        dest="list_only",
        help="List discovered checks without executing them",
    )
    check_parser.add_argument(
        "-C",
        "--project-dir",
        type=str,
        default=".",
        help="Target project directory to verify (default: current directory)",
    )

    # Command: init
    init_parser = subparsers.add_parser(
        "init",
        help="Generate a starter .vibeci.toml configuration file",
    )
    init_parser.add_argument(
        "-C",
        "--project-dir",
        type=str,
        default=".",
        help="Target project directory (default: current directory)",
    )

    # Command: doctor
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Inspect project environment, detected tools, and configuration health",
    )
    doctor_parser.add_argument(
        "-C",
        "--project-dir",
        type=str,
        default=".",
        help="Target project directory (default: current directory)",
    )

    # Command: hook
    hook_parser = subparsers.add_parser(
        "hook",
        help="Manage automated git pre-push verification hook",
    )
    hook_parser.add_argument(
        "action",
        choices=["install", "remove", "status"],
        help="Action to perform: install, remove, or status",
    )
    hook_parser.add_argument(
        "-C",
        "--project-dir",
        type=str,
        default=".",
        help="Target project directory (default: current directory)",
    )

    # Command: rule (Configure AI agent verification rules)
    rule_parser = subparsers.add_parser(
        "rule",
        aliases=["rules"],
        help="Configure AI coding agents (Cursor, Claude Code, Copilot) to automatically run VibeCI after changes",
    )
    rule_parser.add_argument(
        "action",
        nargs="?",
        default="install",
        choices=["install", "print", "status"],
        help="Action to perform: install (default), print, or status",
    )
    rule_parser.add_argument(
        "--agent",
        choices=["all", "cursor", "claude", "copilot", "universal", "antigravity"],
        default="all",
        help="Target AI assistant: all (default), cursor, claude, copilot, universal, or antigravity",
    )
    rule_parser.add_argument(
        "-C",
        "--project-dir",
        type=str,
        default=".",
        help="Target project directory (default: current directory)",
    )

    # Command: upgrade
    upgrade_parser = subparsers.add_parser(
        "upgrade",
        aliases=["update"],
        help="Upgrade VibeCI to the latest version available on PyPI",
    )
    upgrade_parser.add_argument(
        "--check",
        action="store_true",
        help="Check if an update is available without installing it",
    )
    upgrade_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Display the upgrade command that would be executed",
    )

    # Command: help
    help_parser = subparsers.add_parser(
        "help",
        help="Show help for VibeCI or a specific subcommand",
    )
    help_parser.add_argument(
        "subcommand",
        nargs="?",
        default=None,
        help="Subcommand to display help for (e.g. verify, check, doctor, rule, upgrade)",
    )

    return parser


def handle_verify(args: argparse.Namespace) -> int:
    root_dir = Path(args.project_dir).resolve()
    if not root_dir.exists():
        if args.json_output:
            print(f'{{"status": "error", "message": "Directory not found: {root_dir}"}}')
        else:
            print(f"Error: Target directory does not exist: {root_dir}", file=sys.stderr)
        return 1

    config = VibeConfig.load(root_dir)
    engine = VerificationEngine(root_dir=root_dir, config=config)
    formatter = ConsoleFormatter()

    changed_only = getattr(args, "changed_only", False)
    compare_ref = getattr(args, "since", None)

    ecosystem, _ = discover_checks(root_dir)
    mode_label = f"{ecosystem} (Diff Mode)" if changed_only else ecosystem
    if not args.json_output:
        formatter.render_header(root_dir.name, mode_label, 0)

    on_start = formatter.render_check_start if not args.json_output else None
    on_complete = formatter.render_check_result if not args.json_output else None

    run_static = not getattr(args, "no_static", False) and not getattr(args, "build_only", False)
    run_build = not getattr(args, "no_build", False) and not changed_only
    run_tests = not getattr(args, "build_only", False)
    run_smoke = getattr(args, "smoke", False)

    report = engine.verify(
        run_static=run_static,
        run_build=run_build,
        run_tests=run_tests,
        run_smoke=run_smoke,
        changed_only=changed_only,
        compare_ref=compare_ref,
        fail_fast=args.fail_fast,
        timeout_seconds=args.timeout,
        on_start=on_start,
        on_complete=on_complete,
    )

    if args.json_output:
        print(format_json_report(report))
    else:
        formatter.render_report(report)

    if report.status in (OverallStatus.PASSED, OverallStatus.WARNINGS):
        return 0
    else:
        return 1


def handle_check(args: argparse.Namespace) -> int:
    root_dir = Path(args.project_dir).resolve()
    if not root_dir.exists():
        if args.json_output:
            print(f'{{"status": "error", "message": "Directory not found: {root_dir}"}}')
        else:
            print(f"Error: Target directory does not exist: {root_dir}", file=sys.stderr)
        return 1

    config = VibeConfig.load(root_dir)
    ecosystem, discovered_checks = discover_checks(root_dir)

    # Merge custom checks from config
    all_checks = list(discovered_checks)
    for custom in config.custom_checks:
        if not any(c.name == custom.name for c in all_checks):
            all_checks.append(custom)

    # If --list requested
    if getattr(args, "list_only", False):
        if args.json_output:
            data = [
                {
                    "name": c.name,
                    "type": c.check_type.value,
                    "command": c.display_command,
                    "description": c.description,
                    "discovered_by": c.discovered_by,
                }
                for c in all_checks
            ]
            import json
            print(json.dumps({"ecosystem": ecosystem, "checks": data}, indent=2))
        else:
            print(f"\nDiscovered checks for {root_dir.name} (Ecosystem: {ecosystem}):")
            for c in all_checks:
                print(f"  • {c.name:<16} [{c.check_type.value:<9}] {c.display_command}")
            print()
        return 0

    formatter = ConsoleFormatter()
    runner = CheckRunner(root_dir=root_dir, config=config)

    # Check if there are no checks at all
    if not all_checks:
        empty_report = VerificationReport(
            project_name=root_dir.name,
            project_type=ecosystem,
            status=OverallStatus.INCOMPLETE,
            incomplete_reason="No test, lint, or typecheck tools were detected in this project. Create a test or add .vibeci.toml with custom checks.",
        )
        if args.json_output:
            print(format_json_report(empty_report))
        else:
            formatter.render_header(root_dir.name, ecosystem, 0)
            formatter.render_report(empty_report)
        return 0

    if not args.json_output:
        formatter.render_header(root_dir.name, ecosystem, len(all_checks))

    on_start = formatter.render_check_start if not args.json_output else None
    on_complete = formatter.render_check_result if not args.json_output else None

    # Run checks
    report = runner.run_all(
        checks=all_checks,
        project_type=ecosystem,
        fail_fast=args.fail_fast,
        timeout_seconds=args.timeout,
        only_checks=args.only,
        failed_only=args.failed_only,
        on_start=on_start,
        on_complete=on_complete,
    )

    if args.json_output:
        print(format_json_report(report))
    else:
        formatter.render_report(report)

    # Exit code: 0 if passed, 1 if failed, 2 if incomplete
    if report.status == OverallStatus.PASSED:
        return 0
    elif report.status == OverallStatus.FAILED:
        return 1
    else:
        return 2


def handle_init(args: argparse.Namespace) -> int:
    root_dir = Path(args.project_dir).resolve()
    target_path = root_dir / ".vibeci.toml"
    if target_path.exists():
        print(f"Configuration file already exists at {target_path}")
        return 0

    create_starter_config(root_dir)
    print(f"Created VibeCI configuration at {target_path}")
    return 0


def handle_doctor(args: argparse.Namespace) -> int:
    root_dir = Path(args.project_dir).resolve()
    print(f"\n⚡ VibeCI Doctor — Project Health & Tool Inspection")
    print(f"Target Directory: {root_dir}")
    print(f"VibeCI Version:   {VERSION}\n")

    # Check installed runtime binaries
    print("Runtime Executables:")
    for tool in ("python3", "uv", "node", "npm", "pnpm", "yarn", "bun"):
        path = shutil.which(tool)
        status = f"[✓] Found at {path}" if path else "[ ] Not installed"
        print(f"  • {tool:<10}: {status}")

    # Check local virtual environment
    print("\nVirtual Environment:")
    found_venv = False
    for venv in (".venv", "venv", "env"):
        vpath = root_dir / venv
        if vpath.exists():
            print(f"  • Found local virtualenv at `{venv}`")
            found_venv = True
    if not found_venv:
        print("  • No local virtualenv (.venv/venv) detected in project root.")

    # Check discovery
    ecosystem, checks = discover_checks(root_dir)
    print(f"\nProject Ecosystem: {ecosystem}")
    print(f"Discovered Checks ({len(checks)}):")
    for c in checks:
        print(f"  • {c.name:<16} ({c.check_type.value}) -> {c.display_command}")

    # Check config
    cfg_file = root_dir / ".vibeci.toml"
    if cfg_file.exists():
        print(f"\nConfiguration: Found custom configuration at {cfg_file.name}")
    else:
        print("\nConfiguration: Zero-config mode (using defaults)")

    # Platform pre-flight check (Vercel)
    vercel_res = audit_vercel_functions(root_dir)
    if vercel_res:
        print(f"\nPlatform Pre-Flight (Vercel): {vercel_res.summary}")

    netlify_res = audit_netlify_config(root_dir)
    if netlify_res:
        print(f"\nPlatform Pre-Flight (Netlify): {netlify_res.summary}")
        for detail in netlify_res.failure_details:
            print(f"  • {detail}")

    print()
    return 0


PRE_PUSH_SCRIPT = """#!/bin/sh
# VibeCI Pre-Push Verification Hook
# Automatically verifies local software before pushing code to remote repo

echo "⚡ VibeCI: Running pre-push verification on modified files..."
vibeci verify --diff || {
    echo ""
    echo "❌ VibeCI: Pre-push verification failed! Fix the issues above before pushing."
    echo "💡 Tip: To bypass this check in an emergency, use: git push --no-verify"
    exit 1
}
"""


def handle_hook(args: argparse.Namespace) -> int:
    root_dir = Path(args.project_dir).resolve()
    git_dir = root_dir / ".git"
    if not git_dir.exists():
        print(f"Error: {root_dir} is not a git repository (no .git directory found).", file=sys.stderr)
        return 1

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_file = hooks_dir / "pre-push"

    if args.action == "install":
        hook_file.write_text(PRE_PUSH_SCRIPT, encoding="utf-8")
        try:
            hook_file.chmod(hook_file.stat().st_mode | 0o111)
        except Exception:
            pass
        print(f"✔ Successfully installed VibeCI pre-push hook at .git/hooks/pre-push")
        print("  Your code will now be automatically verified on every `git push`!")
        return 0
    elif args.action == "remove":
        if hook_file.exists():
            hook_file.unlink()
            print("✔ Removed VibeCI pre-push hook from .git/hooks/pre-push")
        else:
            print("No pre-push hook was found.")
        return 0
    elif args.action == "status":
        if hook_file.exists() and "VibeCI" in hook_file.read_text(errors="ignore"):
            print("✔ VibeCI pre-push hook is active in this repository.")
        else:
            print("○ VibeCI pre-push hook is not currently installed.")
        return 0
    return 0


def handle_rule(args: argparse.Namespace) -> int:
    from vibeci.rules import VIBECI_RULE_CONTENT, get_rule_status, install_rules

    root_dir = Path(args.project_dir).resolve()
    action = getattr(args, "action", "install") or "install"
    agent = getattr(args, "agent", "all")

    if action == "print":
        print(VIBECI_RULE_CONTENT)
        return 0

    if action == "status":
        print(f"\n🤖 VibeCI AI Agent Rules Status ({root_dir.name}):")
        statuses = get_rule_status(root_dir)
        for name, active in statuses.items():
            mark = "[✓] Active" if active else "[ ] Not configured"
            print(f"  • {name:32} : {mark}")
        print()
        return 0

    if action == "install":
        installed = install_rules(root_dir, agent=agent)
        if installed:
            print("\n✔ Successfully configured VibeCI AI verification rules:")
            for f in installed:
                print(f"  • {f}")
            print("\n🤖 Your AI coding assistant will now automatically:")
            print("  1. Run `vibeci verify --diff --json` after creating or editing code.")
            print("  2. Inspect the JSON report for exact error locations and secret leaks.")
            print("  3. Self-heal and rerun verification before finishing.\n")
        else:
            print("\n○ All selected AI rule files are already configured with VibeCI instructions.\n")
        return 0

    return 0


def parse_version_tuple(v: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts) if parts else (0,)


def is_newer_version(current: str, candidate: str) -> bool:
    return parse_version_tuple(candidate) > parse_version_tuple(current)


def get_latest_pypi_version(package_name: str = "vibeci", timeout: float = 4.0) -> Optional[str]:
    url = f"https://pypi.org/pypi/{package_name}/json"
    req = urllib.request.Request(url, headers={"User-Agent": f"vibeci/{VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("info", {}).get("version")
    except Exception:
        return None


def get_upgrade_command() -> list[str]:
    # Check if installed via pipx
    if "pipx" in sys.prefix or "pipx" in sys.executable:
        if shutil.which("pipx"):
            return ["pipx", "upgrade", "vibeci"]

    # Check if installed via uv tool
    if "uv/tools" in sys.executable or ".local/share/uv/tools" in sys.executable:
        if shutil.which("uv"):
            return ["uv", "tool", "upgrade", "vibeci"]

    # Check if uv is available and in virtual environment
    if sys.prefix != sys.base_prefix and shutil.which("uv"):
        return ["uv", "pip", "install", "--upgrade", "vibeci"]

    # Default python -m pip
    return [sys.executable, "-m", "pip", "install", "--upgrade", "vibeci"]


def handle_help(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    subcommand = getattr(args, "subcommand", None)
    if not subcommand:
        parser.print_help()
        return 0

    subparsers_actions = [
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    for sp_action in subparsers_actions:
        if subcommand in sp_action.choices:
            sp_action.choices[subcommand].print_help()
            return 0

    print(f"Unknown subcommand '{subcommand}'. Run 'vibeci help' for available commands.", file=sys.stderr)
    return 1


def handle_upgrade(args: argparse.Namespace) -> int:
    print(f"Checking for VibeCI updates on PyPI (current: v{VERSION})...")
    latest = get_latest_pypi_version("vibeci")

    if not latest:
        print("⚠ Could not check for updates from PyPI (network or timeout).")
        print("You can manually upgrade with: pip install --upgrade vibeci")
        return 1

    if not is_newer_version(VERSION, latest):
        print(f"✔ VibeCI is already up to date (version {VERSION}).")
        return 0

    print(f"⚡ New version available: v{VERSION} → v{latest}")
    upgrade_cmd = get_upgrade_command()

    if getattr(args, "check", False):
        print(f"To upgrade, run: {' '.join(upgrade_cmd)}")
        return 0

    if getattr(args, "dry_run", False):
        print(f"Would execute: {' '.join(upgrade_cmd)}")
        return 0

    print(f"Running upgrade: {' '.join(upgrade_cmd)} ...")
    try:
        proc = subprocess.run(upgrade_cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            print(f"✔ Successfully upgraded VibeCI to version {latest}!")
            return 0
        else:
            print(f"⚠ Automatic upgrade failed with exit code {proc.returncode}.")
            if proc.stderr:
                print(proc.stderr.strip(), file=sys.stderr)
            print("Please run manually: pip install --upgrade vibeci", file=sys.stderr)
            return proc.returncode
    except Exception as e:
        print(f"⚠ Failed to run upgrade command: {e}", file=sys.stderr)
        print("Please run manually: pip install --upgrade vibeci", file=sys.stderr)
        return 1


def main(argv: Optional[list[str]] = None) -> None:
    # Ensure Windows console encoding supports UTF-8 / emojis safely without charmap crashes
    if sys.platform == "win32":
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    try:
        if argv is None:
            raw_argv = sys.argv[1:]
        else:
            raw_argv = list(argv)

        # Normalize common flags/typos to subcommands
        normalized_argv: list[str] = []
        for arg in raw_argv:
            if arg in ("-upgrade", "--upgrade", "-updrade", "--updrade", "-update", "--update"):
                normalized_argv.append("upgrade")
            elif arg in ("-help",):
                normalized_argv.append("help")
            else:
                normalized_argv.append(arg)
        argv = normalized_argv

        parser = build_parser()
        args = parser.parse_args(argv)

        if not args.command:
            # Default action when invoked with no subcommand is 'verify'
            args = parser.parse_args(["verify"] + argv)

        if args.command == "verify":
            sys.exit(handle_verify(args))
        elif args.command == "check":
            sys.exit(handle_check(args))
        elif args.command == "init":
            sys.exit(handle_init(args))
        elif args.command == "doctor":
            sys.exit(handle_doctor(args))
        elif args.command == "hook":
            sys.exit(handle_hook(args))
        elif args.command in ("rule", "rules"):
            sys.exit(handle_rule(args))
        elif args.command in ("upgrade", "update"):
            sys.exit(handle_upgrade(args))
        elif args.command == "help":
            sys.exit(handle_help(parser, args))
        else:
            parser.print_help()
            sys.exit(0)
    except KeyboardInterrupt:
        print("\n\n⚠ Verification interrupted by user.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
