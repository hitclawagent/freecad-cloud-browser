# FreeCAD Cloud Browser — Project

## What This Is

A FreeCAD workbench plugin that lets users browse, open, upload, and manage files on cloud storage providers (Dropbox, Google Drive, OneDrive, WebDAV, FTP, SFTP, S3) directly from the FreeCAD interface. Users authenticate once per provider and then work with remote files as naturally as local ones.

## Core Value

Seamless cloud file access without leaving FreeCAD — no manual download/upload cycles.

## Current State

**Version:** v1.0 shipped 2026-05-03
**Status:** Stable, tested, security-hardened
**Codebase:** ~4,500 LOC Python
**Tech stack:** Python, PySide2/Qt, FreeCAD workbench API, keyring, cryptography (Fernet)
**Test suite:** 43 tests (pytest) — unit + integration smoke, all passing

Providers implemented: Dropbox, Google Drive, OneDrive, WebDAV, FTP, S3 (6 total; SFTP planned)

## Requirements

### Validated (v1.0)

- ✓ REQ-001: config_store uses `config_dir` @property and returns deepcopied data — v1.0
- ✓ REQ-002: auth_manager handles credentials safely with return type annotations — v1.0
- ✓ REQ-003: All providers implement `provider_type` / `display_name` as `@property` — v1.0
- ✓ REQ-004: Dropbox chunked upload uses new `UploadSessionCursor` per chunk — v1.0
- ✓ REQ-005: WebDAV path joining uses `posixpath.normpath` to prevent path traversal — v1.0
- ✓ REQ-010: No hardcoded colors — all status colors use `QPalette` roles — v1.0
- ✓ REQ-011: All buttons use readable text labels (not single-character symbols) — v1.0
- ✓ REQ-012: Empty states shown on first launch and in empty folders — v1.0
- ✓ REQ-020: Fernet key stored in system keyring (SEC-1 fixed), not in config directory — v1.0
- ✓ REQ-030: Unit tests for config_store, auth_manager, file_cache — v1.0
- ✓ REQ-031: Integration smoke tests for all 6 providers (list_directory + upload_file) — v1.0

### Active (v1.1 candidates)

- [ ] Regression tests for `_get_fernet_key()` keyring primary path and migration path (tech debt from v1.0)
- [ ] `provider_type` / `display_name` asserted in provider smoke tests
- [ ] WebDAV path-traversal safety assertion test
- [ ] UI automated tests (requires pytest-qt setup)
- [ ] SFTP provider implementation

### Out of Scope

- Mobile app — FreeCAD is a desktop application
- Real-time sync / file watching — on-demand access model
- Offline mode — requires active connection for cloud operations

## Key Decisions

| Decision | Outcome | Notes |
|----------|---------|-------|
| `provider_type`/`display_name` as `@property` | ✓ Good | Delegating to class constants is clean; enforced by abstract base |
| `onedrive._refresh_token_if_needed()` raises `RuntimeError` (not `authenticate()`) | ✓ Good | Qt widget safety; prevents cross-thread UI calls |
| Dropbox cursor recreated per chunk | ✓ Good | Required by Dropbox SDK protocol |
| `config_store._save()` bare `raise` | ✓ Good | Preserves original exception type for callers |
| QPalette roles for all UI colors | ✓ Good | Works on both light and dark themes |
| QStackedWidget for empty states | ✓ Good | Clean show/hide without layout thrashing |
| Keyring as primary Fernet key store, on-disk as fallback | ✓ Good | Transparent migration; headless-safe via fallback |
| `_get_keyring()` lazy import function | ✓ Good | Enables clean mocking in tests without import-time side effects |
| `_is_sensitive` as `AuthManager` static method (not module-level) | ✓ Good | Discovered during testing; cleaner encapsulation |

## Known Issues / Tech Debt

- `_get_fernet_key()` keyring primary path has no regression test (the SEC-1 fix is correct but unprotected)
- REQ-010/011/012 (UI) have no automated tests — manual validation only, no pytest-qt configured
- `# SEC-1 note:` comment in `save_credentials()` lines 227-230 may warrant rewording

## Context

### v1.0 Phase Summary

| Phase | What was done |
|-------|---------------|
| 1 — Core Bug Fixes | Fixed bugs across all 8 providers + 3 core modules; provider_type/display_name @property; thread safety; type annotations |
| 2 — UI Polish | QPalette colors; readable button labels; QStackedWidget empty states |
| 3 — Security Hardening | SEC-1 fixed: _get_fernet_key() now uses OS keyring with legacy file migration |
| 4 — Testing & Verification | 43 tests: 31 unit (config_store, file_cache, auth_manager) + 12 smoke (all 6 providers) |

---
*Last updated: 2026-05-03 after v1.0 milestone*
