from pathlib import Path

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
