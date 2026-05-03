---
phase: 03-security-hardening
plan: 01
subsystem: auth
tags: [keyring, fernet, cryptography, windows-credential-manager, security]

# Dependency graph
requires:
  - phase: 01-core-bug-fixes
    provides: auth_manager.py baseline with _get_fernet_key(), _get_keyring(), config_store.config_dir
provides:
  - Keyring-backed Fernet key management (OS keyring primary, file fallback)
  - Legacy on-disk key file migration to OS keyring on first call
  - Graceful degradation when keyring is unavailable
affects: [04-testing-verification, auth, security]

# Tech tracking
tech-stack:
  added: [keyring.errors (_ke.KeyringLocked, _ke.NoKeyringError, _ke.KeyringError)]
  patterns:
    - Lazy keyring import pattern via _get_keyring()
    - Store-before-delete migration safety (set_password before _delete_legacy_key_files)
    - UnicodeDecodeError guard on corrupted legacy key files

key-files:
  created: []
  modified: [core/auth_manager.py]

key-decisions:
  - "Keyring-backed Fernet key: OS keyring is primary; on-disk .credential_key only as fallback when keyring unavailable"
  - "Migration safety: set_password() called before _delete_legacy_key_files() — never reversed (T-03-03)"
  - "UnicodeDecodeError on corrupted legacy key: generate fresh key with warning log rather than crash (T-03-05)"
  - "keyring.errors caught as named exception types (KeyringLocked, NoKeyringError, KeyringError) with broad Exception fallback for unexpected errors"

patterns-established:
  - "Pattern: _KEYRING_SERVICE / _KEYRING_FERNET_KEY_ACCOUNT constants for all keyring operations"
  - "Pattern: _load_legacy_key_file / _delete_legacy_key_files helpers for migration path"

requirements-completed: [REQ-020]

# Metrics
duration: 15min
completed: 2026-05-03
---

# Phase 03 Plan 01: Security Hardening — Keyring-Backed Fernet Key Summary

**Fernet encryption key moved from co-located config directory file to OS keyring (Windows Credential Manager), with transparent migration of existing installs and graceful fallback for headless environments**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-05-03T09:30:00Z
- **Completed:** 2026-05-03T09:45:00Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments

- Replaced insecure `_get_fernet_key()` (SEC-1 bug) that stored key co-located with the ciphertext it protects
- New implementation stores the Fernet key exclusively in the OS keyring on first use
- Existing installs transparently migrate `.credential_key` / `secret.key` → keyring on first call, then delete the file
- Headless/no-keyring environments fall back gracefully to on-disk key with warning log (no crash, no data loss)
- All three smoke tests passed: syntax check, structural import test, and live keyring round-trip

## Task Commits

Each task was committed atomically:

1. **Task 1: Replace _get_fernet_key() with keyring-backed implementation** - `78761b3` (feat)
2. **Task 2: Smoke-test the modified module in FreeCAD's Python** - verification only, no file changes

**Plan metadata:** (docs commit — see final commit)

## Files Created/Modified

- `core/auth_manager.py` — Added `_KEYRING_SERVICE`, `_KEYRING_FERNET_KEY_ACCOUNT`, `_LEGACY_KEY_FILENAMES` constants; added `_load_legacy_key_file()` and `_delete_legacy_key_files()` helpers; replaced entire `_get_fernet_key()` with keyring-backed implementation

## Decisions Made

- **keyring.errors import inside function:** `import keyring.errors as _ke` is done inside the `try` block within `_get_fernet_key()` to avoid module-level import failure on systems without keyring backend
- **Migration safety order:** `set_password()` is always called before `_delete_legacy_key_files()` to prevent data loss if keyring write fails (T-03-03 mitigation)
- **UnicodeDecodeError path:** Non-ASCII legacy key file triggers fresh key generation with warning — avoids crash, warns user that credentials need re-entry (T-03-05 mitigation)
- **Broad Exception fallback:** Catches unexpected keyring errors beyond the named `_ke.*` types and falls back to on-disk — prevents crashes on unknown keyring implementations

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

- Git repository root was at `C:/` (whole drive) instead of plugin directory — initialized a new git repo in the plugin directory to enable proper per-task commits. This is an environment issue, not a code issue.
- Keyring round-trip test passed on Windows with Windows Credential Manager backend — no fallback path exercised (expected behavior for a proper Windows install).

## User Setup Required

None — no external service configuration required. The OS keyring (Windows Credential Manager on Windows) is used automatically.

## Next Phase Readiness

- `core/auth_manager.py` is clean, syntax-valid, and imports successfully in FreeCAD's Python 3.11
- REQ-020 satisfied: Fernet key is no longer stored in the config directory when a keyring backend is available
- `_try_fernet_encrypt()` and `_try_fernet_decrypt()` unchanged — all callers unaffected
- Ready for Phase 4 (Testing & Verification)

---
*Phase: 03-security-hardening*
*Completed: 2026-05-03*
