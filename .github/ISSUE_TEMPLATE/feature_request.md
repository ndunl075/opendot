---
name: Feature request
about: Propose new behavior or a new connector
title: ""
labels: enhancement
---

**Where does this fit in ARCHITECTURE.md?**
Which build slice (section 10) or connector-order phase (section 9) is this
part of? If it doesn't fit anywhere yet, say so — it may need an architecture
decision (section 1) before any code.

**What should Alfred do**

**Why now**
What's blocked or worse without it. If it depends on something only the
owner can supply (a credential, a plan tier, a config choice — see section
12's open questions), say what.

**Safety shape**
For anything that reads or writes through a connector: is this a read, an
automatic low-risk write, or a consequential write that needs
propose/approve/execute (section 8)? A feature request that skips this
question usually means the write path hasn't been thought through yet.
