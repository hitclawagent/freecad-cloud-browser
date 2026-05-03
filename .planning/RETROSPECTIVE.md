# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

---

## Milestone: v1.0 — Stable & Polished Plugin

**Shipped:** 2026-05-03
**Phases:** 4 | **Plans:** 4 (GSD-tracked: 2 phases, 4 plans) | **Sessions:** 1

### What Was Built

- **Core stabilization (Phase 1):** Fixed critical bugs across all 6 providers and 3 core modules — provider abstraction (@property pattern), thread safety, Dropbox chunked upload cursor, WebDAV path traversal fix, deepcopy isolation in config_store
- **UI polish (Phase 2):** QPalette-based colors for theme compatibility, readable button labels, QStackedWidget empty states
- **Security hardening (Phase 3):** SEC-1 fixed — Fernet encryption key moved to OS keyring (Windows Credential Manager) with transparent migration of existing installs and headless fallback
- **Test suite (Phase 4):** 43 tests from zero — 31 unit tests (config_store, file_cache, auth_manager) + 12 provider smoke tests (all 6 providers × 2 operations), all passing in <1s

### What Worked

- **GSD wave execution on Phase 4:** Parallel Wave 1 (04-01 + 04-02) then sequential Wave 2 (04-03) was a clean dependency model — conftest.py fixtures were ready for provider tests
- **`_get_keyring()` lazy import pattern:** Discovered during testing that a function-level import enables clean `patch("core.auth_manager._get_keyring")` mocking — made test tier isolation elegant
- **Verification scores:** 4/4 (Phase 3) and 7/7 (Phase 4) on first verification pass — no re-verification cycles needed
- **Integration checker:** Caught that `_get_fernet_key()` keyring primary path was never tested despite Phase 3 verifying the code — prevented a silent regression gap from shipping unnoticed as debt

### What Was Inefficient

- **Phases 1 & 2 pre-GSD:** These phases were completed before GSD tracking, so no PLAN/SUMMARY/VERIFICATION artifacts exist — audit had to treat them as "manual validation only". Future work should be tracked from the start
- **SUMMARY.md one_liner field inconsistency:** Phase 4 SUMMARY files had missing `one_liner` frontmatter fields (the tool returned `"One-liner:"` as raw text), requiring manual extraction. Executor agents should enforce this field
- **STATE.md format drift:** gsd-tools emitted warnings about missing STATE.md fields (`Current Phase`, `Current Plan`, etc.) throughout execution — indicates STATE.md body format diverged from what gsd-tools expects

### Patterns Established

- **`_get_keyring()` lazy import for testability:** Any module that wraps an optional external library should expose it through a getter function, not a module-level import, to enable clean mock injection
- **`_is_sensitive` as static method:** Sensitivity classification helpers belong on the class, not at module level — enables cleaner imports in tests
- **OneDrive mock patch target:** `requests.get` patched at `requests.get` (not `providers.onedrive.requests.get`) because OneDrive imports `requests` locally inside methods — document this per-provider for future test authors

### Key Lessons

1. **Ship test coverage for security fixes in the same milestone.** REQ-020 (keyring) was verified by code inspection but the keyring primary path has no regression test. If `_get_fernet_key()` is modified in v1.1, there's no automated safety net. Rule: security-critical code paths need dedicated regression tests, not just code inspection.
2. **UI testing requires its own phase setup.** REQ-010/011/012 have no automated tests because pytest-qt wasn't configured. Trying to bolt on UI testing after the fact is expensive. If a milestone includes UI requirements, plan a `pytest-qt` setup task in Phase 1.
3. **The integration checker adds real value.** It surfaced two non-obvious gaps (keyring path untested, provider_type unasserted) that phase-level verifiers missed. Worth the cost for any milestone with cross-phase dependencies.

### Cost Observations

- Model mix: ~100% sonnet (claude-sonnet-4.6 throughout)
- Sessions: 1 (all 4 phases in a single session)
- Notable: Parallel Wave 1 execution (04-01 + 04-02 dispatched sequentially to avoid .git/config.lock contention but ran independently) was effective — both plans completed without conflicts

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Sessions | Phases (GSD) | Key Change |
|-----------|----------|--------------|------------|
| v1.0 | 1 | 2 of 4 | First GSD milestone — established phase/plan/verification loop |

### Cumulative Quality

| Milestone | Tests | Zero-Dep Additions |
|-----------|-------|--------------------|
| v1.0 | 43 | 43 (test suite from zero) |
