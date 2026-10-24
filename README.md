<p align="center">
  <a href="https://vibeci.wasmer.app" target="_blank">
    <img src="assets/logo.png" width="96" height="96" alt="VibeCI logo" style="border-radius: 20px;" />
  </a>
</p>

<h1 align="center">VibeCI</h1>

<p align="center">
  <strong>Stop burning CI minutes. Catch failures on your laptop in 3 seconds.</strong><br />
  Local verification for AI-generated code — before it reaches CI or production.
</p>

<p align="center">
  <a href="https://vibeci.wasmer.app"><img src="https://img.shields.io/badge/website-vibeci.wasmer.app-8b7cff?style=flat-square" alt="Website" /></a>
  <a href="https://pypi.org/project/vibeci/"><img src="https://img.shields.io/pypi/v/vibeci?color=blue&style=flat-square" alt="PyPI version" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg?style=flat-square" alt="License: MIT" /></a>
</p>

<p align="center">
  <a href="https://vibeci.wasmer.app">🌐 <strong>Live Website</strong></a> ·
  <a href="#-quick-start">Quick Start</a> ·
  <a href="#-what-vibeci-checks">What It Checks</a> ·
  <a href="#-ai-agent-integration">AI Agent Integration</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a>
</p>

<p align="center">
  <strong>Local-first</strong> · <strong>Agent-native</strong> · <strong>Open source</strong>
</p>

---

