from pathlib import Path
import shutil as stdlib_shutil

import pytest

from personal_slack_agent import chrome_launcher
from personal_slack_agent.chrome_launcher import (
    default_launcher_app_path,
    default_launcher_profile_path,
    render_launcher_applescript,
)


def test_default_launcher_paths_use_home_scoped_locations(tmp_path):
    assert default_launcher_app_path(home=tmp_path) == tmp_path / "Applications" / "Bob Chrome.app"
    assert default_launcher_profile_path(home=tmp_path) == (
        tmp_path / ".cache" / "personal-slack-agent" / "chrome-profile"
    )


def test_render_launcher_applescript_includes_probe_launch_and_profile(tmp_path):
    script = render_launcher_applescript(home=tmp_path)

    assert "http://127.0.0.1:9222/json/version" in script
    assert "--remote-debugging-port=9222" in script
    assert 'open -a \\"Google Chrome\\"' in script
    assert "__DEBUG_PROBE_URL__" not in script
    assert "__DEBUG_PORT__" not in script
    assert "__PROFILE_DIR__" not in script
    assert str(tmp_path / ".cache" / "personal-slack-agent" / "chrome-profile") in script


def test_render_launcher_applescript_escapes_quote_in_home_path():
    home = Path('/tmp/bob-home-"quoted"')

    script = render_launcher_applescript(home=home)

    assert '/tmp/bob-home-\\"quoted\\"/.cache/personal-slack-agent/chrome-profile' in script


def test_install_chrome_launcher_compiles_app_bundle(tmp_path, monkeypatch):
    calls = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, check, capture_output, text):
        calls["cmd"] = list(cmd)
        calls["source_text"] = Path(cmd[3]).read_text(encoding="utf-8")
        compiled_app = Path(cmd[2])
        (compiled_app / "Contents").mkdir(parents=True)
        (compiled_app / "Contents" / "Info.plist").write_text("compiled", encoding="utf-8")
        return Result()

    monkeypatch.setattr(chrome_launcher.subprocess, "run", fake_run)

    installed = chrome_launcher.install_chrome_launcher(
        output_app=tmp_path / "Applications" / "Bob Chrome.app",
        home=tmp_path,
    )

    assert installed == tmp_path / "Applications" / "Bob Chrome.app"
    assert calls["cmd"][:2] == ["osacompile", "-o"]
    assert Path(calls["cmd"][2]).name == "Bob Chrome.app"
    assert Path(calls["cmd"][2]).parent != tmp_path / "Applications"
    assert "--remote-debugging-port=9222" in calls["source_text"]
    assert installed.exists()


def test_install_chrome_launcher_rejects_existing_app_without_force(tmp_path):
    target = tmp_path / "Applications" / "Bob Chrome.app"
    target.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="already exists"):
        chrome_launcher.install_chrome_launcher(output_app=target, home=tmp_path)


def test_install_chrome_launcher_force_keeps_existing_bundle_when_compile_fails(tmp_path, monkeypatch):
    target = tmp_path / "Applications" / "Bob Chrome.app"
    marker = target / "Contents" / "marker.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("keep me", encoding="utf-8")

    class Result:
        returncode = 1
        stdout = ""
        stderr = "compile failed"

    monkeypatch.setattr(chrome_launcher.subprocess, "run", lambda *args, **kwargs: Result())

    with pytest.raises(RuntimeError, match="Failed to compile launcher app: compile failed"):
        chrome_launcher.install_chrome_launcher(output_app=target, force=True, home=tmp_path)

    assert target.exists()
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "keep me"


def test_install_chrome_launcher_wraps_osacompile_oserror(tmp_path, monkeypatch):
    def fail_run(*args, **kwargs):
        raise FileNotFoundError("osacompile missing")

    monkeypatch.setattr(chrome_launcher.subprocess, "run", fail_run)

    with pytest.raises(RuntimeError, match="osacompile missing"):
        chrome_launcher.install_chrome_launcher(
            output_app=tmp_path / "Applications" / "Bob Chrome.app",
            home=tmp_path,
        )


