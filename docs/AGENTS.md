# Documentation

## Purpose

- Explain Depfix to users, operators, contributors, and runtime implementers beyond the README quick path.

## Ownership

- This tree owns API, CLI, architecture, resolution, realm, deployment, security, migration, troubleshooting, verification, and research documentation.

## Local Contracts

- Describe shipped behavior in the present tense and label proposals or experiments explicitly.
- Keep commands executable from the repository root unless a document says otherwise.
- Link to the canonical contract rather than duplicating long normative details.

## Work Guidance

- Organize durable product concepts into named folders with their own DOX contracts.
- Keep the root README concise; move deep operational and internal explanations here.

## Verification

- Check relative links and run the commands a changed guide promises when practical.

## Child DOX Index

- [`concepts/AGENTS.md`](concepts/AGENTS.md) — core technical concepts, each in an independent folder.
- [`guides/AGENTS.md`](guides/AGENTS.md) — task-oriented onboarding and troubleshooting.
- [`operations/AGENTS.md`](operations/AGENTS.md) — security and verification procedures.
- [`project/AGENTS.md`](project/AGENTS.md) — implementation records and historical baselines.
- [`reference/AGENTS.md`](reference/AGENTS.md) — exact Python and CLI interfaces.
- [`research/AGENTS.md`](research/AGENTS.md) — explicitly non-shipping design research.

`README.md` is the human documentation index and remains owned by this parent.
