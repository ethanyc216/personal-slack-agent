# Publishing

This page captures the current release and package-publishing setup for
`personal-slack-agent`.

## Current State

- GitHub CI is configured in `.github/workflows/ci.yml`.
- Pushes to `main` run the test matrix, generate a package version, create a
  Git tag, create a GitHub Release, and upload wheel plus source distribution
  artifacts.
- After the GitHub Release job succeeds, `.github/workflows/pypi.yml` is wired
  to publish the same wheel and source distribution artifacts to PyPI from the
  `pypi` environment.
- GitHub Release versions are generated from the base `pyproject.toml` version
  and the CI workflow run number. For example, base version `0.1.0` can produce
  release `v0.1.7`.
- TestPyPI publishing is configured in `.github/workflows/testpypi.yml`.
- TestPyPI publishing is manual-only through `workflow_dispatch`.
- PyPI publishing is configured in `.github/workflows/pypi.yml` for automatic
  main-branch releases and for manual fallback publishing by tag.
- Real PyPI publishing requires account-side PyPI Trusted Publisher setup before
  the PyPI workflow can publish successfully.

The committed `pyproject.toml` version remains the base version. The workflows
temporarily rewrite that version inside GitHub Actions before building package
artifacts.

## Public Exposure

Treat GitHub Releases, TestPyPI, and PyPI as public distribution channels.

For this pure-Python project, published wheels include readable `.py` source
files. Source distributions include a fuller source archive. Do not publish to
TestPyPI or PyPI until the package contents are acceptable for public download.

The current built artifacts include runtime package source and the packaged
AppleScript resource. They do not include `docs/` or `docs/superpowers/`.

## Version Alignment

The GitHub Release workflow and the TestPyPI workflow currently generate
versions independently unless a TestPyPI version is supplied manually.

For a smoke test where exact version alignment does not matter, run
`Publish to TestPyPI` and leave the `version` input blank. The workflow will
publish `0.1.<TestPyPI workflow run number>`.

For a TestPyPI publish that should match a GitHub Release, run
`Publish to TestPyPI` with the exact package version, without the leading `v`.
For example, release tag `v0.1.7` should be published to TestPyPI with:

```text
0.1.7
```

Package indexes do not allow overwriting an already-uploaded version. If a
publish succeeds with the wrong version, leave that version in place and publish
a newer or matching unused version.

## PyPI Trusted Publisher Setup

For automatic PyPI publishing from generated GitHub Releases and manual fallback
publishing by tag, create a PyPI Trusted Publisher using:

```text
Project name: personal-slack-agent
Owner: ethanyc216
Repository name: personal-slack-agent
Workflow name: pypi.yml
Environment name: pypi
```

Also create the `pypi` GitHub environment in the repository settings. Consider
adding environment protection rules if you want a human approval gate before
the public PyPI upload runs.

Package indexes do not allow overwriting an already-uploaded version. If a
publish succeeds with the wrong version, leave that version in place and publish
a newer unused version.

## Publishing Options

### 1. Manual TestPyPI Only

Keep GitHub Releases automatic and TestPyPI manual.

Flow:

```text
main push -> CI -> GitHub Release
manual workflow -> TestPyPI publish
no workflow -> PyPI publish
```

This was the safest setup while the project was still only being tested because
no package was uploaded to TestPyPI or PyPI without an explicit manual action.

### 2. Automatic TestPyPI From GitHub Releases

Publish each generated GitHub Release to TestPyPI automatically.

Flow:

```text
main push -> CI -> GitHub Release -> TestPyPI publish
no workflow -> PyPI publish
```

This keeps TestPyPI aligned with GitHub Releases and exercises the same package
upload path on every release. It still avoids real PyPI.

This would require changing the TestPyPI workflow trigger/versioning so it uses
the generated release version rather than the TestPyPI workflow run number.

### 3. Manual PyPI Promotion

Publish to TestPyPI first, verify install behavior, then manually promote the
same version to real PyPI.

Flow:

```text
main push -> CI -> GitHub Release
manual workflow -> TestPyPI publish
manual workflow -> PyPI publish
```

The repo includes `.github/workflows/pypi.yml` for this fallback path. When
running the workflow, enter the exact GitHub Release tag to publish. For
example:

```text
v0.1.8
```

The workflow downloads the wheel and source distribution from that GitHub
Release and publishes those files to PyPI. It does not rebuild from the current
branch.

### 4. Automatic PyPI From GitHub Releases

Publish every generated GitHub Release directly to real PyPI.

Flow:

```text
main push -> CI -> GitHub Release -> PyPI publish
```

This is the current configured path once PyPI Trusted Publisher setup is in
place. It is convenient because every successful main-branch release becomes
publicly installable from PyPI, and the PyPI version is the same version
generated for the GitHub Release.

The automatic job downloads the distribution files attached to the GitHub
Release. For example, if CI creates GitHub Release `v0.1.9`, the PyPI upload
uses the release files for package version `0.1.9`.

### 5. Private Distribution Instead Of PyPI

If the source code or runtime behavior should not be public, do not publish to
TestPyPI or PyPI. Use private GitHub Releases, a private package index, or an
internal artifact repository instead.

## README Badges And Links

The README uses a dynamic GitHub Actions badge for CI status. That badge updates
when GitHub renders it, so the README does not need a commit just to refresh the
displayed build status.

The README links to `https://github.com/ethanyc216/personal-slack-agent/releases/latest`
instead of hard-coding a release version. That URL resolves to the latest GitHub
Release without a README change.

Do not use a Shields.io GitHub release-version badge while the repository is
private unless you have verified that the badge can read the repository. For
private repositories, Shields may render `repo not found` because it cannot see
the release metadata.

Regular README prose is static. If a literal version number is written in the
README text, it will only change when a commit changes that text.
