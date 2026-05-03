---
phase: 03-security-hardening
verified: 2026-05-03T11:30:00Z
status: passed
score: 4/4 must-haves verified
re_verification: false
---

# Phase 03: Security Hardening Verification Report

**Phase Goal:** Move the Fernet encryption key out of the data directory (currently stored alongside the ciphertext), implement keyring-based key storage, and add any remaining security improvements (fix SEC-1).
**Verified:** 2026-05-03T11:30:00Z
**Status:** ✅ PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Fernet key is loaded from the OS keyring, not from a file in the config directory | ✓ VERIFIED | `_get_fernet_key()` calls `kr.get_password(_KEYRING_SERVICE, _KEYRING_FERNET_KEY_ACCOUNT)` as primary path; file is only a fallback when keyring unavailable |
| 2 | On first run after upgrade, an existing `.credential_key` or `secret.key` file is migrated into the keyring and then deleted | ✓ VERIFIED | `_load_legacy_key_file()` + `set_password()` + `_delete_legacy_key_files()` migration path confirmed in lines 75–102; delete always after `set_password` |
| 3 | When keyring is unavailable, plugin falls back gracefully to on-disk key file with a warning logged — does NOT crash | ✓ VERIFIED | `except (_ke.KeyringLocked, _ke.NoKeyringError, _ke.KeyringError)` catches named errors; broad `except Exception` catches unexpected errors; both log and fall through to file fallback at line 127–141 |
| 4 | The legacy `_get_fernet_key()` SEC-1 comment is removed; implementation fully replaced | ✓ VERIFIED | grep for `"the key file resides in the same directory"` returns no matches; new implementation present at lines 52–141 |

**Score:** 4/4 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `core/auth_manager.py` | Keyring-backed Fernet key management; contains `_KEYRING_FERNET_KEY_ACCOUNT` | ✓ VERIFIED | Constant present at line 13; `_get_fernet_key()` uses it at lines 68, 80, 92, 107 |
| `core/auth_manager.py` | Legacy key file migration helpers; contains `_load_legacy_key_file` | ✓ VERIFIED | Function defined at lines 26–36; called in migration path |
| `core/auth_manager.py` | Legacy key file cleanup helper; contains `_delete_legacy_key_files` | ✓ VERIFIED | Function defined at lines 39–49; called only after `set_password` |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `_get_fernet_key()` | `keyring.get_password / keyring.set_password` | `_get_keyring()` lazy import + `_KEYRING_SERVICE` / `_KEYRING_FERNET_KEY_ACCOUNT` constants | ✓ WIRED | `kr.get_password(_KEYRING_SERVICE, _KEYRING_FERNET_KEY_ACCOUNT)` at line 68; `kr.set_password(...)` at lines 79, 91, 106 |
| `_get_fernet_key()` migration path | `_delete_legacy_key_files()` | Called only AFTER successful `set_password` | ✓ WIRED | Migration safety ordering verified programmatically: all `_delete_legacy_key_files` calls at chars 1875 and 2055 follow `set_password` at char 1703 within function body; char 739 is the already-in-keyring cleanup branch (safe — key pre-confirmed in keyring) |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| `_get_fernet_key()` | `stored` (keyring key) | `kr.get_password()` → Windows Credential Manager | ✓ Yes — OS keyring read; returns bytes on hit | ✓ FLOWING |
| `_try_fernet_encrypt()` / `_try_fernet_decrypt()` | `key` | `_get_fernet_key(config_dir)` | ✓ Yes — both still call `_get_fernet_key` unchanged | ✓ FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `py_compile` (syntax valid) | `python.exe -m py_compile core/auth_manager.py` | Exit 0, no output | ✓ PASS |
| Structural smoke test (constants + callables + `_load_legacy_key_file` on empty dir) | `python.exe -c "...smoke-test..."` | `smoke-test PASSED` | ✓ PASS |
| All required patterns in source | AST/string checks via Python | All 9 patterns: OK | ✓ PASS |
| SEC-1 legacy comment removed | grep `"the key file resides in the same directory"` | No match | ✓ PASS |
| Migration safety ordering | Position analysis of `set_password` vs `_delete_legacy_key_files` in function body | All delete calls after prior `set_password` | ✓ PASS |
| `_try_fernet_encrypt` / `_try_fernet_decrypt` callers unchanged | Regex extraction + `_get_fernet_key` search in each function | Both confirmed | ✓ PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| REQ-020 | 03-01-PLAN.md | Fernet key must be stored in system keyring, not in config directory | ✓ SATISFIED | `_get_fernet_key()` uses OS keyring as primary; on-disk only as fallback when keyring unavailable; migration deletes legacy file after successful `set_password` |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `core/auth_manager.py` | 227–229 | SEC-1 comment block in `save_credentials()` (lines 227–230) still references `SEC-1 note: the Fernet key is stored in the same directory` | ℹ️ Info | This comment is in `AuthManager.save_credentials()`, describing the *fallback* code path when neither keyring nor cryptography is available — it is **descriptively accurate** for that fallback path and is NOT a code bug. The SEC-1 fix was for `_get_fernet_key()` which is now fixed. This comment documents the remaining theoretical risk in the Fernet fallback path and should be considered for cleanup in a future pass but does NOT block goal achievement. |

No blocker or warning anti-patterns found.

---

### Human Verification Required

*(None — all must-haves are fully verifiable programmatically.)*

---

## Gaps Summary

No gaps. All 4 observable truths verified, all 3 required artifacts confirmed substantive and wired, all 2 key links confirmed, REQ-020 satisfied. The phase goal — hardening credential storage by replacing insecure on-disk Fernet key with OS keyring-backed storage — is fully achieved.

**One informational note:** A `# SEC-1 note:` comment block remains in `AuthManager.save_credentials()` (lines 227–230). This comment accurately describes the Fernet fallback path (when keyring is unavailable at the *credential* level — distinct from the Fernet *key* level fixed here). It is not a bug and does not indicate incomplete work, but may warrant cleanup or rewording in a future documentation pass.

---

_Verified: 2026-05-03T11:30:00Z_
_Verifier: OpenCode (gsd-verifier)_
