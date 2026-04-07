# Setup Guide

This guide walks through a first-time setup of Bob on a new machine.

## 1. Install the package

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[dev]'
```

## 2. Generate starter config

```bash
bob-init
```

This creates:

```text
~/.config/personal-slack-agent/bob.toml
```

You can also start from the committed sample template:

```text
config/bob.sample.toml
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

## 4. Fill in your config

At minimum:

- `allowed_actor_ids`
- `slack_url`
- one `[[workspaces.channels]]` `name`
- `default_cwd`

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

Check it:

```bash
bobctl status
bobctl tail-log --lines 50
```

## 7. Test from Slack

In your configured channel, send:

```text
Bob, test
```

Bob should create a thread and reply with:

- working: `_*Bob is working on it :arrows_counterclockwise::*_ ...`
- done: `_*codex Bob :white_check_mark::*_ ...`

## Notes

- `slack_api_token` is sensitive. Do not commit your personal config.
- Bob currently uses private browser-session-backed Slack web APIs, not a public Slack app install flow.