def test_install_chrome_launcher_restores_existing_bundle_when_final_move_fails(tmp_path, monkeypatch):
    target = tmp_path / "Applications" / "Bob Chrome.app"
    marker = target / "Contents" / "marker.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("keep me", encoding="utf-8")
    calls = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, check, capture_output, text):
        compiled_app = Path(cmd[2])
        calls["compiled_app"] = compiled_app
        (compiled_app / "Contents").mkdir(parents=True)
        (compiled_app / "Contents" / "Info.plist").write_text("compiled", encoding="utf-8")
        return Result()

    real_move = stdlib_shutil.move
    failed_once = {"value": False}

    def fake_move(src, dst, *args, **kwargs):
        src_path = Path(src)
        dst_path = Path(dst)
        if (
            src_path == calls.get("compiled_app")
            and dst_path == target
            and not failed_once["value"]
        ):
            failed_once["value"] = True
            raise OSError("final move failed")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(chrome_launcher.subprocess, "run", fake_run)
    monkeypatch.setattr(chrome_launcher.shutil, "move", fake_move)

    with pytest.raises(RuntimeError, match="final move failed"):
        chrome_launcher.install_chrome_launcher(output_app=target, force=True, home=tmp_path)

    assert target.exists()
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "keep me"


def test_install_chrome_launcher_restores_existing_bundle_when_partial_target_exists(tmp_path, monkeypatch):
    target = tmp_path / "Applications" / "Bob Chrome.app"
    marker = target / "Contents" / "marker.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("keep me", encoding="utf-8")
    calls = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, check, capture_output, text):
        compiled_app = Path(cmd[2])
        calls["compiled_app"] = compiled_app
        (compiled_app / "Contents").mkdir(parents=True)
        (compiled_app / "Contents" / "Info.plist").write_text("compiled", encoding="utf-8")
        return Result()

    real_move = stdlib_shutil.move
    failed_once = {"value": False}

    def fake_move(src, dst, *args, **kwargs):
        src_path = Path(src)
        dst_path = Path(dst)
        if (
            src_path == calls.get("compiled_app")
            and dst_path == target
            and not failed_once["value"]
        ):
            failed_once["value"] = True
            (target / "Contents").mkdir(parents=True, exist_ok=True)
            (target / "Contents" / "partial.txt").write_text("partial", encoding="utf-8")
            raise OSError("final move failed")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(chrome_launcher.subprocess, "run", fake_run)
    monkeypatch.setattr(chrome_launcher.shutil, "move", fake_move)

    with pytest.raises(RuntimeError, match="final move failed"):
        chrome_launcher.install_chrome_launcher(output_app=target, force=True, home=tmp_path)

    assert target.exists()
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "keep me"
    assert not (target / "Contents" / "partial.txt").exists()


def test_install_chrome_launcher_leaves_backup_when_restore_fails(tmp_path, monkeypatch):
    target = tmp_path / "Applications" / "Bob Chrome.app"
    marker = target / "Contents" / "marker.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("keep me", encoding="utf-8")
    backup = target.parent / "{0}.backup".format(target.name)
    calls = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, check, capture_output, text):
        compiled_app = Path(cmd[2])
        calls["compiled_app"] = compiled_app
        (compiled_app / "Contents").mkdir(parents=True)
        (compiled_app / "Contents" / "Info.plist").write_text("compiled", encoding="utf-8")
        return Result()

    real_move = stdlib_shutil.move

    def fake_move(src, dst, *args, **kwargs):
        src_path = Path(src)
        dst_path = Path(dst)
        if src_path == calls.get("compiled_app") and dst_path == target:
            raise OSError("install move failed")
        if src_path == backup and dst_path == target:
            raise OSError("restore move failed")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(chrome_launcher.subprocess, "run", fake_run)
    monkeypatch.setattr(chrome_launcher.shutil, "move", fake_move)

    with pytest.raises(RuntimeError) as excinfo:
        chrome_launcher.install_chrome_launcher(output_app=target, force=True, home=tmp_path)

    assert "restore failed" in str(excinfo.value)
    assert str(backup) in str(excinfo.value)
    assert backup.exists()
    assert (backup / "Contents" / "marker.txt").read_text(encoding="utf-8") == "keep me"
