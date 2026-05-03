# FreeCAD Cloud Browser — Roadmap

## Project Goal
A FreeCAD workbench plugin that lets users browse, open, upload, and manage files on cloud storage providers (Dropbox, Google Drive, OneDrive, WebDAV, FTP, SFTP, S3) directly from FreeCAD.

## Milestone 1: Stable & Polished Plugin

### Phase 1: Core Bug Fixes & Code Quality
**Goal:** Fix all critical and high-severity bugs across all provider modules and core infrastructure so the plugin is stable and safe to use.
**Status:** Complete

Files:
- core/config_store.py
- core/auth_manager.py
- core/file_cache.py
- providers/base.py
- providers/dropbox.py
- providers/onedrive.py
- providers/google_drive.py
- providers/webdav.py
- providers/ftp.py
- providers/s3.py
- providers/__init__.py
- ui/browser_panel.py
- ui/provider_dialog.py
- CloudBrowserWorkbench.py
- InitGui.py

Requirements: REQ-001, REQ-002, REQ-003, REQ-004, REQ-005

### Phase 2: UI Polish & Accessibility
**Goal:** Improve the UI so it works correctly on light and dark themes, all interactive elements are clearly labeled, and empty states guide the user rather than showing a blank screen.
**Status:** In Progress

Files:
- ui/browser_panel.py
- ui/provider_dialog.py

Requirements: REQ-010, REQ-011, REQ-012

### Phase 3: Security Hardening
**Goal:** Move the Fernet encryption key out of the data directory (currently stored alongside the ciphertext), implement keyring-based key storage, and add any remaining security improvements.
**Status:** Planned
**Plans:** 1 plan

Plans:
- [x] 03-01-PLAN.md — Replace _get_fernet_key() with OS keyring-backed storage + migration helpers

Files:
- core/auth_manager.py

Requirements: REQ-020

### Phase 4: Testing & Verification
**Goal:** Add unit tests for core modules (config_store, auth_manager, file_cache) and integration smoke tests for each provider's list_directory and upload_file methods.
**Status:** Planned
**Plans:** 3 plans

Plans:
- [ ] 04-01-PLAN.md — Unit tests for ConfigStore and FileCache (REQ-030)
- [ ] 04-02-PLAN.md — Unit tests for AuthManager credential tiers (REQ-030)
- [ ] 04-03-PLAN.md — Provider smoke tests for list_directory and upload_file (REQ-031)

Files:
- tests/

Requirements: REQ-030, REQ-031

---

## Requirements

| ID | Description |
|----|-------------|
| REQ-001 | config_store uses property config_dir and deep copies returned data |
| REQ-002 | auth_manager handles credentials safely with type annotations |
| REQ-003 | All providers implement provider_type and display_name as @property |
| REQ-004 | Dropbox chunked upload uses new UploadSessionCursor per chunk |
| REQ-005 | WebDAV path joining uses posixpath.normpath to prevent path traversal |
| REQ-010 | No hardcoded hex/named colors — all colors use QPalette roles |
| REQ-011 | All buttons have readable text labels (not single-character symbols) |
| REQ-012 | Empty states shown on first launch and in empty folders |
| REQ-020 | Fernet key stored in system keyring, not in config directory |
| REQ-030 | Unit tests for config_store, auth_manager, file_cache |
| REQ-031 | Integration smoke tests for each provider |
