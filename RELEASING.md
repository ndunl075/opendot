# Releasing

Alfred has no external users yet, so this process exists to keep releases
reproducible and verifiable once it does, not to satisfy an SLA.

## What a release is

A release is an sdist + wheel of `alfred-core` at one commit, attached to a
GitHub Release, with a [SLSA build provenance
attestation](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
proving they were built by this repository's own GitHub Actions workflow from
that exact source — not hand-built and uploaded from somewhere else. This is
keyless: it rides GitHub's OIDC token through Sigstore, so there is no GPG
key or long-lived secret to generate, rotate, or lose. It answers "did this
file really come from this repo's CI," not "should you trust this code" —
read the diff for that.

Nothing is published to PyPI yet; installation stays `pip install -e .` from
a checkout (see README.md's "Local setup") until there's a reason to change
that.

## Cutting one

1. Update `version` in `pyproject.toml` ([semantic
   versioning](https://semver.org/): breaking change → major, new
   capability → minor, fix only → patch). While pre-1.0, breaking changes
   only bump the minor version.
2. Move `CHANGELOG.md`'s `## [Unreleased]` entries under a new `## [X.Y.Z] -
   YYYY-MM-DD` heading; leave `[Unreleased]` empty above it.
3. Commit, merge to `main`, then tag that commit and push the tag:
   ```powershell
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
4. Pushing the tag triggers `.github/workflows/release.yml`, which builds the
   package, runs the full test suite against the built wheel (not just the
   source tree), attests provenance, and creates the GitHub Release with both
   files attached. If the test step fails, the release does not publish —
   fix the problem, delete the tag (`git push --delete origin vX.Y.Z`), and
   start over from step 3.

## Verifying a downloaded release

```powershell
gh attestation verify <path-to-downloaded-file> --repo ndunl075/alfred
```

This confirms the exact bytes came from a `.github/workflows/release.yml` run
in this repository at the tagged commit.
