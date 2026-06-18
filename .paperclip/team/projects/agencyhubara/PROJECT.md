---
name: AgencyHubara Delivery
slug: agencyhubara
description: Rolling backlog of AgencyHubara user stories (HUs) implemented test-first under the hubara-dev harness and gated by the deterministic architecture panel.
owner: architect
---

The pod's home project. Inbound product priorities and bug reports become engineering
child issues here.

- The **architect** triages, refines each HU against the capability specs, and
  decomposes it into bounded child issues with behavioral acceptance criteria.
- The **implementer** checks out one child issue and ships it red → green → refactor.
- The **reviewer** verifies on `in_review` — deterministic panel, adversarial diff
  audit, and live-stack behavior — then approves or sends it back.

Every issue's definition-of-done is a green `/hubara-gates` panel plus, for
user-visible changes, confirmed behavior on the running Docker stack. Issues that
touch PROTECTED paths carry the `architecture-change` label and an ADR.
