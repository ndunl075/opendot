# Security Policy

Alfred runs entirely on its owner's own machine and holds real credentials
(Google, GitHub, Telegram, Slack) plus a personal memory archive. A security
bug here is not "an attacker defaces a page" — it's "an attacker reads or
sends on the owner's behalf," so please report privately rather than opening
a public issue.

## Reporting a vulnerability

Use GitHub's [private vulnerability
reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
for this repository ("Security" tab → "Report a vulnerability"). Include:

- What you found and where (file/function, or the CLI/MCP command that
  triggers it).
- The impact: what an attacker could read, change, or send, and what access
  they'd need first (e.g. "any MCP client" vs. "an already-paired Telegram
  user" vs. "local filesystem access").
- Steps to reproduce, or a minimal patch/test that demonstrates it.

This is a solo-maintained personal project, not a company with an SLA.
Expect an acknowledgment within a few days and a fix or mitigation on a
best-effort basis, prioritized by the impact above.

## Scope

In scope:

- Anything that lets a consequential action (a send, a create, a delete, a
  restore) happen without a fresh, correctly-scoped approval — see
  section 8 of [ARCHITECTURE.md](ARCHITECTURE.md) for what "consequential"
  is supposed to mean here.
- Anything that lets one paired identity (a Telegram user, a Slack user, an
  email sender) act as another, or act without being paired at all.
- A credential, token, or raw personal record ending up somewhere it
  shouldn't: a log line, an audit record, the Obsidian vault, or a committed
  file.
- A retry or crash-recovery path that can create a duplicate action, or one
  whose outcome is genuinely ambiguous but doesn't fail closed.
- An MCP tool that returns data or performs an action outside its declared
  client scope.

Out of scope:

- Anything that requires the reporter to already control the machine Alfred
  runs on, its OS credential store, or its owner's actual Google/GitHub/
  Telegram/Slack account — at that point the local machine is the trust
  boundary, not Alfred.
- Denial of service against a single local process the owner controls.
- Findings in Hermes, Obsidian, or any other upstream dependency — report
  those to the upstream project. (See decision 1 in ARCHITECTURE.md: Alfred
  does not currently fork or vendor Hermes.)
