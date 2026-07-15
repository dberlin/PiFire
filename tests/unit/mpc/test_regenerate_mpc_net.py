import os
import sys

# tools/ isn't a package (no __init__.py); resolve it relative to this file
# rather than the fragile cwd-relative 'tools' insert this replaced.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "tools"))
import regenerate_mpc_net as rg


def test_export_cmd_fan_on_uses_fan_paths_and_flag():
    cmd = rg.export_cmd("py", True)
    assert "--enable-fan" in cmd
    assert any(a.endswith("pifire_span_fan.npz") for a in cmd)
    assert any(a.endswith("mpc_policy_net_fan.npz") for a in cmd)


def test_export_cmd_fan_off_uses_base_paths_no_flag():
    cmd = rg.export_cmd("py", False)
    assert "--enable-fan" not in cmd
    assert any(a.endswith("pifire_span.npz") and not a.endswith("_fan.npz") for a in cmd)
    assert any(a.endswith("mpc_policy_net.npz") and not a.endswith("_fan.npz") for a in cmd)


def test_sample_cmd_carries_episodes_and_fan_flag():
    on = rg.sample_cmd("py", True, 500, None)
    assert "--enable-fan" in on and "500" in on and "--mode" in on and "span" in on
    off = rg.sample_cmd("py", False, 300, 8)
    assert "--enable-fan" not in off and "300" in off and "8" in off


def test_plan_commands_both_orders_sample_before_export_per_mode():
    cmds = rg.plan_commands([False, True], episodes=500, workers=None, skip_sample=False)
    # 4 commands: sample-off, export-off, sample-on, export-on
    assert len(cmds) == 4
    assert "sample_mpc.py" in " ".join(cmds[0]) and "export_span_net.py" in " ".join(cmds[1])


def test_plan_commands_skip_sample_omits_sampling():
    cmds = rg.plan_commands([True], episodes=500, workers=None, skip_sample=True)
    assert len(cmds) == 1 and "export_span_net.py" in " ".join(cmds[0])


def test_plan_commands_interpreter_is_injectable():
    # py is threaded through to every command so the builder is fully injectable
    cmds = rg.plan_commands([False, True], episodes=500, workers=None, skip_sample=False, py="DUMMYPY")
    assert all(cmd[0] == "DUMMYPY" for cmd in cmds)
