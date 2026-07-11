"""Phase A item 2 — code-set stamp `asof` required for backward-looking analyses.

v1.1 breaking change. `stamp(deliverable, asof=None)` raises CodesetVersionError
when the deliverable's `analysis_period` ends more than 90 days before today.
The fix: pass `asof` (typically `analysis_period['end']`) so old data isn't
silently stamped with a current code-set vintage.

Also exercises the analysis_period parsers for dict, "PYxxxx", "FYxxxx", and
"yyyy-Qn" string forms.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from medecon_verify import codeset as cv

# The three tests that drive the CLI `deliver` command / `_load_skill` reach a
# `skills/...render_deliverable.py` script and `output_formats`, neither of which
# travels into medecon-verify (they stay in medecon-stack). The self-contained
# renderer fixtures that replace them are task 0.6.
_needs_fixtures = pytest.mark.xfail(
    reason="needs-0.6-fixtures: drives a skills/ renderer + output_formats not in medecon-verify",
    strict=False,
)


def test_no_analysis_period_uses_today() -> None:
    """The simple `medecon stamp` use case (empty deliverable) must keep working."""
    out = cv.stamp({})
    assert "stamped_at" in out["code_set_versions"]
    assert out["code_set_versions"]["stamped_at"] == date.today().isoformat()


def test_recent_analysis_period_does_not_raise_and_uses_period_end() -> None:
    """If analysis_period is recent (< 90 days), no asof needed; period.end is used."""
    recent_end = (date.today() - timedelta(days=10)).isoformat()
    deliv = {"analysis_period": {"start": "2026-01-01", "end": recent_end}}
    out = cv.stamp(deliv)
    assert out["code_set_versions"]["stamped_at"] == recent_end


def test_old_analysis_period_dict_without_asof_raises() -> None:
    deliv = {"analysis_period": {"start": "2024-01-01", "end": "2024-12-31"}}
    with pytest.raises(cv.CodesetVersionError, match="analysis_period ends 2024-12-31"):
        cv.stamp(deliv)


def test_old_analysis_period_dict_with_asof_succeeds() -> None:
    deliv = {"analysis_period": {"start": "2024-01-01", "end": "2024-12-31"}}
    out = cv.stamp(deliv, asof=date(2024, 12, 31))
    assert out["code_set_versions"]["stamped_at"] == "2024-12-31"
    assert out["code_set_versions"]["icd10cm_fy"] == "FY2025"  # fall PY2024 → FY2025


def test_py_string_analysis_period_recognized() -> None:
    deliv = {"analysis_period": "PY2023"}
    with pytest.raises(cv.CodesetVersionError, match="analysis_period ends 2023-12-31"):
        cv.stamp(deliv)


def test_fy_string_analysis_period_recognized() -> None:
    deliv = {"analysis_period": "FY2024"}
    with pytest.raises(cv.CodesetVersionError, match="analysis_period ends 2024-09-30"):
        cv.stamp(deliv)


def test_calendar_quarter_analysis_period_recognized() -> None:
    deliv = {"analysis_period": "2024-Q4"}
    with pytest.raises(cv.CodesetVersionError, match="analysis_period ends 2024-12-31"):
        cv.stamp(deliv)


def test_calendar_quarter_q1() -> None:
    deliv = {"analysis_period": "2024-Q1"}
    with pytest.raises(cv.CodesetVersionError, match="analysis_period ends 2024-03-31"):
        cv.stamp(deliv)


def test_calendar_quarter_q2() -> None:
    deliv = {"analysis_period": "2024-Q2"}
    with pytest.raises(cv.CodesetVersionError, match="analysis_period ends 2024-06-30"):
        cv.stamp(deliv)


def test_unrecognized_analysis_period_falls_back_to_today() -> None:
    """Unknown formats shouldn't crash — they should behave like no period."""
    deliv = {"analysis_period": "garbage-string"}
    out = cv.stamp(deliv)
    assert out["code_set_versions"]["stamped_at"] == date.today().isoformat()


def test_explicit_asof_always_wins_even_with_old_period() -> None:
    """When asof is passed, no refusal — caller has acknowledged the period."""
    deliv = {"analysis_period": {"start": "2020-01-01", "end": "2020-12-31"}}
    out = cv.stamp(deliv, asof=date(2020, 12, 31))
    assert out["code_set_versions"]["stamped_at"] == "2020-12-31"


@_needs_fixtures
def test_renderer_propagates_codeset_error() -> None:
    """The deliverable renderer must surface CodesetVersionError, not swallow it."""
    from medecon_verify import cli

    mod = cli._load_skill(
        "skills/outputs/analyst-deliverable-templates/scripts/render_deliverable.py",
        "_render_deliv_test_for_codeset",
    )
    payload = {
        "title": "test",
        "analysis_period": {"start": "2024-01-01", "end": "2024-12-31"},
        "scope": {"decision_maker": "test analyst", "decision": "test", "decision_window": "Q4"},
        "findings": [],
    }
    with pytest.raises(cv.CodesetVersionError):
        mod.render("exec-summary", payload)


def test_cli_stamp_command_no_asof_still_works() -> None:
    """CLI `medecon stamp` with no flags must keep printing today's vintages."""
    from click.testing import CliRunner
    from medecon_verify.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["stamp"])
    assert result.exit_code == 0, result.output
    import json
    payload = json.loads(result.output)
    assert payload["stamped_at"] == date.today().isoformat()


def test_cli_stamp_command_with_asof_uses_passed_date() -> None:
    from click.testing import CliRunner
    from medecon_verify.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["stamp", "--asof", "2024-06-15"])
    assert result.exit_code == 0, result.output
    import json
    payload = json.loads(result.output)
    assert payload["stamped_at"] == "2024-06-15"


@_needs_fixtures
def test_cli_deliver_refuses_old_period_without_asof(tmp_path) -> None:
    """End-to-end: backward-looking deliverable hits the refusal at CLI level."""
    import json
    from click.testing import CliRunner
    from medecon_verify.cli import main

    payload = {
        "title": "old analysis",
        "analysis_period": {"start": "2024-01-01", "end": "2024-12-31"},
        "scope": {"decision_maker": "test analyst", "decision": "test", "decision_window": "Q4"},
        "findings": [],
    }
    payload_path = tmp_path / "p.json"
    payload_path.write_text(json.dumps(payload))

    runner = CliRunner()
    result = runner.invoke(main, ["deliver", "exec-summary", str(payload_path)])
    assert result.exit_code == 2
    assert "CODESET STAMP REFUSED" in result.output


@_needs_fixtures
def test_cli_deliver_succeeds_with_asof(tmp_path) -> None:
    import json
    from click.testing import CliRunner
    from medecon_verify.cli import main

    payload = {
        "title": "old analysis",
        "analysis_period": {"start": "2024-01-01", "end": "2024-12-31"},
        "scope": {"decision_maker": "test analyst", "decision": "test", "decision_window": "Q4"},
        "findings": [],
    }
    payload_path = tmp_path / "p.json"
    payload_path.write_text(json.dumps(payload))

    runner = CliRunner()
    result = runner.invoke(
        main, ["deliver", "exec-summary", str(payload_path), "--asof", "2024-12-31"]
    )
    assert result.exit_code == 0, result.output


def test_cli_asof_bad_format_rejected() -> None:
    from click.testing import CliRunner
    from medecon_verify.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["stamp", "--asof", "06/15/2024"])
    assert result.exit_code != 0
    assert "YYYY-MM-DD" in result.output
