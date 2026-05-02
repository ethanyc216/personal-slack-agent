# Setup Guide

This guide walks through a first-time setup of Bob on a new machine.

## 1. Install the package

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[dev]'
```

## 2. Run the setup wizard

```bash
bob-init
```

The wizard prompts for the owner identity, Slack workspace URL, channel, default working directory,
and channel memory policy. It writes a validated config to:

```text
~/.config/personal-slack-agent/bob.toml
```

You can also start from the committed sample template:

```text
config/bob.sample.toml
```

For a field-by-field explanation of the Bob config file, see:

```text
docs/bob-config-setup.md
```

For a command-by-command operator reference, see:

```text
docs/command-reference.md
```

For the architecture and Slack interaction model, see:

```text
docs/how-it-works.md
```

## 3. Start Chrome for Bob

```bash
open -na "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.cache/personal-slack-agent/chrome-profile" \
  --no-first-run \
  --no-default-browser-check \
  "https://slack.com/signin?entry_point=nav_menu#/signin"
```

In that Chrome instance:

1. open `chrome://inspect/#remote-debugging`
2. enable remote debugging
3. log into Slack
4. open the workspace/channel page you want Bob to use

### Optional Dock Launcher

If you want a reusable Dock app for the debug browser:

```bash
.venv/bin/bobctl install-chrome-launcher --force
```

That command compiles `~/Applications/Bob Chrome.app`.

At install time, Bob renders that launcher from the current `[browser]` config in `bob.toml`:

- `browser.cdp_url`
- `browser.browser_user_data_dir`
- `browser.chrome_executable_path` when set

If you use a non-default config file, pass it here too:

```bash
.venv/bin/bobctl install-chrome-launcher --config ~/.config/personal-slack-agent/bob.toml --force
```

When clicked:

- if the configured `browser.cdp_url` is already reachable, it foregrounds the configured Chrome app
- otherwise it launches a fresh debug-enabled Chrome using the configured app/profile settings
- it does not open Slack or any other URL automatically

If you later change the `[browser]` settings, rerun the same install command to refresh the installed app, including `--config ...` when you use a non-default config file.

This launcher is for the browser only. Bob itself still must be started or restarted from a normal unsandboxed shell.

## 4. Review your config

The setup wizard fills in the minimum practical config. Review or edit these fields if your Slack
workspace or channel needs a different setup:

- `owner_name`
- `owner_preferred_name`
- `allowed_actor_ids`
- `slack_url`
- one `[[workspaces.channels]]` `name`
- `default_cwd`
- `persistent_memory_mode`
- `persistent_memory_owner` for any `owner_only` channel
- `slack_channel_id` if Bob cannot resolve a channel from the rendered Slack sidebar

You can inspect the current file with:

```bash
bobctl show-config --config ~/.config/personal-slack-agent/bob.toml
```

## 5. Discover Slack API auth automatically

If you already have the Slack workspace open in the debuggable Chrome session:

```bash
bob-init --discover-slack-auth --workspace my-workspace
```

That command:

- attaches to the logged-in browser
- discovers the browser-session-backed Slack API auth
- writes `slack_api_origin` and `slack_api_token` into your config

## 6. Start Bob

```bash
bobctl start --config ~/.config/personal-slack-agent/bob.toml --poll-interval-seconds 10
```

`--poll-interval-seconds` is Bob's idle cycle / recovery interval. Normal message detection is websocket-driven; this interval mainly controls reconnect recovery cadence and stop-file responsiveness.

Check it:

```bash
bob --workspace my-workspace --channel my-private-channel "summarize this repo"
bobctl status
bobctl doctor
bobctl smoke-test --workspace my-workspace --channel my-private-channel
bobctl tail-log --lines 50
```

## 7. Test from Slack

In your configured channel, send:

```text
Bob, test
```

Bob should create a thread and reply with:

- working: `_*Bob is working on it :arrows_counterclockwise::*_ ...`
- done: `_*Bob :white_check_mark::*_ ...`

If you want terminal-originated Bob requests without specifying the channel each time, mark exactly one channel with:

```toml
persistent_memory_mode = "owner_only"
persistent_memory_owner = "bob_owner_handle"
post_terminal_threads_here = true
```

Then you can run:

```bash
bob "summarize this repo"
```

Recommended validation sequence:

1. run `bobctl doctor`
2. confirm `config_loaded: True`
3. confirm `cdp_reachable: True`
4. confirm the expected `workspace:channel` entries are listed
5. run `bobctl smoke-test --workspace my-workspace --channel my-private-channel`
6. if the test fails, inspect `bobctl tail-log --lines 100`

## Notes

- `slack_api_token` is sensitive. Do not commit your personal config.
- Keep committed examples anonymized, but set `defaults.owner_name` and `defaults.owner_preferred_name` in your local config so Bob's runtime prompt names the correct owner.
- Every configured channel must declare `persistent_memory_mode`. Use `owner_only` plus `persistent_memory_owner` for a private owner channel, or `disabled` for shared/test channels that must not update personal durable notes.
- If a configured channel is not visible in Slack's rendered sidebar for Bob's browser session, add `slack_channel_id` for that channel to seed its route directly.
- Bob currently uses Slack Web realtime sockets for detection and private browser-session-backed Slack web APIs for hydration and posting, not a public Slack app install flow.
- Per-channel message scraping through the Slack DOM is not part of the normal read path anymore. The browser is used for auth/bootstrap, channel-id discovery, and realtime websocket attachment.

## Related Docs

- [README](../README.md)
- [How Bob works](how-it-works.md)
- [Config guide](bob-config-setup.md)
- [Command reference](command-reference.md)
- [Publishing guide](publishing.md)
