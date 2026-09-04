"""Whether this machine can run the engine.

The check that earns its keep is the serverless one. Everything else here is a
courtesy; that one exists because a platform can build the project, report
success, and be structurally incapable of running it -- and nothing in the
deployment says so.
"""

from __future__ import annotations

import platform
import sys

import pytest

from elyon.host import SERVERLESS_MARKERS, HostCheck, HostReport, inspect, verdict


class TestServerlessDetection:
    @pytest.mark.parametrize("variable,name", sorted(SERVERLESS_MARKERS.items()))
    def test_each_platform_is_recognised(self, monkeypatch, variable, name):
        monkeypatch.setenv(variable, "1")
        report = inspect()
        runtime = next(c for c in report.checks if c.name == "runtime")
        assert not runtime.passed
        assert name in runtime.detail

    def test_it_blocks_rather_than_warns(self, monkeypatch):
        # A warning is the wrong shape. There is no configuration that makes an
        # ephemeral function hold a position across invocations.
        monkeypatch.setenv("VERCEL", "1")
        report = inspect()
        assert not report.can_trade_live
        assert report.blockers

    def test_the_reason_is_the_architecture_not_a_missing_package(self, monkeypatch):
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "elyon")
        detail = next(c for c in inspect().checks if c.name == "runtime").detail
        assert "long-lived" in detail
        assert "duplicate-position" in detail

    def test_an_empty_variable_is_not_a_serverless_host(self, monkeypatch):
        # Some CI systems export the name with no value. That is not the same
        # as running inside it, and blocking on it would refuse to start on an
        # ordinary machine.
        monkeypatch.setenv("VERCEL", "")
        runtime = next(c for c in inspect().checks if c.name == "runtime")
        assert runtime.passed

    def test_an_ordinary_machine_passes(self, monkeypatch):
        for variable in SERVERLESS_MARKERS:
            monkeypatch.delenv(variable, raising=False)
        runtime = next(c for c in inspect().checks if c.name == "runtime")
        assert runtime.passed


class TestTheHostItself:
    def test_the_operating_system_is_reported_honestly(self):
        check = next(c for c in inspect().checks if c.name == "os")
        assert check.passed == (platform.system() == "Windows")
        if not check.passed:
            # Not blocking: everything except reaching a broker through MT5
            # works fine anywhere.
            assert not check.blocking
            assert "Windows-only" in check.detail

    def test_a_missing_timezone_database_blocks(self):
        # Every ICT model is defined by a window in New York local time, and a
        # minimal container image often ships without tzdata. Without it those
        # models raise on the first bar they evaluate.
        check = next(c for c in inspect().checks if c.name == "timezones")
        assert check.blocking

    def test_an_unwritable_directory_blocks(self, tmp_path):
        target = tmp_path / "readonly"
        target.mkdir()
        target.chmod(0o500)
        try:
            check = next(
                c for c in inspect(target).checks if c.name == "filesystem"
            )
            if check.passed:
                pytest.skip("running as a user that ignores file permissions")
            assert check.blocking
            assert "order journal" in check.detail
        finally:
            target.chmod(0o700)

    def test_temporary_storage_is_flagged_as_not_durable(self):
        # Writable is not the same as durable. A journal in /tmp looks fine
        # right up until the moment it is needed, which is after a crash.
        check = next(c for c in inspect("/tmp").checks if c.name == "durability")
        assert not check.passed
        assert not check.blocking
        assert "after a crash" in check.detail

    def test_ordinary_storage_is_not_flagged(self, tmp_path):
        if str(tmp_path).startswith(("/tmp", "/var/tmp")):
            pytest.skip("pytest's tmp_path is itself under /tmp here")
        check = next(c for c in inspect(tmp_path).checks if c.name == "durability")
        assert check.passed

    def test_a_missing_directory_is_created_rather_than_failing(self, tmp_path):
        check = next(
            c for c in inspect(tmp_path / "not" / "there").checks
            if c.name == "filesystem"
        )
        assert check.passed


class TestTheVerdict:
    def test_a_blocked_host_is_told_it_cannot_run_the_engine(self, monkeypatch):
        monkeypatch.setenv("VERCEL", "1")
        answer = verdict(inspect())
        assert "cannot run the engine" in answer
        assert "Vercel" in answer

    def test_a_linux_host_is_told_what_it_can_still_do(self, monkeypatch):
        for variable in SERVERLESS_MARKERS:
            monkeypatch.delenv(variable, raising=False)
        answer = verdict(inspect())
        if platform.system() == "Windows":
            pytest.skip("this assertion is about non-Windows hosts")
        # The useful half: this is a research machine, not a dead end.
        assert "backtest, calibrate and paper trade" in answer
        assert "Windows VPS" in answer

    def test_the_report_prints_a_mark_per_check(self):
        text = str(inspect())
        assert all(mark in text for mark in ("✓",))
        for line in text.splitlines():
            assert line.strip()[0] in "✓✕!"


class TestTheShape:
    def test_blockers_and_advisories_are_separated(self):
        report = HostReport((
            HostCheck("a", False, True, "stops everything"),
            HostCheck("b", False, False, "worth knowing"),
            HostCheck("c", True, True, "fine"),
        ))
        assert [c.name for c in report.blockers] == ["a"]
        assert [c.name for c in report.advisories] == ["b"]
        assert not report.can_trade_live

    def test_host_checks_need_nothing_from_the_engine(self):
        # This tool has to work on the host where the engine does not. An
        # import of the trading modules would make it useless exactly there.
        import elyon.host as host

        source = open(host.__file__).read()
        assert "from elyon." not in source
        assert "import elyon" not in source
