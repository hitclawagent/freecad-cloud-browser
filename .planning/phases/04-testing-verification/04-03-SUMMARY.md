---
phase: 04-testing-verification
plan: "03"
subsystem: providers
tags: [testing, smoke-tests, mocking, providers]
dependency_graph:
  requires: [04-01, 04-02]
  provides: [provider-smoke-tests]
  affects: [providers/dropbox.py, providers/google_drive.py, providers/onedrive.py, providers/webdav.py, providers/ftp.py, providers/s3.py]
tech_stack:
  added: []
  patterns: [unittest.mock.patch, class-based test grouping, module-level request mocking]
key_files:
  created:
    - tests/test_providers_smoke.py
  modified: []
decisions:
  - "OneDrive requests patched at requests module level (not providers.onedrive.requests) because requests is imported locally inside list_directory and upload_file methods"
metrics:
  duration: "~15 min"
  completed: "2026-05-03"
  tasks_completed: 1
  tasks_total: 1
  files_created: 1
  files_modified: 0
---

# Phase 04 Plan 03: Provider Smoke Tests Summary

## One-liner

12 SDK-mocked smoke tests (list_directory + upload_file) for all 6 cloud providers — Dropbox, Google Drive, OneDrive, WebDAV, FTP, S3.

## What Was Built

- `tests/test_providers_smoke.py`: 405 lines, 12 tests in 6 test classes (one per provider)
- Each provider has:
  - A `list_directory` smoke test verifying correct SDK interaction and `RemoteItem` list return
  - An `upload_file` smoke test verifying the underlying SDK upload method was called
- No real network calls, credentials, or FreeCAD imports required
- All 12 tests pass; full test suite is 43/43 green (no regressions)

## Commits

| Hash     | Message                                                                 |
|----------|-------------------------------------------------------------------------|
| c7458c8  | test(04-03): provider smoke tests for all 6 providers, list_directory and upload_file |

## Task Summary

| Task | Name                              | Status   | Commit  |
|------|-----------------------------------|----------|---------|
| 1    | Provider smoke tests (TDD green)  | Complete | c7458c8 |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] OneDrive mock patch target corrected**
- **Found during:** task 1 (test run)
- **Issue:** `patch("providers.onedrive.requests.get")` failed with `AttributeError: module 'providers.onedrive' has no attribute 'requests'` because `requests` is imported locally inside each method (not at module top-level)
- **Fix:** Changed patch targets to `patch("requests.get")` and `patch("requests.put")` which patches the `requests` module object directly before the local `import requests` inside each method re-binds it
- **Files modified:** tests/test_providers_smoke.py
- **Commit:** c7458c8

## Implementation Notes

### Mock Strategy Per Provider

| Provider    | Client Attribute | Mock Injection Pattern |
|-------------|-----------------|------------------------|
| Dropbox     | `_dbx`          | `provider._dbx = mock_dbx` — bypasses authenticate() entirely |
| Google Drive| `_service`      | `provider._service = mock_service` — bypasses _build_service() |
| OneDrive    | `_token`        | `provider._token = "fake"` + patch `requests.get/put` at module level |
| WebDAV      | `_client`       | `provider._client = mock_client` — bypasses authenticate() |
| FTP         | `_ftp`          | `provider._ftp = mock_ftp; provider._use_sftp = False` |
| S3          | `_client`       | `provider._client = mock_client` — bypasses authenticate() |

### WebDAV list() format
The `list_directory` implementation uses `get_info=True`, returning dicts. Tests mock accordingly with `{"name": ..., "isdir": ..., "path": ..., "size": ..., "modified": ...}` entries.

### FTP list format
The `_list_ftp` method uses `retrlines("LIST", callback)` with Unix-style permission strings, not MLSD. Tests simulate this via `side_effect` on the mock to invoke the callback with Unix-style lines.

### S3 paginator
The `list_directory` uses `get_paginator("list_objects_v2")` + `.paginate()`. Tests mock `get_paginator` returning a mock paginator whose `.paginate()` returns a single-page list with both `Contents` and `CommonPrefixes`.

## Known Stubs

None — all tests wire real mock data to provider methods and verify actual return values.

## Threat Flags

None — test file introduces no new network endpoints, auth paths, or trust boundaries. Fake credential strings carry no real secrets (T-04-05 accepted in plan threat model).

## Self-Check: PASSED

- [x] `tests/test_providers_smoke.py` exists (405 lines)
- [x] commit `c7458c8` exists in git log
- [x] 12 tests collected and pass
- [x] Full suite: 43/43 pass
