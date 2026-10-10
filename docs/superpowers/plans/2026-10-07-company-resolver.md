# Multi-Source Company Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Resolve a document's company from arbitrary layouts by collecting candidates from independent detectors, validating against the roster, and deciding by corroboration.

**Architecture:** New `company_resolver.py` (detectors + combiner). `company_extractor.py` stays as the detector library. Context (folder path) is threaded from callers. Auto-confirm gate becomes configurable. A learned store (`company_hints.json`) feeds a high-weight detector.

**Tech Stack:** Python 3.14, rapidfuzz, fitz, PyQt6.

**Spec:** `docs/superpowers/specs/2026-10-07-company-resolver-design.md`

## Global Constraints

- Roster (`known_companies.json`) remains source of truth; never auto-confirm a non-roster name.
- Auto-confirm default threshold `90`, read from config `company_confidence_threshold`.
- Folder hint is a **weak corroborator only** (weight 0.4); never auto-confirms alone.
- Sender/letterhead/signature/caption outrank body gazetteer hits.
- Keep the output dict shape consumed by `ui/widgets.py` / `session.py` unchanged.
- No OCR changes; detectors work on the existing `page_text` strings.

## Review Focus

- Addressee picked as company (must be rejected).
- Body mention of another company beating the letterhead (must pick sender).
- Single weak source auto-confirming (must not).
- Corroboration not elevating confidence (must reach ≥90).
- Regression: existing `test_resume_scan.py`/`test_organize.py`/`test_merge.py` stay green.

---

### Task 1: Resolver core — model, gazetteer, folder hint, combiner

**Files:** Create `company_resolver.py`; Test `test_company_resolver.py`.

**Interfaces:**
- `CompanyCandidate(name: str, source: str, weight: float)`
- `detect_roster_gazetteer(page_texts: list[dict], roster: list[str]) -> list[CompanyCandidate]`
- `detect_folder_hint(context: dict, roster: list[str]) -> list[CompanyCandidate]`
- `combine(candidates: list[CompanyCandidate], roster: list[str]) -> dict`
- `resolve_company(page_texts, context=None, roster=None) -> dict` (same output keys as `get_company_name_for_filename`).

- [ ] **Step 1: failing tests** for gazetteer (roster entry present → match; absent → none), folder hint (folder≈roster → low-confidence candidate), combiner (two agreeing sources → confidence ≥ 90; single weak → review; no roster match → needs_review).
- [ ] **Step 2:** run tests, see them fail (`ModuleNotFoundError`).
- [ ] **Step 3:** implement the module.
- [ ] **Step 4:** tests pass.
- [ ] **Step 5:** commit.

### Task 2: Letterhead + signature detectors + addressee guard

**Files:** Modify `company_resolver.py`; Test `test_company_resolver.py`.

- [ ] **Step 1:** tests — letterhead snippet yields candidate; addressee-only yields none; signature block yields candidate; body-mention-only does not outrank letterhead.
- [ ] **Step 2:** implement `detect_letterhead`, `detect_signature_block`, `_is_addressee_region`, wire into `resolve_company`.
- [ ] **Step 3:** tests pass; commit.

### Task 3: Wire resolver into the pipeline + configurable gate

**Files:** Modify `company_extractor.py` (delegate `get_company_name_for_filename` to resolver), `pipeline.py:190-194`, `ui/widgets.py`, `ui/main_window.py` (default `company_confidence_threshold: 90`).

- [ ] **Step 1:** tests — `get_company_name_for_filename` keeps output shape; gate uses config value.
- [ ] **Step 2:** implement context threading + resolver call + gate.
- [ ] **Step 3:** full suite green; commit.

### Task 4: Learning store

**Files:** Create/Modify `company_resolver.py` (`detect_learned_hint`, `record_folder_hint`, `record_letterhead_hint`, load/save `company_hints.json`); Modify `ui/review_tab.py` (record on confirm).

- [ ] **Step 1:** tests — record then resolve uses learned hint; corrupted file tolerated.
- [ ] **Step 2:** implement.
- [ ] **Step 3:** commit.

### Task 5: Settings control + spec bundle + eval

**Files:** Modify `ui/settings_tab.py` (threshold spin box), `LumeedQScan.spec` (bundle `company_resolver.py`); add eval script.

- [ ] **Step 1:** settings control reads/writes `company_confidence_threshold`.
- [ ] **Step 2:** spec bundles new module.
- [ ] **Step 3:** eval harness reports auto-confirm rate on labeled samples.
- [ ] **Step 4:** commit.

## Self-Review
- Spec coverage: detectors (T1/T2/T4), combiner (T1), context (T3), gate (T3/T5), learning (T4), settings/spec/eval (T5).
- Review Focus tests: addressee (T2), body-vs-letterhead (T2), single-weak (T1), corroboration (T1), regression (T3).
