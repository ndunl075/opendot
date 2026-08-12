## Summary

<!-- What changed and why. If this completes or starts a build-slice item
     from ARCHITECTURE.md section 10, say which one. -->

## Architecture

<!-- Does this change or contradict anything ARCHITECTURE.md says? If so,
     this PR updates the relevant section/decision too, with a one-line
     reason. If nothing changes here, delete this section. -->

## Test plan

<!-- `python -m pytest -q` output (pass count), plus any manual verification
     for anything that isn't covered by an automated test (e.g. a script
     that touches the filesystem or Task Scheduler). -->

## Safety checklist

<!-- Delete any line that doesn't apply. -->

- [ ] New consequential writes follow propose → approve → execute, never a
      single step (see CONTRIBUTING.md's "Code shape").
- [ ] New writes are idempotent and, where the provider allows it, recover
      across a crash between the provider accepting the write and Alfred
      recording its receipt, failing closed on an absent/ambiguous result.
- [ ] New intake channels check a locally configured allowlist
      (default-deny) before turning a message into a local write.
- [ ] No secret, token, or raw personal record is logged, committed, or
      returned outside its declared MCP client scope.
- [ ] Every consequential action is audited in the same transaction as the
      action itself.