> *"local build+test before ci is such a clean idea. vibeci is gonna save a bunch of wasted pipeline runs tbh"*  
> — **[@WukongNumber1 on X](https://x.com/WukongNumber1)**

---

## 💡 The Problem

AI coding assistants make it easier than ever to generate code. But generated code is not necessarily working code.

- A feature can compile while referencing missing environment variables.
- A deployment can exceed platform limits (like Vercel Hobby's 12 serverless functions limit) with zero local compiler warnings.
- An application can build successfully and still crash when it starts.
- A hardcoded secret or API key can accidentally get committed and pushed.
- A project can have no tests at all.

Running `npm test` or `pytest` only tells you what the tests you actually wrote can establish.

**VibeCI helps you catch more problems before you push.**

It brings project checks, static analysis, build verification, platform pre-flight audits, environment checks, and runtime smoke testing into one local sub-second workflow. It also produces structured results that AI coding agents can use to investigate and fix failures.

No cloud dashboard. No separate CI server. No requirement to write a complete test suite before getting useful feedback.

---

## 🚀 Quick Start

### 1. Install

VibeCI is written in Python. Install it using `uv` (recommended):

```bash
uv tool install vibeci
```

Alternatively, install it with `pip`:

```bash
python -m pip install vibeci
```

For development from a cloned repository:

```bash
git clone https://github.com/piyush588/vibeCI.git
cd vibeCI
python -m pip install -e .
```

### 2. Verify Your Project

Navigate to the root of your application and run:

```bash
vibeci verify
```

Or simply:

```bash
vibeci
```

VibeCI discovers applicable checks, executes them, and summarizes the results.

### 3. Automate It For Your AI (`vibeci rule`)

Never worry about forgetting to run verification. Run this once in your project root:

```bash
vibeci rule
```

This automatically configures your AI assistant (`.cursorrules`, `CLAUDE.md`, `.github/copilot-instructions.md`, `AGENTS.md`) to **always verify code changes with VibeCI** and inspect the structured JSON report to self-heal errors before finishing tasks.

### 4. Get Results Your AI Can Use

```bash
vibeci verify --json
```

Use the structured report to help your coding agent identify failures, understand their exact `file:line:col` locations, and verify fixes automatically.

> **Tip:** Install the automated Git pre-push hook with `vibeci hook install` to guarantee that unverified or broken code can never be pushed to your remote repository.

---

## 🏗️ What VibeCI Checks

VibeCI is designed to provide useful verification even when a project has few or no tests across **5 verification layers**:

### 1. Static Health and Environment Preflight
*Find problems that can be detected without starting the application.*
- **Platform pre-flight checks:** Catch deployment platform limits before push (e.g., Vercel Serverless Function limits on Hobby vs Pro, preventing wasted CI pipelines).
- **Netlify configuration:** Check `netlify.toml` syntax and HTTP status codes in `_redirects` and `public/_redirects`. Errors include file, line and column locations. This check does not validate every Netlify routing option.
- **Secret leak detection:** Scan for potentially exposed credentials and hardcoded secrets (AWS keys, GitHub tokens, database passwords).
- **Environment preflight:** Compare recognized environment variable references (`process.env`, `os.environ`) with `.env.example`.
- **Type & lint checks:** Run applicable linting and type-checking tools when available (`tsc --noEmit`, `mypy`, `ruff`, `eslint`, `biome`).
- **Configuration audit:** Identify configuration and dependency problems supported by available checks.

### 2. Production Build Verification
*Check whether the project can compile and produce its configured build artifacts.*
- Run the project's configured production build (`npm run build`, `turbo build`, `vite build`, `uv build`).
- Surface compilation and bundling failures that survive static syntax checks.
- Report build errors separately from non-fatal warnings.
- Support build systems and package managers (`npm`, `pnpm`, `yarn`, `bun`, `uv`).

*(A successful build does not guarantee that the application behaves correctly at runtime.)*

### 3. Runtime Smoke Testing (`--smoke`)
*Where supported and configured, exercise the application rather than only inspecting its source.*
- Start the application using a known command in a sandboxed process group.
- Check whether the process boots successfully without crashing.
- Wait for a configured readiness condition (socket listener).
- Probe configured HTTP endpoints (`/`, `/api/health`).
- Report startup failures and unsuccessful HTTP responses (`HTTP >= 500`).
- Cleanly terminate all spawned processes without leaving dangling port listeners.

### 4. Existing Tests
*Discover and run supported test commands.*
- Discover and execute configured `pytest`, `vitest`, `jest` and `bun test` suites.
- Reuses existing testing tools rather than replacing them with a proprietary test runner.
- Enforce non-interactive CI flags to prevent test runners from hanging in interactive watch mode.

*(No tests configured? VibeCI still performs all other applicable checks. It does not automatically guarantee behavioral correctness when tests are absent.)*

### 5. Honest Verification Boundaries
*A useful report must explain not only what passed, but also what remains unverified.*
Depending on the project, that might include:
- **Database Integration:** Database drivers are installed, but no test database was configured.
- **External Third-Party APIs:** Live third-party credentials (payment gateways, messaging) were not exercised.
- **Browser Flows:** No browser automation tool (Playwright, Cypress) is configured.
- **Untested Features:** Features that have no tests or known expected behavior.

*(A skipped or unavailable check is never presented as a passing check.)*

---

## 📋 Understand Your Verification Report

A VibeCI report helps you answer three questions:
1. **What passed?**
2. **What needs attention?**
3. **What could not be verified?**

### Example Terminal Report:

```text
╭──────────────────────────────────────────────────────────────────────────────╮
│ ⚡ VibeCI • Local Verification for AI-Written Code                           │
│ Project: my-app  |  Ecosystem: javascript                                    │
╰──────────────────────────────────────────────────────────────────────────────╯
  ✔ PASSED  env:preflight      env          0.02s  All referenced env vars documented in .env.example
  ✔ PASSED  npm:build          build        2.14s  Production bundle built successfully
  ✔ PASSED  vitest             test         1.12s  14 passed (14)
  ✘ FAILED  security:secrets   security     0.01s  Detected hardcoded credentials
  ⚠ WARNING  npm:lint          lint         1.05s  2 warnings found

── What Needs Fixing? ───────────────────────────────────────────
╭───────────────────────── [FAILED] security:secrets ──────────────────────────╮
│ Issue: Hardcoded database connection string with password                     │
│ Location: src/database.ts:14                                                 │
╰──────────────────────────────────────────────────────────────────────────────╯

── Unverified Capabilities (Honest Boundaries) ─────────────────
  ○ Database Integration        — Drivers installed, but no test database configured.
  ○ External Third-Party APIs   — Live Stripe API credentials were not exercised.
  ○ End-to-End Browser Flows    — No browser automation tool configured.

╭──────────────────────────────────────────────────────────────────────────────╮
│ ✘ VERDICT: FAILED                                                            │
│ 1 check failed. Review evidence above or feed --json to your AI agent.       │
╰──── 3 Passed  •  1 Failed  •  1 Warnings  •  3 Not Checked  (in 4.34s) ──────╯
```

VibeCI distinguishes failed checks, warnings, skipped checks, and capabilities that remain unverified. A green result means the checks that ran passed; it is not an empty guarantee of production readiness.

---

## ⚡ Smart Change-Aware Verification

Large projects can take time to verify in full. VibeCI provides change-aware verification options intended to focus work on modified code:

```bash
# Verify changes detected through Git
vibeci verify --diff

# Compare against a branch
vibeci verify --diff --since main

# Compare against a previous commit
vibeci verify --diff --since HEAD~1
```

*The goal is to reduce unnecessary work while preserving trustworthy feedback.*

> **Important:** Change-aware verification is not equivalent to proving that only changed files can contain regressions. Shared dependencies and indirect effects may require broader checks.

---

## 🤖 AI Agent Integration

VibeCI is designed to fit directly into the development workflow of AI coding assistants (Cursor, Claude Code, GitHub Copilot, Codex, Antigravity).

### ⚡ 1-Command AI Rule Setup (`vibeci rule`)

Configure your AI coding assistant to automatically run VibeCI after changes and read the structured JSON report with a single command:

```bash
# Configure rules for all AI assistants (.cursorrules, CLAUDE.md, .github/copilot-instructions.md, AGENTS.md)
vibeci rule

# Or target a specific assistant
vibeci rule --agent cursor
vibeci rule --agent claude
vibeci rule --agent copilot

# Check rule status across your project
vibeci rule status
```

Once installed, your AI assistant will automatically:
1. Run `vibeci verify --diff --json` after creating or editing files.
2. Read the structured JSON output (`status`, `file_locations`, and `failure_details`).
3. Automatically self-heal errors, fix secret leaks, and re-verify before declaring tasks complete.

See the [AI Agent Integration Guide](docs/AGENT_INTEGRATION.md) for deeper details and schemas.

---

## 🛡️ Automated Pre-Push Verification

VibeCI supports automated Git hook management:

```bash
# Install the pre-push hook
vibeci hook install

# Inspect hook status
vibeci hook status

# Remove the hook
vibeci hook remove
```

A configured pre-push hook runs verification before Git pushes changes to a remote repository and rejects the push when configured checks fail.

> **Note:** Hooks are a useful final guard, but they can be bypassed and cannot guarantee that code is safe or correct. Remote CI and production monitoring remain important.

---

## 🛠️ CLI Reference

| Command | Purpose |
| :--- | :--- |
| `vibeci verify` | Run applicable verification checks |
| `vibeci verify --json` | Return a machine-readable JSON report for AI agents |
| `vibeci verify --diff` | Enable change-aware verification for git-modified files |
| `vibeci verify --since <ref>` | Compare changes against a Git reference (`main`, `HEAD~1`) |
| `vibeci verify --no-build` | Skip production build checks |
| `vibeci verify --no-static` | Skip static and environment preflight checks |
| `vibeci verify --smoke` | Enable supported runtime smoke checks |
| `vibeci verify --build-only` | Run build verification only |
| `vibeci verify --fail-fast` | Stop immediately after the first failure |
| `vibeci verify --timeout <sec>` | Set the maximum duration per check |
| `vibeci upgrade` | Upgrade VibeCI to the latest version available on PyPI |
| `vibeci help [command]` | Display help for VibeCI or a specific subcommand |
| `vibeci rule` | Automatically install verification rules for AI coding assistants |
| `vibeci rule status` | Check which AI assistant rules are configured in the project |
| `vibeci rule print` | Print the raw AI verification prompt protocol to stdout |
| `vibeci hook install` | Install the automated git pre-push hook |
| `vibeci hook status` | Inspect pre-push hook status |
| `vibeci hook remove` | Remove the git pre-push hook |
| `vibeci doctor` | Inspect the local environment, virtualenvs, and available tools |
| `vibeci init` | Generate a `.vibeci.toml` configuration file |

*Run `vibeci verify --help` to see all supported flags.*

---

## 🎯 Design Principles

- **Local-first:** Verification runs on your machine using the tools available to your project with zero network overhead.
- **Agent-native:** Structured JSON output makes verification results directly consumable by coding agents.
- **Reuse, don't reinvent:** VibeCI coordinates existing testing, build, and analysis tools rather than replacing them.
- **Fast feedback:** Change-aware execution and selective check running reduce unnecessary waiting.
- **Honest results:** Passed, failed, skipped, and unverified checks remain distinct without fake confidence scores.
- **Beginner-friendly:** A single verification workflow that works with zero initial configuration.

---

## 🔒 Privacy and Security

- **100% Local:** VibeCI is designed for local verification with zero cloud telemetry or hosted backends.
- **No Data Leaks:** No source code, error snippets, or reports leave your computer.
- **Redaction:** Potential secrets detected during scanning are safeguarded from full diagnostic exposure.
- **Controlled Execution:** Subprocess timeouts and process group management prevent hanging or orphaned processes.

See the [Architecture Specification](docs/ARCHITECTURE.md) for implementation details.

---

## 📖 Documentation

- [Architecture and Technical Specification](docs/ARCHITECTURE.md)
- [AI Agent Integration Guide](docs/AGENT_INTEGRATION.md)
- [Contributing Guide](docs/CONTRIBUTING.md)
- [Product Vision](VISION.md)

---

## 🤝 Contributing

VibeCI is open source, and contributions are welcome. Useful contributions include bug reports, reproducible failure cases, new verification adapters, performance benchmarks, cross-platform fixes, and improvements to verification reports.

Please review the [Contributing Guide](docs/CONTRIBUTING.md) before submitting a pull request.

---

## 🛡️ License

Released under the [MIT License](LICENSE). © 2026 Piyush Kumar.

<p align="center">
  <strong>Your AI writes the code. VibeCI helps you verify it before you push.</strong>
</p>
