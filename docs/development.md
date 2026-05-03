# Development

This page is for working on `personal-slack-agent` from a repo checkout.
For normal user setup, start with [docs/setup.md](setup.md).

## Editable Install

Use an editable install when changing the package source:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[dev]'
```

The editable install exposes the local CLI entry points from the checkout:

```bash
.venv/bin/bob
.venv/bin/bob-agent
.venv/bin/bob-init
.venv/bin/bobctl
```

## Testing

Run the full test suite:

```bash
.venv/bin/python -m pytest -q
```

For package-index release testing, including TestPyPI install commands, see
[docs/publishing.md](publishing.md).
