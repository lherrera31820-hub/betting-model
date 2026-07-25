#!/usr/bin/env python3
"""
audit_workflow_secrets.py
==========================

Secrets audit tool for GitHub Actions workflows.

Built for the lherrera31820-hub sports-betting pipeline repos
(betting-model, Betbot-), but works on any repo with .github/workflows/*.yml.

What it does
------------
1. Parses every workflow YAML file and extracts every `secrets.<NAME>`
   reference, noting exactly where it appears (job-level env, step-level
   env, `with:` inputs, or inline inside a `run:` script).
2. Cross-references referenced secret names against the secrets actually
   configured on the repo (via `gh secret list`), unless --dry-run is set.
3. Flags:
   - MISSING     - referenced in a workflow but not configured as a repo secret.
   - UNUSED      - configured as a repo secret but never referenced in any workflow.
   - INLINE_RUN  - secret expression interpolated directly into a `run:` shell
                   block instead of routed through `env:` (log/injection risk).
   - ECHOED      - a secret-backed env var appears to be explicitly printed
                   (echo/print/cat) inside a step, which can defeat masking.
   - DEBUG_RISK  - step debug logging (ACTIONS_STEP_DEBUG, `set -x`) is enabled
                   in a job that also handles secrets.
   - PUBLIC_REPO_SECRET - repo secrets exist on a public repository (elevated
                   blast radius if a workflow is ever misconfigured).
   - MALFORMED   - a secrets reference that doesn't match the correct
                   `${{ secrets.NAME }}` GitHub Actions expression syntax.
   - HARDCODED_LOOKALIKE - a high-entropy / known-prefix token committed
                   directly in the YAML instead of pulled from secrets.
4. Generates a Markdown (default), JSON, or plain-text report summarizing
   every finding, grouped by severity, with remediation notes.

Dry-run mode
------------
--dry-run skips all `gh` CLI / GitHub API calls (no network access, no
credentials needed). It only parses the local workflow files and reports
what secrets are referenced and how, without cross-referencing configured
repo secrets. Useful for quick local linting, CI pre-checks on a fork, or
environments without `gh auth` configured. Pair with --known-secrets to
still get MISSING/UNUSED diffing without hitting the API.

Usage
-----
    # Live audit against GitHub (needs `gh auth login` or GH_TOKEN)
    python audit_workflow_secrets.py --repo lherrera31820-hub/betting-model

    # Multiple repos in one report (defaults to both pipeline repos if omitted)
    python audit_workflow_secrets.py \\
        --repo lherrera31820-hub/betting-model \\
        --repo lherrera31820-hub/Betbot-

    # Offline / dry run, no gh calls, using a manual secret list
    python audit_workflow_secrets.py --repo lherrera31820-hub/betting-model \\
        --dry-run --known-secrets DATABASE_URL,ODDS_API_KEY

    # Audit a local checkout instead of calling the API for file contents
    python audit_workflow_secrets.py --repo lherrera31820-hub/betting-model \\
        --local-path ./betting-model

    # Write a JSON report and fail the process (exit 1) on any CRITICAL finding
    python audit_workflow_secrets.py --repo lherrera31820-hub/betting-model \\
        --format json --output report.json --fail-on critical
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import yaml  # PyYAML
except ImportError:
    print("Missing dependency: PyYAML. Install with `pip install pyyaml`.", file=sys.stderr)
    sys.exit(2)


# --------------------------------------------------------------------------- #
# Defaults specific to this repository setup
# --------------------------------------------------------------------------- #

# If --repo is not passed at all, audit both pipeline repos by default.
DEFAULT_REPOS = [
    "lherrera31820-hub/betting-model",
    "lherrera31820-hub/Betbot-",
]

# Secrets we already know this pipeline depends on, used only to enrich
# report notes (e.g. flagging a typo'd near-match). Not authoritative.
KNOWN_PIPELINE_SECRETS = {
    "DATABASE_URL": "Postgres connection string used by database_setup.py / main.py",
    "SPORTSDATAIO_API_KEY": "SportsDataIO subscription key used by main.py ingestion",
    "ODDS_API_KEY": "The Odds API key, optional supplementary odds source",
    "ML_API_KEY": "Legacy/other model API key seen on Betbot-",
}

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]

# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass
class SecretRef:
    name: str
    workflow: str
    job: Optional[str]
    step: Optional[str]
    location: str  # "env_block" | "inline_run" | "with_input" | "job_env" | "workflow_env"
    line_hint: Optional[str] = None  # short snippet for context


@dataclass
class Finding:
    severity: str  # critical|high|medium|low|info
    category: str
    secret: Optional[str]
    workflow: Optional[str]
    detail: str
    remediation: str


@dataclass
class RepoAudit:
    repo: str
    visibility: Optional[str] = None
    workflows_scanned: list = field(default_factory=list)
    referenced_secrets: dict = field(default_factory=dict)  # name -> list[SecretRef]
    configured_secrets: Optional[set] = None  # None if unknown (dry-run, no --known-secrets)
    findings: list = field(default_factory=list)
    dry_run: bool = False
    errors: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# gh CLI helpers
# --------------------------------------------------------------------------- #


def run_gh(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(["gh"] + args, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def gh_repo_visibility(repo: str) -> Optional[str]:
    rc, out, err = run_gh(["repo", "view", repo, "--json", "visibility"])
    if rc != 0:
        return None
    try:
        return json.loads(out).get("visibility")
    except json.JSONDecodeError:
        return None


def gh_secret_list(repo: str) -> Optional[set[str]]:
    rc, out, err = run_gh(["secret", "list", "-R", repo, "--json", "name"])
    if rc != 0:
        return None
    try:
        return {item["name"] for item in json.loads(out)}
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def gh_list_workflow_files(repo: str) -> Optional[list[str]]:
    rc, out, err = run_gh(
        ["api", f"repos/{repo}/contents/.github/workflows", "--jq", ".[].name"]
    )
    if rc != 0:
        return None
    return [line for line in out.splitlines() if line.strip()]


def gh_get_file_content(repo: str, path: str) -> Optional[str]:
    rc, out, err = run_gh(["api", f"repos/{repo}/contents/{path}", "--jq", ".content"])
    if rc != 0:
        return None
    try:
        return base64.b64decode(out.strip()).decode("utf-8", errors="replace")
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Workflow loading (live via gh API, or local checkout)
# --------------------------------------------------------------------------- #


def load_workflows(repo: str, local_path: Optional[str], dry_run: bool) -> dict[str, str]:
    """Returns {filename: raw_yaml_text}."""
    if local_path:
        wf_dir = Path(local_path) / ".github" / "workflows"
        if not wf_dir.is_dir():
            return {}
        out = {}
        for p in sorted(wf_dir.glob("*.y*ml")):
            out[p.name] = p.read_text(encoding="utf-8", errors="replace")
        return out

    if dry_run:
        # In pure dry-run with no local checkout, we can't fetch files either
        # (that would require network/API access). Caller should pass
        # --local-path if they want dry-run to inspect real file content.
        return {}

    names = gh_list_workflow_files(repo)
    if names is None:
        return {}
    out = {}
    for name in names:
        if not name.endswith((".yml", ".yaml")):
            continue
        content = gh_get_file_content(repo, f".github/workflows/{name}")
        if content is not None:
            out[name] = content
    return out


# --------------------------------------------------------------------------- #
# Parsing: extract secret references with location context
# --------------------------------------------------------------------------- #

SECRET_EXPR_RE = re.compile(r"\$\{\{\s*secrets\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# Catches near-miss malformed forms: missing $, missing braces, wrong case function, etc.
MALFORMED_SECRET_RE = re.compile(
    r"(?<!\$)\{\{\s*secrets\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"          # missing leading $
    r"|\$secrets\.([A-Za-z_][A-Za-z0-9_]*)\b"                          # $secrets.NAME (no braces)
    r"|\$\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"                # singular "secret."
)

ECHO_LIKE_RE = re.compile(r"\b(echo|print|printf|cat|console\.log)\b", re.IGNORECASE)
DEBUG_FLAG_RE = re.compile(r"(set\s+-x|set\s+-o\s+xtrace|ACTIONS_STEP_DEBUG|ACTIONS_RUNNER_DEBUG)")

# Heuristics for secret-lookalike literals hardcoded in YAML (not via secrets.*)
HARDCODED_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key ID pattern"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "OpenAI-style secret key pattern"),
    (re.compile(r"ghp_[A-Za-z0-9]{30,}"), "GitHub personal access token pattern"),
    (re.compile(r"postgres(ql)?://[^:\s]+:[^@\s]+@"), "Embedded DB credential in a connection string"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "JWT-shaped token"),
]


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def find_high_entropy_literals(text: str, min_len: int = 24) -> list[str]:
    """Flags long, high-entropy quoted literals that look like leaked secrets
    rather than legitimate config (best-effort heuristic, not a bare regex noise
    generator)."""
    hits = []
    for m in re.finditer(r"['\"]([A-Za-z0-9+/_\-\.]{%d,})['\"]" % min_len, text):
        token = m.group(1)
        if "secrets." in token or "github." in token or token.count("-") > 6:
            continue  # looks like an expression fragment or a UUID list, skip
        ent = shannon_entropy(token)
        if ent >= 4.0:
            hits.append(token)
    return hits


def extract_secret_refs(workflow_name: str, raw_yaml: str) -> tuple[list[SecretRef], list[Finding]]:
    refs: list[SecretRef] = []
    findings: list[Finding] = []

    try:
        doc = yaml.safe_load(raw_yaml) or {}
    except yaml.YAMLError as e:
        findings.append(
            Finding(
                severity="high",
                category="PARSE_ERROR",
                secret=None,
                workflow=workflow_name,
                detail=f"Could not parse YAML: {e}",
                remediation="Fix YAML syntax so the workflow (and this audit) can be evaluated.",
            )
        )
        return refs, findings

    # Malformed reference syntax (regex over raw text, since a malformed
    # expression is still just a string to the YAML parser).
    for m in MALFORMED_SECRET_RE.finditer(raw_yaml):
        name = next(g for g in m.groups() if g)
        findings.append(
            Finding(
                severity="high",
                category="MALFORMED",
                secret=name,
                workflow=workflow_name,
                detail=f"Malformed secrets reference near: `{m.group(0)}`",
                remediation="Use the exact syntax `${{ secrets.%s }}`." % name,
            )
        )

    # Hardcoded lookalikes
    for pattern, label in HARDCODED_PATTERNS:
        for m in pattern.finditer(raw_yaml):
            findings.append(
                Finding(
                    severity="critical",
                    category="HARDCODED_LOOKALIKE",
                    secret=None,
                    workflow=workflow_name,
                    detail=f"{label} found hardcoded in workflow text: `{m.group(0)[:12]}...`",
                    remediation="Remove the literal value, rotate it immediately if real, "
                    "and move it into a GitHub Actions secret referenced via `secrets.*`.",
                )
            )
    for token in find_high_entropy_literals(raw_yaml):
        findings.append(
            Finding(
                severity="medium",
                category="HARDCODED_LOOKALIKE",
                secret=None,
                workflow=workflow_name,
                detail=f"High-entropy literal committed in workflow (possible leaked token): "
                f"`{token[:10]}...` (len={len(token)})",
                remediation="Verify this isn't a real credential. If it is, rotate it and "
                "replace it with a `secrets.*` reference.",
            )
        )

    # Debug flags anywhere in the file
    debug_enabled = bool(DEBUG_FLAG_RE.search(raw_yaml))

    jobs = doc.get("jobs") or {}
    if not isinstance(jobs, dict):
        jobs = {}

    workflow_env = doc.get("env") or {}
    for key, val in (workflow_env or {}).items():
        for m in SECRET_EXPR_RE.finditer(str(val)):
            refs.append(SecretRef(m.group(1), workflow_name, None, None, "workflow_env", f"{key}: {val}"))

    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        job_env = job.get("env") or {}
        job_has_secret = False
        for key, val in (job_env or {}).items():
            for m in SECRET_EXPR_RE.finditer(str(val)):
                refs.append(SecretRef(m.group(1), workflow_name, job_name, None, "job_env", f"{key}: {val}"))
                job_has_secret = True

        steps = job.get("steps") or []
        if not isinstance(steps, list):
            steps = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_name = step.get("name") or step.get("id") or step.get("uses") or "(unnamed step)"

            step_env = step.get("env") or {}
            for key, val in (step_env or {}).items():
                for m in SECRET_EXPR_RE.finditer(str(val)):
                    refs.append(
                        SecretRef(m.group(1), workflow_name, job_name, step_name, "env_block", f"{key}: {val}")
                    )
                    job_has_secret = True

            with_block = step.get("with") or {}
            for key, val in (with_block or {}).items():
                for m in SECRET_EXPR_RE.finditer(str(val)):
                    refs.append(
                        SecretRef(m.group(1), workflow_name, job_name, step_name, "with_input", f"{key}: {val}")
                    )
                    job_has_secret = True

            run_block = step.get("run")
            if isinstance(run_block, str):
                for m in SECRET_EXPR_RE.finditer(run_block):
                    name = m.group(1)
                    refs.append(
                        SecretRef(name, workflow_name, job_name, step_name, "inline_run", m.group(0))
                    )
                    job_has_secret = True
                    findings.append(
                        Finding(
                            severity="high",
                            category="INLINE_RUN",
                            secret=name,
                            workflow=workflow_name,
                            detail=(
                                f"`{name}` is interpolated directly into a `run:` shell block in "
                                f"job `{job_name}` / step `{step_name}` instead of via `env:`."
                            ),
                            remediation=(
                                f"Move `{name}` into a step- or job-level `env:` block and reference it "
                                f"as `$" + name + "` inside the script. Directly substituting "
                                "`${{ secrets.* }}` into shell text risks command-injection and can "
                                "bypass automatic log masking for anything derived from the value."
                            ),
                        )
                    )

                # Echo-like exposure check: does this step both use a secret-backed
                # env var (from any env block at env_block/job_env/workflow_env
                # level with a name matching a bash variable) AND explicitly print it?
                bound_names = {r.name for r in refs if r.workflow == workflow_name and r.job == job_name}
                for line in run_block.splitlines():
                    if ECHO_LIKE_RE.search(line):
                        for secret_name in bound_names:
                            # crude but effective: env var name usually mirrors secret name
                            if re.search(rf"\${{?{secret_name}\b", line) or re.search(
                                rf"\b{secret_name}\b", line
                            ):
                                findings.append(
                                    Finding(
                                        severity="critical",
                                        category="ECHOED",
                                        secret=secret_name,
                                        workflow=workflow_name,
                                        detail=(
                                            f"Job `{job_name}` / step `{step_name}` appears to print "
                                            f"`{secret_name}` directly: `{line.strip()[:120]}`"
                                        ),
                                        remediation=(
                                            "Never echo/print/cat a secret-backed variable, even for "
                                            "debugging. GitHub's log masking is best-effort and can be "
                                            "defeated by partial prints, encoding, or string concatenation."
                                        ),
                                    )
                                )

        if debug_enabled and job_has_secret:
            findings.append(
                Finding(
                    severity="medium",
                    category="DEBUG_RISK",
                    secret=None,
                    workflow=workflow_name,
                    detail=(
                        f"Job `{job_name}` handles secrets and the workflow also enables shell/step "
                        "debug tracing (`set -x` or ACTIONS_STEP_DEBUG)."
                    ),
                    remediation=(
                        "Avoid `set -x`/xtrace in jobs that touch secret-backed env vars — trace output "
                        "can print full command lines including expanded variable values."
                    ),
                )
            )

    return refs, findings


# --------------------------------------------------------------------------- #
# Cross-referencing + repo-level findings
# --------------------------------------------------------------------------- #


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def cross_reference(audit: RepoAudit) -> None:
    referenced = set(audit.referenced_secrets.keys())

    if audit.configured_secrets is None:
        audit.findings.append(
            Finding(
                severity="info",
                category="NO_LIVE_CHECK",
                secret=None,
                workflow=None,
                detail="Dry-run mode (or `gh` lookup unavailable): configured repo secrets were "
                "not fetched, so MISSING/UNUSED comparisons were skipped. Pass --known-secrets "
                "to still diff against a manual list.",
                remediation="Run without --dry-run (with `gh auth login` completed) for a full audit.",
            )
        )
        return

    missing = sorted(referenced - audit.configured_secrets)
    unused = sorted(audit.configured_secrets - referenced)

    for name in missing:
        # suggest a likely typo match against configured secrets
        close = [
            c for c in audit.configured_secrets if levenshtein(c.upper(), name.upper()) <= 3 and c != name
        ]
        hint = f" Closest configured secret name(s): {', '.join(close)}." if close else ""
        audit.findings.append(
            Finding(
                severity="critical",
                category="MISSING",
                secret=name,
                workflow=None,
                detail=(
                    f"`{name}` is referenced in one or more workflows but is NOT configured as a "
                    f"repo secret on {audit.repo}.{hint}"
                ),
                remediation=(
                    f"Run `gh secret set {name} -R {audit.repo}` (or add it under Settings > "
                    "Secrets and variables > Actions) before this workflow is relied on — "
                    "affected runs will fail or silently receive an empty value."
                ),
            )
        )

    for name in unused:
        sev = "high" if audit.visibility == "PUBLIC" else "medium"
        extra = ""
        if audit.visibility == "PUBLIC":
            extra = " This is a PUBLIC repository — unused credential material sitting in Actions secrets unnecessarily widens the blast radius if any future workflow change (including from a compromised action) can read it."
        audit.findings.append(
            Finding(
                severity=sev,
                category="UNUSED",
                secret=name,
                workflow=None,
                detail=(
                    f"`{name}` is configured as a repo secret on {audit.repo} but is never referenced "
                    f"in any current workflow.{extra}"
                ),
                remediation=(
                    f"If `{name}` is no longer needed, remove it with `gh secret remove {name} -R "
                    f"{audit.repo}`. If it's needed by a workflow that hasn't been added yet, reference "
                    "it explicitly so its purpose is documented in code."
                ),
            )
        )

    if audit.visibility == "PUBLIC" and audit.configured_secrets:
        audit.findings.append(
            Finding(
                severity="info",
                category="PUBLIC_REPO_SECRET",
                secret=None,
                workflow=None,
                detail=(
                    f"{audit.repo} is a PUBLIC repository with {len(audit.configured_secrets)} "
                    f"Actions secret(s) configured ({', '.join(sorted(audit.configured_secrets))})."
                ),
                remediation=(
                    "Confirm no workflow triggered by `pull_request` from a fork (as opposed to "
                    "`pull_request_target`/`workflow_run`) ever exposes these secrets, and that only "
                    "trusted, reviewed workflows can read them."
                ),
            )
        )


# --------------------------------------------------------------------------- #
# Report generation
# --------------------------------------------------------------------------- #


def severity_rank(sev: str) -> int:
    return SEVERITY_ORDER.index(sev) if sev in SEVERITY_ORDER else len(SEVERITY_ORDER)


def build_report(audits: list[RepoAudit], generated_at: str) -> dict:
    report = {"generated_at": generated_at, "repos": []}
    for audit in audits:
        counts = Counter(f.severity for f in audit.findings)
        report["repos"].append(
            {
                "repo": audit.repo,
                "visibility": audit.visibility,
                "dry_run": audit.dry_run,
                "workflows_scanned": audit.workflows_scanned,
                "referenced_secrets": {
                    name: [
                        {
                            "workflow": r.workflow,
                            "job": r.job,
                            "step": r.step,
                            "location": r.location,
                            "hint": r.line_hint,
                        }
                        for r in refs
                    ]
                    for name, refs in audit.referenced_secrets.items()
                },
                "configured_secrets": sorted(audit.configured_secrets) if audit.configured_secrets is not None else None,
                "finding_counts": dict(counts),
                "findings": [
                    {
                        "severity": f.severity,
                        "category": f.category,
                        "secret": f.secret,
                        "workflow": f.workflow,
                        "detail": f.detail,
                        "remediation": f.remediation,
                    }
                    for f in sorted(audit.findings, key=lambda f: severity_rank(f.severity))
                ],
                "errors": audit.errors,
            }
        )
    return report


def render_markdown(report: dict) -> str:
    lines = ["# GitHub Actions Secrets Audit Report", "", f"Generated: {report['generated_at']}", ""]
    total_findings = sum(len(r["findings"]) for r in report["repos"])
    total_critical = sum(
        1 for r in report["repos"] for f in r["findings"] if f["severity"] == "critical"
    )
    lines.append(f"**Repos audited:** {len(report['repos'])} · "
                 f"**Total findings:** {total_findings} · **Critical:** {total_critical}")
    lines.append("")

    for r in report["repos"]:
        lines.append(f"## {r['repo']}")
        vis = r["visibility"] or "unknown"
        lines.append(f"- Visibility: **{vis}**")
        lines.append(f"- Mode: {'dry-run (no live GitHub calls)' if r['dry_run'] else 'live'}")
        lines.append(f"- Workflows scanned: {', '.join(r['workflows_scanned']) or 'none found'}")
        if r["configured_secrets"] is not None:
            lines.append(f"- Configured repo secrets: {', '.join(r['configured_secrets']) or '(none)'}")
        else:
            lines.append("- Configured repo secrets: not checked (dry-run)")
        if r["referenced_secrets"]:
            lines.append(f"- Secrets referenced in workflows: {', '.join(sorted(r['referenced_secrets']))}")
        else:
            lines.append("- Secrets referenced in workflows: (none found)")
        lines.append("")

        if not r["findings"]:
            lines.append("No issues found.")
            lines.append("")
            continue

        lines.append("| Severity | Category | Secret | Workflow | Detail |")
        lines.append("|---|---|---|---|---|")
        for f in r["findings"]:
            detail = f["detail"].replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {f['severity'].upper()} | {f['category']} | {f['secret'] or '-'} | "
                f"{f['workflow'] or '-'} | {detail} |"
            )
        lines.append("")

        lines.append("### Remediation")
        for f in r["findings"]:
            lines.append(f"- **[{f['severity'].upper()}/{f['category']}]** {f['secret'] or f['workflow'] or ''}: "
                         f"{f['remediation']}")
        lines.append("")

        if r["errors"]:
            lines.append("### Errors during audit")
            for e in r["errors"]:
                lines.append(f"- {e}")
            lines.append("")

    return "\n".join(lines)


def render_text(report: dict) -> str:
    # Reuse markdown but strip table pipes for a plain console view.
    md = render_markdown(report)
    return re.sub(r"\|", " ", md)


# --------------------------------------------------------------------------- #
# Main audit flow per repo
# --------------------------------------------------------------------------- #


def audit_repo(
    repo: str,
    dry_run: bool,
    local_path: Optional[str],
    known_secrets: Optional[set[str]],
) -> RepoAudit:
    audit = RepoAudit(repo=repo, dry_run=dry_run)

    if not dry_run:
        audit.visibility = gh_repo_visibility(repo)
        if audit.visibility is None:
            audit.errors.append(
                f"Could not determine visibility for {repo} (gh auth/permissions issue, or repo not found)."
            )
        configured = gh_secret_list(repo)
        if configured is None and known_secrets is None:
            audit.errors.append(
                f"Could not list secrets for {repo} via `gh secret list` "
                "(check `gh auth status` and repo admin access)."
            )
            audit.configured_secrets = None
        else:
            audit.configured_secrets = configured if configured is not None else set(known_secrets)
    else:
        audit.configured_secrets = set(known_secrets) if known_secrets is not None else None

    workflows = load_workflows(repo, local_path, dry_run)
    audit.workflows_scanned = sorted(workflows.keys())
    if not workflows:
        audit.errors.append(
            f"No workflow files loaded for {repo} "
            f"({'dry-run with no --local-path' if dry_run and not local_path else 'none found or fetch failed'})."
        )

    referenced: dict[str, list[SecretRef]] = defaultdict(list)
    for wf_name, raw_yaml in workflows.items():
        refs, findings = extract_secret_refs(wf_name, raw_yaml)
        for r in refs:
            referenced[r.name].append(r)
        audit.findings.extend(findings)

    audit.referenced_secrets = dict(referenced)
    cross_reference(audit)
    return audit


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Audit GitHub Actions workflow secrets for missing/unused/exposed configuration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--repo",
        action="append",
        dest="repos",
        help="owner/name of a repo to audit. Repeatable. Defaults to both pipeline repos if omitted.",
    )
    p.add_argument(
        "--local-path",
        help="Path to a local checkout to read workflow files from instead of the GitHub API "
        "(only applies to the single repo passed with --repo; use one at a time with this flag).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip all gh CLI/API calls. Only parses local workflow files (requires --local-path "
        "to see real content) and reports referenced secrets without live cross-referencing, "
        "unless --known-secrets is also given.",
    )
    p.add_argument(
        "--known-secrets",
        help="Comma-separated list of secret names to treat as 'configured', for use with "
        "--dry-run (or as an override in live mode) instead of calling `gh secret list`.",
    )
    p.add_argument(
        "--format",
        choices=["markdown", "json", "text"],
        default="markdown",
        help="Report output format (default: markdown).",
    )
    p.add_argument("--output", help="Write the report to this file instead of stdout.")
    p.add_argument(
        "--fail-on",
        choices=SEVERITY_ORDER,
        default=None,
        help="Exit with status 1 if any finding at or above this severity is present.",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    repos = args.repos or DEFAULT_REPOS
    known_secrets = set(s.strip() for s in args.known_secrets.split(",")) if args.known_secrets else None

    if args.local_path and len(repos) > 1:
        print("--local-path can only be used with a single --repo at a time.", file=sys.stderr)
        return 2

    audits = [
        audit_repo(repo, args.dry_run, args.local_path, known_secrets)
        for repo in repos
    ]

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    report = build_report(audits, generated_at)

    if args.format == "json":
        rendered = json.dumps(report, indent=2)
    elif args.format == "text":
        rendered = render_text(report)
    else:
        rendered = render_markdown(report)

    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(rendered)

    if args.fail_on:
        threshold = severity_rank(args.fail_on)
        for audit in audits:
            for f in audit.findings:
                if severity_rank(f.severity) <= threshold:
                    return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
