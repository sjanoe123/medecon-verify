"""Top-level CLI for medecon-verify — `medecon-verify` entrypoint.

Usage:
    medecon-verify scan <file>              # PHI scan a CSV or JSON file
    medecon-verify stamp [--strict]         # print today's code-set vintages
    medecon-verify eval-run --evals PATH    # programmatic eval suite (discovery only)
    medecon-verify version

Each command writes JSON to stdout. Failures print the error to stderr and
exit non-zero.

The core library (`medecon_verify.adjudication`, `.codeset`, `.privacy`,
`.phi`, `.dateparse`, `.glossary`) is stdlib-only. This CLI is the one place
in the package that imports `click`, and it does so gracefully: importing
`medecon_verify.cli` never fails just because click is missing — only
invoking `main()` does, with a clear install hint (the `[cli]` extra).
"""
from __future__ import annotations

import inspect
import json
import sys
from datetime import date, datetime
from pathlib import Path

try:
    import click
except ImportError:  # the CLI ships as the [cli] extra
    click = None

from . import __version__, codeset


if click is None:
    def main() -> None:  # type: ignore[misc]
        """medecon-verify CLI entry point (requires the [cli] extra)."""
        sys.stderr.write(
            "medecon-verify: the command-line interface requires the optional "
            "'cli' extra.\n"
            "Install it with:  pip install 'medecon-verify[cli]'\n"
        )
        raise SystemExit(1)
else:
    def _parse_asof(value: str | None) -> date | None:
        """Parse a YYYY-MM-DD string into a date, or return None."""
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise click.BadParameter(
                f"--asof must be YYYY-MM-DD; got {value!r}"
            ) from exc


    @click.group()
    def main() -> None:
        """medecon-verify — the correctness-and-compliance layer for healthcare AI agents."""


    @main.command()
    def version() -> None:
        """Print the installed medecon-verify version."""
        click.echo(__version__)


    @main.command()
    @click.argument("path", type=click.Path(exists=True, dir_okay=False))
    def scan(path: str) -> None:
        """Run the PHI scanner over a CSV or JSON file."""
        import csv as _csv
        from .phi import scan as scan_records, scanner_metrics, reset_metrics

        reset_metrics()
        p = Path(path)
        records: list[dict] = []
        if p.suffix.lower() == ".csv":
            with p.open() as f:
                records = list(_csv.DictReader(f))
        elif p.suffix.lower() == ".json":
            with p.open() as f:
                data = json.load(f)
            records = data if isinstance(data, list) else [data]
        else:
            click.echo(f"unsupported file type: {p.suffix}", err=True)
            sys.exit(2)

        redacted = scan_records(records)
        click.echo(json.dumps({
            "records_scanned": len(records),
            "metrics": scanner_metrics(),
            "first_record_redacted": redacted[0] if redacted else None,
        }, indent=2, default=str))


    @main.command()
    @click.option("--asof", default=None, help="As-of date YYYY-MM-DD. Default: today.")
    @click.option("--strict", is_flag=True, default=False,
                  help="Fail closed: raise CodesetVersionError on any unknown "
                       "vintage instead of stamping UNKNOWN.")
    def stamp(asof: str | None, strict: bool) -> None:
        """Print code-set version stamps for the chosen as-of date."""
        deliverable: dict = {}
        # codeset.stamp's `strict=` param is task 0.3's contract (registry
        # externalization + fail-closed mode). Detect support so this CLI
        # works against both the pre-0.3 and post-0.3 signature rather than
        # hard-requiring 0.3 to have landed first.
        kwargs: dict = {"asof": _parse_asof(asof)}
        if "strict" in inspect.signature(codeset.stamp).parameters:
            kwargs["strict"] = strict
        elif strict:
            click.echo(
                "CODESET STAMP REFUSED: --strict is not supported by the "
                "installed medecon_verify.codeset (strict mode ships in a "
                "later release).",
                err=True,
            )
            sys.exit(2)
        try:
            codeset.stamp(deliverable, **kwargs)
        except codeset.CodesetVersionError as e:
            click.echo(f"CODESET STAMP REFUSED: {e}", err=True)
            sys.exit(2)
        click.echo(json.dumps(deliverable["code_set_versions"], indent=2))


    @main.command(name="eval-run")
    @click.option("--evals", "evals_path", required=True,
                  type=click.Path(exists=True, file_okay=False),
                  help="Root directory to search for **/evals/evals.json.")
    def eval_run(evals_path: str) -> None:
        """Run the programmatic eval suite (no samples — discovery only)."""
        from .certify import runner as eval_runner
        specs = eval_runner.discover_evals(Path(evals_path))
        by_skill: dict[str, int] = {}
        for s in specs:
            by_skill[s.skill_name] = by_skill.get(s.skill_name, 0) + 1
        click.echo(json.dumps({
            "evals_discovered": len(specs),
            "skills_with_evals": len(by_skill),
            "by_skill": by_skill,
        }, indent=2))


if __name__ == "__main__":
    main()
