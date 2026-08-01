# W13/W14 adversarial verification

## Scope

- Tester-only verification at `main` `fa279ea`; application code is out of scope.
- Exercise W14 process-wide signed-URL redaction across standard-library logging, traceback,
  `extra` values, provider signatures, and a worker process.
- Exercise W13 `find_fabrication` against Turkish orthographic, Unicode, line-break, and case
  variants; record deliberate false positives.

## Expected documentation changes

- `docs/handoffs/W14-verification-followups-2.md`
- `docs/handoffs/W13-script-generation.md`

## Verification

1. Start the isolated compose stack with `COMPOSE_PROJECT_NAME=sp-codex` and obtain runtime/tool
   versions.
2. Run read-only/ad-hoc adversarial probes and the focused existing suites; do not add tests or
   change production code.
3. Append reproducible findings and a final decision to each handoff's Doğrulama section.
