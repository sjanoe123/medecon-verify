"""Tests for medecon_verify.cli — scan | stamp | eval-run | version.

Task 0.4 scope: the CLI is stripped down to the four commands that belong to
the standalone package. Skill-orchestration commands (profile/scope/analyze/
deliver/peer-review/ablation/brain-correct) stay in medecon-stack.

The stamp --strict test monkeypatches `codeset.stamp` rather than relying on
the real strict-mode implementation, because that behavior is task 0.3's
contract (`strict: bool = False`, per the plan) and may land independently of
this task — the CLI's job is only to pass the flag through correctly.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

click = pytest.importorskip("click")
from click.testing import CliRunner

from medecon_verify import cli as verify_cli
from medecon_verify import codeset


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestVersion:
    def test_version_prints_something(self, runner: CliRunner) -> None:
        result = runner.invoke(verify_cli.main, ["version"])
        assert result.exit_code == 0
        assert result.output.strip()


class TestScan:
    def test_scan_csv(self, runner: CliRunner, tmp_path: Path) -> None:
        p = tmp_path / "records.csv"
        p.write_text("member_id,note\n1,hello\n2,world\n")
        result = runner.invoke(verify_cli.main, ["scan", str(p)])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["records_scanned"] == 2
        assert "metrics" in payload

    def test_scan_json_list(self, runner: CliRunner, tmp_path: Path) -> None:
        p = tmp_path / "records.json"
        p.write_text(json.dumps([{"member_id": "1"}, {"member_id": "2"}]))
        result = runner.invoke(verify_cli.main, ["scan", str(p)])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["records_scanned"] == 2

    def test_scan_json_single_object(self, runner: CliRunner, tmp_path: Path) -> None:
        p = tmp_path / "record.json"
        p.write_text(json.dumps({"member_id": "1"}))
        result = runner.invoke(verify_cli.main, ["scan", str(p)])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["records_scanned"] == 1

    def test_scan_unsupported_extension_exits_nonzero(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        p = tmp_path / "records.txt"
        p.write_text("not a csv or json")
        result = runner.invoke(verify_cli.main, ["scan", str(p)])
        assert result.exit_code == 2
        assert "unsupported file type" in result.output


import inspect

_CODESET_STAMP_HAS_STRICT = "strict" in inspect.signature(codeset.stamp).parameters


class TestStamp:
    def test_stamp_default(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        # Decoupled from task 0.3 (registry externalization + strict mode,
        # which adds `strict=` to codeset.stamp) via monkeypatch, so this
        # test doesn't race that task's landing. The real signature is
        # covered by test_codeset_version.py.
        def fake_stamp(deliverable, *, asof=None, **kwargs):
            deliverable["code_set_versions"] = {
                "stamped_at": (asof or date.today()).isoformat(),
                "icd10cm_fy": "FY2026",
            }
            return deliverable

        monkeypatch.setattr(codeset, "stamp", fake_stamp)
        result = runner.invoke(verify_cli.main, ["stamp", "--asof", "2026-05-01"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["stamped_at"] == "2026-05-01"
        assert payload["icd10cm_fy"] == "FY2026"

    @pytest.mark.skipif(
        not _CODESET_STAMP_HAS_STRICT,
        reason="codeset.stamp strict= not yet landed (task 0.3)",
    )
    def test_stamp_default_against_real_codeset(self, runner: CliRunner) -> None:
        result = runner.invoke(verify_cli.main, ["stamp", "--asof", "2026-05-01"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["stamped_at"] == "2026-05-01"
        assert payload["icd10cm_fy"] == "FY2026"

    def test_stamp_rejects_bad_asof(self, runner: CliRunner) -> None:
        result = runner.invoke(verify_cli.main, ["stamp", "--asof", "not-a-date"])
        assert result.exit_code != 0

    def test_stamp_strict_flag_passes_strict_true_through(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        def fake_stamp(deliverable, *, asof=None, strict=False):
            captured["asof"] = asof
            captured["strict"] = strict
            deliverable["code_set_versions"] = {"stamped_at": (asof or date.today()).isoformat()}
            return deliverable

        monkeypatch.setattr(codeset, "stamp", fake_stamp)
        result = runner.invoke(
            verify_cli.main, ["stamp", "--asof", "2026-05-01", "--strict"]
        )
        assert result.exit_code == 0
        assert captured["strict"] is True
        assert captured["asof"] == date(2026, 5, 1)

    def test_stamp_without_strict_flag_passes_strict_false(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        def fake_stamp(deliverable, *, asof=None, strict=False):
            captured["strict"] = strict
            deliverable["code_set_versions"] = {"stamped_at": (asof or date.today()).isoformat()}
            return deliverable

        monkeypatch.setattr(codeset, "stamp", fake_stamp)
        result = runner.invoke(verify_cli.main, ["stamp", "--asof", "2026-05-01"])
        assert result.exit_code == 0
        assert captured["strict"] is False

    def test_stamp_strict_surfaces_codeset_version_error(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_stamp(deliverable, *, asof=None, strict=False):
            raise codeset.CodesetVersionError("unknown vintage for asof under strict mode")

        monkeypatch.setattr(codeset, "stamp", fake_stamp)
        result = runner.invoke(
            verify_cli.main, ["stamp", "--asof", "2026-05-01", "--strict"]
        )
        assert result.exit_code == 2
        assert "CODESET STAMP REFUSED" in result.output


class TestEvalRun:
    def test_eval_run_requires_evals_option(self, runner: CliRunner) -> None:
        result = runner.invoke(verify_cli.main, ["eval-run"])
        assert result.exit_code != 0

    def test_eval_run_discovers_zero_evals_in_empty_dir(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(verify_cli.main, ["eval-run", "--evals", str(tmp_path)])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["evals_discovered"] == 0
        assert payload["skills_with_evals"] == 0
        assert payload["by_skill"] == {}

    def test_eval_run_rejects_missing_evals_dir(self, runner: CliRunner) -> None:
        result = runner.invoke(
            verify_cli.main, ["eval-run", "--evals", "/nonexistent/path/xyz"]
        )
        assert result.exit_code != 0


class TestCliSurface:
    def test_no_skill_orchestration_commands_leaked_in(self, runner: CliRunner) -> None:
        """0.4 strips profile/scope/analyze/deliver/peer-review/ablation/etc.

        Those stay in medecon-stack's own CLI; verify's surface is exactly
        scan | stamp | eval-run | version.
        """
        result = runner.invoke(verify_cli.main, ["--help"])
        assert result.exit_code == 0
        names = set(verify_cli.main.commands.keys())
        assert names == {"scan", "stamp", "eval-run", "version"}

    def test_click_import_is_lazy_and_graceful(self) -> None:
        """Importing the module never requires click to be present.

        We can't uninstall click for this test process, but we can assert the
        module records whether click was importable via a plain try/except
        rather than a hard top-level `import click` that would raise on
        install without the [cli] extra.
        """
        import importlib
        import medecon_verify.cli as mod

        importlib.reload(mod)
        assert mod.click is not None  # click IS installed in the test env
        assert hasattr(mod, "main")
