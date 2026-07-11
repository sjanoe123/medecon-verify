"""Top-level CLI for medecon-stack — `medecon` entrypoint.

Usage:
    medecon profile <input-dir>      # /profile-claims-source
    medecon scan <file>              # PHI scan a CSV or JSON file
    medecon stamp                    # print today's code-set vintages
    medecon estimate <sources.json>  # preflight estimator
    medecon scope <payload.json>     # /scope renderer
    medecon analyze <payload.json>   # /analyze renderer
    medecon deliver <template> <payload.json>   # /deliver renderer
    medecon peer-review <draft.md>   # /peer-review checker
    medecon trend-decompose <records.json> --pmpm-field claims_pmpm
    medecon risk-adjust <members.json> [--model cms-hcc-v28]
    medecon episode <claims.json> --target prices.json
    medecon eval-run                 # programmatic eval suite
    medecon version

Each command writes JSON to stdout (deliverable-style markdown to stdout for
renderer commands). Failures print the error to stderr and exit non-zero.
"""
from __future__ import annotations

import importlib.resources
import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path

try:
    import click
except ImportError:  # the CLI ships as the [cli] extra; scripts install unconditionally
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


    def _resolve_skills_root() -> Path:
        """Return an absolute Path to the skills/ tree.

        Two resolution modes:
        - Installed mode: `skills` is a real package whose data files were shipped
          via [tool.setuptools.package-data]. `importlib.resources.files("skills")`
          returns the concrete on-disk root.
        - Editable / source mode: fall back to the repo layout (package sibling).
        """
        try:
            root = importlib.resources.files("skills")
            as_path = Path(str(root))
            if as_path.is_dir():
                return as_path
        except (ModuleNotFoundError, TypeError, ValueError):
            pass
        return Path(__file__).resolve().parents[1] / "skills"


    SKILLS_ROOT = _resolve_skills_root()
    REPO_ROOT = SKILLS_ROOT.parent


    def _load_skill(rel_path: str, name: str):
        """Load a hyphenated-skill script by file path.

        `rel_path` is given relative to the repo root (e.g.
        "skills/methodology/.../scripts/foo.py"); we resolve it against the
        location where skills were actually installed.

        Skill scripts may use bare-import fallbacks (e.g. `import checks` after a
        failed `from . import checks`). To make those resolve, put the script's
        parent dir on sys.path for the duration of the load.
        """
        path = REPO_ROOT / rel_path
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise FileNotFoundError(f"could not load {rel_path} (resolved: {path})")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        parent_dir = str(path.parent)
        inserted = parent_dir not in sys.path
        if inserted:
            sys.path.insert(0, parent_dir)
        try:
            spec.loader.exec_module(mod)
        finally:
            if inserted:
                try:
                    sys.path.remove(parent_dir)
                except ValueError:
                    pass
        return mod


    @click.group()
    def main() -> None:
        """medecon-stack — infrastructure for healthcare-analyst agents."""


    @main.command()
    def version() -> None:
        """Print the installed medecon-stack version."""
        click.echo(__version__)


    # ---------------------------------------------------------------------------
    # Existing thin commands (preserved)
    # ---------------------------------------------------------------------------


    @main.command()
    @click.argument("input_dir", type=click.Path(exists=True, file_okay=False))
    @click.option("--out", default="./briefs", help="Output directory for briefs.")
    def profile(input_dir: str, out: str) -> None:
        """Run /profile-claims-source against an input directory."""
        profiler = _load_skill(
            "skills/orientation/subagents/tabular-file-profiler/scripts/run_profile.py",
            "_run_profile_cli",
        )
        out_path = Path(out)
        out_path.mkdir(parents=True, exist_ok=True)
        result = profiler.run(Path(input_dir), out_path)
        click.echo(json.dumps(result, indent=2, default=str))


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
    def stamp(asof: str | None) -> None:
        """Print code-set version stamps for the chosen as-of date."""
        deliverable: dict = {}
        codeset.stamp(deliverable, asof=_parse_asof(asof))
        click.echo(json.dumps(deliverable["code_set_versions"], indent=2))


    # ---------------------------------------------------------------------------
    # New commands (V9)
    # ---------------------------------------------------------------------------


    @main.command()
    @click.argument("payload_path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--asof", default=None,
                  help="As-of date YYYY-MM-DD to stamp the artifact. Default: today. "
                       "Set it for backward-looking scopes so the artifact date "
                       "matches the analysis, not the day it was rendered.")
    def scope(payload_path: str, asof: str | None) -> None:
        """Render a scoping artifact from a JSON payload."""
        payload = json.loads(Path(payload_path).read_text())
        mod = _load_skill(
            "skills/methodology/analytical-project-scoping/scripts/render_scoping_artifact.py",
            "_scope_cli",
        )
        try:
            click.echo(mod.render(payload, asof=_parse_asof(asof)))
        except mod.ScopingHardFail as e:
            click.echo(f"SCOPE HARD FAIL: {e}", err=True)
            sys.exit(2)


    @main.command()
    @click.argument("payload_path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--asof", default=None, help="As-of date YYYY-MM-DD for code-set stamps. Required for backward-looking analyses with analysis_period set.")
    def analyze(payload_path: str, asof: str | None) -> None:
        """Render an analysis artifact from a JSON payload."""
        payload = json.loads(Path(payload_path).read_text())
        mod = _load_skill(
            "skills/methodology/analytical-project-scoping/scripts/render_analysis_artifact.py",
            "_analyze_cli",
        )
        try:
            click.echo(mod.render(payload, asof=_parse_asof(asof)))
        except mod.AnalysisHardFail as e:
            click.echo(f"ANALYSIS HARD FAIL: {e}", err=True)
            sys.exit(2)
        except codeset.CodesetVersionError as e:
            click.echo(f"CODESET STAMP REFUSED: {e}", err=True)
            sys.exit(2)


    @main.command()
    @click.argument("template")
    @click.argument("payload_path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--asof", default=None, help="As-of date YYYY-MM-DD for code-set stamps. Required for backward-looking analyses with analysis_period set.")
    @click.option("--format", "fmt", default="markdown",
                  type=click.Choice(["markdown", "html", "pdf", "pptx"], case_sensitive=False),
                  help="Output format. PPTX is only valid with template=slide-outline. PDF requires medecon[pdf]. PPTX requires medecon[pptx].")
    @click.option("--out", "out_path", default=None,
                  type=click.Path(dir_okay=False),
                  help="Write output to this path. Required for PDF and PPTX. For markdown/html, defaults to stdout.")
    def deliver(template: str, payload_path: str, asof: str | None,
                fmt: str, out_path: str | None) -> None:
        """Render a deliverable. Templates: exec-summary, methodology, slide-outline, chart-spec, kff-brief."""
        from . import output_formats
        payload = json.loads(Path(payload_path).read_text())
        mod = _load_skill(
            "skills/outputs/analyst-deliverable-templates/scripts/render_deliverable.py",
            "_deliver_cli",
        )
        fmt = fmt.lower()

        # PPTX requires structured slide spec, not markdown — special case.
        if fmt == "pptx":
            if template != "slide-outline":
                click.echo(
                    "DELIVER HARD FAIL: --format pptx requires template=slide-outline "
                    "(PPTX cannot be derived losslessly from non-slide markdown).",
                    err=True,
                )
                sys.exit(2)
            if not out_path:
                click.echo("DELIVER HARD FAIL: --format pptx requires --out PATH.", err=True)
                sys.exit(2)
            pyramid = _load_skill(
                "skills/outputs/pyramid-slide-construction/scripts/render_pyramid.py",
                "_pyramid_cli_pptx",
            )
            try:
                spec = pyramid.to_slide_spec(payload, asof=_parse_asof(asof))
            except pyramid.PyramidSlideError as e:
                click.echo(f"DELIVER HARD FAIL: {e}", err=True)
                sys.exit(2)
            except codeset.CodesetVersionError as e:
                click.echo(f"CODESET STAMP REFUSED: {e}", err=True)
                sys.exit(2)
            try:
                output_formats.slides_to_pptx(spec, Path(out_path))
            except output_formats.OutputFormatNotAvailable as e:
                click.echo(f"DELIVER HARD FAIL: {e}", err=True)
                sys.exit(2)
            click.echo(f"wrote {out_path}", err=True)
            return

        # Markdown / HTML / PDF go through the standard renderer.
        try:
            md = mod.render(template, payload, asof=_parse_asof(asof))
        except mod.DeliverHardFail as e:
            click.echo(f"DELIVER HARD FAIL: {e}", err=True)
            sys.exit(2)
        except codeset.CodesetVersionError as e:
            click.echo(f"CODESET STAMP REFUSED: {e}", err=True)
            sys.exit(2)

        title = payload.get("title", template)
        if fmt == "markdown":
            if out_path:
                Path(out_path).write_text(md)
                click.echo(f"wrote {out_path}", err=True)
            else:
                click.echo(md)
            return
        if fmt == "html":
            html = output_formats.markdown_to_html(md, title=title)
            if out_path:
                Path(out_path).write_text(html)
                click.echo(f"wrote {out_path}", err=True)
            else:
                click.echo(html)
            return
        if fmt == "pdf":
            if not out_path:
                click.echo("DELIVER HARD FAIL: --format pdf requires --out PATH.", err=True)
                sys.exit(2)
            try:
                output_formats.markdown_to_pdf(md, Path(out_path), title=title)
            except output_formats.OutputFormatNotAvailable as e:
                click.echo(f"DELIVER HARD FAIL: {e}", err=True)
                sys.exit(2)
            click.echo(f"wrote {out_path}", err=True)
            return


    @main.command(name="peer-review")
    @click.argument("draft_path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--sidecar", "--scope", "sidecar_path", default=None,
                  type=click.Path(exists=True, dir_okay=False),
                  help="JSON sidecar (the /analyze payload: findings + method / "
                       "hypothesis tree). Supplies the scope-alignment gate. Without "
                       "it the method-vs-question gate cannot run and the report "
                       "carries an explicit 'scope-alignment not checked' note.")
    def peer_review(draft_path: str, sidecar_path: str | None) -> None:
        """Run peer-review checker over a draft markdown.

        The scope-alignment (method-vs-question) gate only fires when a sidecar is
        supplied via --sidecar/--scope. With no sidecar the gate cannot run; the
        report says so explicitly rather than silently skipping it.
        """
        draft = Path(draft_path).read_text()
        sidecar = json.loads(Path(sidecar_path).read_text()) if sidecar_path else None
        mod = _load_skill(
            "skills/methodology/peer-review-for-analytics/scripts/peer_review.py",
            "_peer_review_cli",
        )
        report = mod.review(draft, sidecar=sidecar)
        click.echo(mod.render_review_report(report))
        if not report.passes:
            sys.exit(2)


    @main.command(name="trend-decompose")
    @click.argument("records_path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--pmpm-field", default="claims_pmpm")
    @click.option("--window", default=3, type=int)
    def trend_decompose(records_path: str, pmpm_field: str, window: int) -> None:
        """Decompose PMPM trend (Milliman pattern)."""
        records = json.loads(Path(records_path).read_text())
        mod = _load_skill(
            "skills/domain/healthcare-economics-fundamentals/scripts/trend_decompose.py",
            "_trend_decompose_cli",
        )
        result = mod.decompose(records, pmpm_field=pmpm_field, window=window)
        click.echo(json.dumps(result.to_dict(), indent=2))


    @main.command(name="risk-adjust")
    @click.argument("members_path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--model", default="cms-hcc-v28")
    def risk_adjust(members_path: str, model: str) -> None:
        """Score a member panel's RAF for the chosen model."""
        members = json.loads(Path(members_path).read_text())
        mod = _load_skill(
            "skills/domain/risk-adjustment-mechanics/scripts/risk_adjustment.py",
            "_risk_adjust_cli",
        )
        scores = mod.score_population(members, model=model)
        click.echo(json.dumps([
            {"member_id": s.member_id, "raf": s.raf,
             "demo": s.demographic_component,
             "hcc": s.hcc_component,
             "interaction": s.interaction_component,
             "triggered_hccs": s.triggered_hccs}
            for s in scores
        ], indent=2))


    @main.command()
    @click.argument("claims_path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--target", required=True, type=click.Path(exists=True))
    def episode(claims_path: str, target: str) -> None:
        """Construct BPCI-A episodes from claim records."""
        claims = json.loads(Path(claims_path).read_text())
        targets = json.loads(Path(target).read_text())
        mod = _load_skill(
            "skills/domain/episode-grouping-logic/scripts/episode_constructor.py",
            "_episode_cli",
        )
        bundle = mod.construct(claims, target_prices=targets)
        summary = mod.summarize(bundle)
        click.echo(json.dumps(summary, indent=2))


    @main.command(name="eval-run")
    def eval_run() -> None:
        """Run the programmatic eval suite (no samples — discovery only)."""
        from .certify import runner as eval_runner
        specs = eval_runner.discover_evals(REPO_ROOT)
        by_skill: dict[str, int] = {}
        for s in specs:
            by_skill[s.skill_name] = by_skill.get(s.skill_name, 0) + 1
        click.echo(json.dumps({
            "evals_discovered": len(specs),
            "skills_with_evals": len(by_skill),
            "by_skill": by_skill,
        }, indent=2))


    @main.command()
    @click.option("--samples", "samples_path", default=None,
                  type=click.Path(exists=True, dir_okay=False),
                  help="JSON: {skill: {rendered, sidecar}}. Omit to use the demo set.")
    @click.option("--telemetry", "telemetry_path", default=None,
                  help="Write the study as a JSON telemetry record (file or dir).")
    @click.option("--markdown", "as_markdown", is_flag=True,
                  help="Print the contribution table as markdown instead of JSON.")
    def ablation(samples_path: str | None, telemetry_path: str | None,
                 as_markdown: bool) -> None:
        """Ablation study: measure each discipline's contribution to eval pass-rate.

        Strips one component at a time from a fixed sample set and reports how many
        evals flip pass->fail. A component that flips nothing is INERT — its checker
        has gone decorative. Pass --samples for your own rendered outputs.
        """
        from . import ablation as ab
        from .certify import runner as eval_runner
        if samples_path:
            samples = json.loads(Path(samples_path).read_text())
            specs = eval_runner.discover_evals(REPO_ROOT)
        else:
            # Shared demo set (ablation.demo_study) — same source the module
            # runner uses, so both stay in lockstep.
            specs, samples = ab.demo_study()
        report = ab.run_study(
            specs, samples,
            telemetry_sink=Path(telemetry_path) if telemetry_path else None,
        )
        if as_markdown:
            click.echo(report.to_markdown())
        else:
            click.echo(json.dumps(report.to_dict(), indent=2, default=str))


    @main.command(name="harvest-corrections")
    @click.option("--inbox", default=None,
                  help="Corrections inbox dir. Default: .medecon/org/corrections/inbox/.")
    @click.option("--root", default=".", help="Project root holding the org pack.")
    @click.option("--out", "out_path", default=None,
                  help="Write the digest markdown here. Default: stdout.")
    def harvest_corrections(inbox: str | None, root: str, out_path: str | None) -> None:
        """Batch-harvest a corrections inbox into one owner-grouped digest.

        Proposes doc fixes + eval candidates; applies nothing. Schedule it LOCALLY
        (wrap in /schedule or /loop) — the inbox lives under the gitignored,
        PHI-bearing org pack, so it must never run in CI.
        """
        from . import active_correction as ac
        report = ac.harvest_inbox(root, inbox)
        md = report.to_markdown()
        if out_path:
            Path(out_path).write_text(md, encoding="utf-8")
            click.echo(json.dumps({"digest": out_path, **report.summary}, indent=2))
        else:
            click.echo(md)



if __name__ == "__main__":
    main()
