"""
Mock tests for the vm-cpufreq mode: simulate a human typing wrong
parameters (bad thresholds, typos in regexes, wrong host, ...) and
VMs with odd vCPU numbers / frequencies. No vCenter needed.

Run:  pytest -v test_vmcpufreq.py
"""
import re
from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from checkvsphere.vcmd import vmcpufreq

OK, WARNING, CRITICAL, UNKNOWN = 0, 1, 2, 3


# ---------------------------------------------------------------- helpers
def make_host(name="esx01", cpu_mhz=2600):
    host = MagicMock()
    host.name = name
    host.summary.hardware.cpuMhz = cpu_mhz
    return host


def make_vm(name="vm1", cpus=4, used=3000, power="poweredOn", host="default"):
    """Shape returned by find_entity_views(): {'obj': ..., 'props': {...}}"""
    return {
        "obj": MagicMock(),
        "props": {
            "name": name,
            "runtime.powerState": power,
            "runtime.host": make_host() if host == "default" else host,
            "config.hardware.numCPU": cpus,
            "summary.quickStats.overallCpuUsage": used,
        },
    }


def run_mode(monkeypatch, capsys, vms, **opts):
    """Run the plugin with fake CLI args and fake vCenter data."""
    args = Namespace(
        vihost=None, warning=None, critical=None,
        allowed=None, banned=None, percent=False,
    )
    vars(args).update(opts)

    parser = MagicMock()
    parser.get_args.return_value = args
    monkeypatch.setattr(vmcpufreq.cli, "Parser", lambda: parser)
    monkeypatch.setattr(vmcpufreq.service_instance, "connect", lambda a: MagicMock())
    monkeypatch.setattr(vmcpufreq, "find_entity_views", lambda *a, **k: vms)

    with pytest.raises(SystemExit) as exc:
        vmcpufreq.run()
    return exc.value.code, capsys.readouterr().out


# ------------------------------------------------ nominal behaviour
def test_normal_vm_is_ok(monkeypatch, capsys):
    code, out = run_mode(monkeypatch, capsys, [make_vm(cpus=4, used=3000)])
    assert code == OK
    assert "vm1" in out


def test_thresholds_worst_status_wins(monkeypatch, capsys):
    # thresholds apply to MHz per vCPU: 2500 -> ok, 1500 -> warn, 500 -> crit
    vms = [
        make_vm("a", cpus=4, used=10000),
        make_vm("b", cpus=4, used=6000),
        make_vm("c", cpus=4, used=2000),
    ]
    code, out = run_mode(monkeypatch, capsys, vms, warning="2000:", critical="1000:")
    assert code == CRITICAL
    assert "c" in out


# ------------------------- human error: wrong vCPU number / frequency
def test_vm_over_expected_frequency_is_not_ok(monkeypatch, capsys):
    # 4 vCPU -> 8000 MHz expected, VM burns 9000: the text says KO,
    # so the exit code must not be OK
    code, out = run_mode(monkeypatch, capsys, [make_vm(cpus=4, used=9000)])
    assert "Frequences max reached" in out
    assert code != OK


def test_zero_vcpu_is_ignored_not_crashing(monkeypatch, capsys):
    code, out = run_mode(monkeypatch, capsys, [make_vm(cpus=0, used=1000)])
    assert code in (WARNING, UNKNOWN)          # "no VM found", no ZeroDivisionError


def test_missing_vcpu_info(monkeypatch, capsys):
    code, _ = run_mode(monkeypatch, capsys, [make_vm(cpus=None, used=1000)])
    assert code in (WARNING, UNKNOWN)


def test_missing_cpu_usage(monkeypatch, capsys):
    code, _ = run_mode(monkeypatch, capsys, [make_vm(used=None)])
    assert code in (WARNING, UNKNOWN)


def test_odd_vcpu_count_still_works(monkeypatch, capsys):
    # humans create 3, 5 or 7 vCPU VMs too
    code, out = run_mode(monkeypatch, capsys, [make_vm(cpus=5, used=4000)])
    assert code == OK


def test_powered_off_vm_skipped(monkeypatch, capsys):
    code, _ = run_mode(monkeypatch, capsys, [make_vm(power="poweredOff")])
    assert code in (WARNING, UNKNOWN)


# ----------------------------- human error: wrong parameters on the CLI
def test_wrong_host_name_matches_nothing(monkeypatch, capsys):
    code, _ = run_mode(monkeypatch, capsys, [make_vm()], vihost="esx99-typo")
    assert code in (WARNING, UNKNOWN)


def test_right_host_name_matches(monkeypatch, capsys):
    code, out = run_mode(monkeypatch, capsys, [make_vm()], vihost="esx01")
    assert code == OK and "vm1" in out


def test_garbage_threshold_is_rejected(monkeypatch, capsys):
    # --warning abc  must never be silently accepted
    with pytest.raises((ValueError, SystemExit)):
        run_mode(monkeypatch, capsys, [make_vm()], warning="abc", critical="500:")


def test_banned_regex_excludes_everything(monkeypatch, capsys):
    code, _ = run_mode(monkeypatch, capsys, [make_vm("prod-1")], banned=[".*"])
    assert code in (WARNING, UNKNOWN)


def test_allowed_regex_filters(monkeypatch, capsys):
    vms = [make_vm("prod-1"), make_vm("test-1", cpus=2, used=100000)]
    code, out = run_mode(monkeypatch, capsys, vms, allowed=["^prod"])
    assert "test-1" not in out


def test_invalid_regex_typo(monkeypatch, capsys):
    with pytest.raises((re.error, SystemExit)):
        run_mode(monkeypatch, capsys, [make_vm()], allowed=["[unclosed"])


# ------------------------------------------------------ output format
def test_no_pipe_in_plugin_text(monkeypatch, capsys):
    # '|' starts the perfdata section for Nagios/Naemon/Icinga
    _, out = run_mode(monkeypatch, capsys, [make_vm()])
    assert "|" not in out.split("\n")[0]
