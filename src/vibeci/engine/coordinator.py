"""Master Verification Coordinator for VibeCI.

Orchestrates all 5 verification layers:
1. Static Health & Preflight (Env audit, secret scanning, typecheck, lint)
2. Production Build Verification (Bundling and compilation)
3. Behavior & Existing Test Suites (Unit & integration checks)
4. Runtime Smoke Testing (Boot, crash detection, HTTP probing)
5. Honest Accounting of Unverified Capabilities
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

from vibeci.config import VibeConfig
from vibeci.detectors import discover_checks
from vibeci.detectors.netlify import audit_netlify_config
from vibeci.detectors.vercel import audit_vercel_functions
from vibeci.engine.build.build_verifier import BuildVerifier
from vibeci.engine.diff.analyzer import ChangedFilesResult, GitChangeAnalyzer
from vibeci.engine.health.env_audit import EnvAuditor
from vibeci.engine.health.secret_scan import SecretScanner
from vibeci.engine.runtime.smoke_tester import RuntimeSmokeTester
from vibeci.models import (
    CheckDefinition,
    CheckLayer,
    CheckResult,
    CheckStatus,
    CheckType,
    OverallStatus,
    UnverifiedCapability,
    VerificationReport,
)
from vibeci.runner import CheckRunner


class VerificationEngine:
    def __init__(self, root_dir: Path, config: Optional[VibeConfig] = None):
        self.root_dir = root_dir.resolve()
        self.config = config or VibeConfig.load(self.root_dir)
        self.runner = CheckRunner(root_dir=self.root_dir, config=self.config)

    def identify_unverified_capabilities(self, checks: list[CheckResult]) -> list[UnverifiedCapability]:
        """Honest accounting of critical software capabilities that could not be verified."""
        unverified: list[UnverifiedCapability] = []

        pkg_json = self.root_dir / "package.json"
        pyproject = self.root_dir / "pyproject.toml"
        req_txt = self.root_dir / "requirements.txt"

        pkg_content = pkg_json.read_text(encoding="utf-8", errors="ignore").lower() if pkg_json.exists() else ""
        pyproject_content = pyproject.read_text(encoding="utf-8", errors="ignore").lower() if pyproject.exists() else ""
        req_content = req_txt.read_text(encoding="utf-8", errors="ignore").lower() if req_txt.exists() else ""
        all_deps = f"{pkg_content}\n{pyproject_content}\n{req_content}"

        # 1. Database integration check
        has_db_test = any("db" in c.name.lower() or "database" in c.name.lower() for c in checks)
        db_keywords = (
            "mongoose", "mongodb", "pg", "prisma", "typeorm", "sqlite",
            "sqlalchemy", "django", "tortoise", "psycopg", "motor", "mysql", "redis"
        )
        has_db_deps = any(kw in all_deps for kw in db_keywords)

        if has_db_deps and not has_db_test:
            unverified.append(
                UnverifiedCapability(
                    name="Database Integration",
                    reason="Database drivers are installed, but no test database or isolated DB tests were configured.",
                )
            )

        # 2. Live 3rd-party APIs (Stripe, Razorpay, WhatsApp, Resend, AI APIs, etc.)
        api_keywords = (
            "stripe", "razorpay", "twilio", "resend", "sendgrid", "openai",
            "anthropic", "google-generativeai", "boto3", "aws-sdk", "firebase",
            "supabase", "axios", "requests", "httpx", "aiohttp", "node-fetch", "got"
        )
        has_api_deps = any(kw in all_deps for kw in api_keywords)
        if has_api_deps:
            unverified.append(
                UnverifiedCapability(
                    name="External Third-Party APIs",
                    reason="Live third-party API credentials (payment gateways, messaging, AI services) were not exercised.",
                )
            )

        # 3. End-to-End Browser UI Flows
        has_playwright = any("playwright" in c.name.lower() or "cypress" in c.name.lower() for c in checks)
        frontend_keywords = ("react", "vue", "svelte", "next", "nuxt", "vite", "angular", "astro", "solid-js")
        has_frontend = any(kw in all_deps for kw in frontend_keywords)
        if not has_frontend:
            if any(self.root_dir.glob("*.html")) or any((self.root_dir / "public").glob("*.html")) or any((self.root_dir / "src").glob("*.html")):
                has_frontend = True

        if has_frontend and not has_playwright:
            unverified.append(
                UnverifiedCapability(
                    name="End-to-End Browser Flows",
                    reason="No browser automation tool (Playwright/Cypress) configured for full user journey verification.",
                )
            )

        return unverified

    def verify(
        self,
        run_static: bool = True,
        run_build: bool = True,
        run_tests: bool = True,
        run_smoke: bool = False,
        changed_only: bool = False,
        compare_ref: Optional[str] = None,
        fail_fast: bool = False,
        timeout_seconds: Optional[int] = None,
        on_start: Optional[Callable[[CheckDefinition], None]] = None,
        on_complete: Optional[Callable[[CheckResult], None]] = None,
    ) -> VerificationReport:
        start_time = time.perf_counter()
        results: list[CheckResult] = []

        ecosystem, discovered_checks = discover_checks(self.root_dir)

        report = VerificationReport(
            project_name=self.root_dir.name,
            project_type=ecosystem,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Check git changes if changed_only requested
        target_files: Optional[list[Path]] = None
        change_info: Optional[ChangedFilesResult] = None
        if changed_only:
            analyzer = GitChangeAnalyzer(self.root_dir)
            change_info = analyzer.analyze(compare_ref=compare_ref)
            if change_info.is_git_repo:
                if not change_info.has_changes:
                    # Clean working tree fast exit
                    report.status = OverallStatus.PASSED
                    report.incomplete_reason = "Git working tree is clean. Zero uncommitted changes to verify."
                    report.total_duration_ms = int((time.perf_counter() - start_time) * 1000)
                    return report
                target_files = change_info.files

        # =====================================================================
        # LAYER 1: Static Health & Preflight (Runs even with ZERO tests)
        # =====================================================================
        if run_static:
            # 1. Environment preflight
            env_def = CheckDefinition(name="env:preflight", layer=CheckLayer.ENV_AUDIT, check_type=CheckType.ENV)
            if on_start:
                on_start(env_def)
            env_res = EnvAuditor(self.root_dir).audit(target_files=target_files)
            results.append(env_res)
            if on_complete:
                on_complete(env_res)

            # 2. Secret leak audit
            sec_def = CheckDefinition(name="security:secrets", layer=CheckLayer.SECURITY, check_type=CheckType.SECURITY)
            if on_start:
                on_start(sec_def)
            sec_res = SecretScanner(self.root_dir).scan(target_files=target_files)
            results.append(sec_res)
            if on_complete:
                on_complete(sec_res)

            # 3. Platform pre-flight: Vercel serverless function limits
            vercel_res = audit_vercel_functions(self.root_dir)
            if vercel_res:
                vercel_def = CheckDefinition(
                    name=vercel_res.name,
                    layer=CheckLayer.STATIC_HEALTH,
                    check_type=CheckType.ENV,
                    description="Vercel serverless function deployment limits",
                )
                if on_start:
                    on_start(vercel_def)
                results.append(vercel_res)
                if on_complete:
                    on_complete(vercel_res)

            netlify_res = audit_netlify_config(self.root_dir)
            if netlify_res:
                netlify_def = CheckDefinition(
                    name=netlify_res.name,
                    layer=CheckLayer.STATIC_HEALTH,
                    check_type=CheckType.ENV,
                    description="Netlify configuration syntax and redirect status codes",
                )
                if on_start:
                    on_start(netlify_def)
                results.append(netlify_res)
                if on_complete:
                    on_complete(netlify_res)

            # 3. Static type and lint checks
            static_checks = [c for c in discovered_checks if c.check_type in (CheckType.LINT, CheckType.TYPECHECK)]
            for sc in static_checks:
                sc.layer = CheckLayer.STATIC_HEALTH
                # If changed_only and package filtering applies in monorepo
                if changed_only and change_info and change_info.affected_packages:
                    # Filter check command if turbo or npm script supports it
                    pass
                if on_start:
                    on_start(sc)
                res = self.runner.run_check(sc, timeout_seconds=timeout_seconds)
                res.layer = CheckLayer.STATIC_HEALTH.value
                results.append(res)
                if on_complete:
                    on_complete(res)
                if fail_fast and res.status in (CheckStatus.FAILED, CheckStatus.ERROR):
                    break

        # =====================================================================
        # LAYER 2: Production Build Verification
        # =====================================================================
        # When running in fast change-aware mode, skip full build unless requested
        if run_build and not (changed_only and not run_build) and not (fail_fast and any(r.status == CheckStatus.FAILED for r in results)):
            build_verifier = BuildVerifier(self.root_dir)
            build_def = build_verifier.detect_build_check()
            if build_def:
                if on_start:
                    on_start(build_def)
                build_res = self.runner.run_check(build_def, timeout_seconds=timeout_seconds or 180)
                build_res.layer = CheckLayer.BUILD.value
                results.append(build_res)
                if on_complete:
                    on_complete(build_res)

        # =====================================================================
        # LAYER 3: Behavior & Existing Unit Tests
        # =====================================================================
        if run_tests and not (fail_fast and any(r.status == CheckStatus.FAILED for r in results)):
            test_checks = [c for c in discovered_checks if c.check_type == CheckType.TEST]
            for tc in test_checks:
                tc.layer = CheckLayer.BEHAVIOR

                # Smart selective execution for changed files
                if changed_only and change_info:
                    has_py_changes = any(p.suffix == ".py" for p in change_info.files)
                    if tc.name == "pytest":
                        py_tests = [p for p in change_info.related_test_files if p.suffix == ".py"]
                        if py_tests:
                            test_paths = [str(p.relative_to(self.root_dir)) for p in py_tests]
                            tc.command = tc.command + test_paths
                        elif not has_py_changes:
                            # No Python files modified or related, skip full suite
                            continue

                if on_start:
                    on_start(tc)
                res = self.runner.run_check(tc, timeout_seconds=timeout_seconds)
                res.layer = CheckLayer.BEHAVIOR.value
                results.append(res)
                if on_complete:
                    on_complete(res)
                if fail_fast and res.status in (CheckStatus.FAILED, CheckStatus.ERROR):
                    break

        # =====================================================================
        # LAYER 4: Runtime Smoke Testing (Optional / on-demand)
        # =====================================================================
        if run_smoke and not (fail_fast and any(r.status == CheckStatus.FAILED for r in results)):
            smoke_def = CheckDefinition(name="runtime:smoke", layer=CheckLayer.RUNTIME_SMOKE, check_type=CheckType.SMOKE)
            if on_start:
                on_start(smoke_def)
            smoke_res = RuntimeSmokeTester(self.root_dir).run_smoke_test(timeout_seconds=15)
            results.append(smoke_res)
            if on_complete:
                on_complete(smoke_res)

        # =====================================================================
        # LAYER 5: Honest Unverified Accounting
        # =====================================================================
        report.checks = results
        report.unverified = self.identify_unverified_capabilities(results)
        report.total_duration_ms = int((time.perf_counter() - start_time) * 1000)
        report.compute_status()

        # Cache last run
        self.runner._save_cache(report)

        return report
