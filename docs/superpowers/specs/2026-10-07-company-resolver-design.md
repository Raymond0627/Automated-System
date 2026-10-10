# Multi-Source Company Resolver — Design

Date: 2026-10-07
Status: Approved (design)

## Purpose

The current company-name extractor assumes legal **case captions**
("Complainant … vs … Respondent"), routing-slip `Subject:` fields, or
keyword windows. Real inputs include company **letterheads**, business
letters addressed to a regulator, invoices, policies and other archetypes
that match none of those. In those cases every tier fails and the document
is routed to manual review even though the company is plainly present (for
example in a letterhead and/or the grouping folder name).

Concrete motivating sample (division 143):
`.../_ecms_out/_staging/143/NEW ZEALAND INSURANCE CORPORATION/*.pdf` — scanned
company letters to the Insurance Commission. The letterhead reads "THE NEW
ZEALAND INSURANCE COMPANY LIMITED … MANILA BRANCH (INCORPORATED IN NEW
ZEALAND)"; the body also mentions an unrelated company ("Semirara Coal
Corporation"). Every document currently lands in the review tab.

## Goal

Resolve the company for arbitrary document types by collecting candidates
from **independent detectors**, validating each against the roster, and
deciding by **corroboration** — increasing the auto-confirm rate without
making auto-confirm any less safe.

## Non-Goals (YAGNI)

- No machine-learned/logo image fingerprints (text/roster/learning only).
- No change to the roster's role as source of truth.
- No change to the Review tab's data shape consumed downstream.
- No document-type classifier unless a later phase proves it necessary.

## Key Insight

The **sender/letterhead entity** is what matters; the body may **mention
other companies**, and the **addressee** (e.g. "Office of the Insurance
Commissioner") is not the company. So detectors must prioritize the
letterhead/signature/caption region and down-weight body mentions, and the
folder name is a **weak corroborator only** (input is not always grouped by
company).

## Architecture

New module `company_resolver.py`. `company_extractor.py` remains the
detector library (its public functions are reused unchanged).

### Data

```python
@dataclass
class CompanyCandidate:
    name: str        # raw (un-normalized) candidate text
    source: str      # detector id, e.g. "caption", "letterhead", "gazetteer"
    weight: float    # base trust, 0..1
```

### Detectors (each returns a list of `CompanyCandidate`)

| Detector | Source | Base weight | Notes |
|---|---|---|---|
| `detect_case_caption` | `caption` | 0.9 | Wraps existing `extract_from_caption` / `extract_from_caption_wide` / `extract_from_complainant_respondent`. |
| `detect_routing_slip` | `routing_slip` | 0.8 | Wraps existing `extract_from_routing_slip`. |
| `detect_letterhead` | `letterhead` | 0.85 | New. See heuristic below. |
| `detect_signature_block` | `signature` | 0.8 | New. See heuristic below. |
| `detect_roster_gazetteer` | `gazetteer` | 0.9 exact / 0.7 near | New. Match roster entries present anywhere in text; sender-region hits outrank body hits. |
| `detect_keyword_window` | `keyword` | 0.6 | Wraps existing `extract_from_full_keywords`. |
| `detect_folder_hint` | `folder` | 0.4 | New. Parent folder name(s) fuzzy-matched to roster; corroborator only. |
| `detect_learned_hint` | `learned` | 0.95 | New. Consults `company_hints.json`. |

**Letterhead heuristic (`detect_letterhead`):** consider the first ~15
non-empty lines of each page. Find a maximal run of 1–4 consecutive lines
that (a) are ≥70% uppercase or title-case and (b) contain a strong corporate
keyword (`STRONG_KEYWORDS_RE`). Exclude runs inside the addressee block
(lines following an addressee marker such as "Office of the", "The Insurance
Commission", or between "To:"/"Dear" and the salutation). Emit the run text
as a candidate.

**Signature heuristic (`detect_signature_block`):** find a closing marker
(`Very truly yours`, `Yours faithfully`, `Yours truly`, `Sincerely`,
`Respectfully`, `Truly yours`). Search the next ~8 lines and the 2 lines
immediately above the marker for a line/run with a strong corporate keyword;
emit it. Reject addressee-block matches.

**Gazetteer matching (`detect_roster_gazetteer`):** normalize the full
document text once. For each roster entry, test for presence by normalized
token-subsequence (all significant tokens of the entry appear in order in the
text). Exact normalized substring → weight 0.9; token-subsequence-only →
0.7. Split text into an early "sender region" (first ~25% of the first page,
plus the signature region) and the rest; hits in the sender region get a
severity boost over body-only hits.

### Addressee/regulator guard

A candidate is dropped if its only occurrences fall inside the addressee
block, or its normalized form matches a configurable blacklist
(`company_blacklist`, default includes `INSURANCE COMMISSION` and similar
regulator names). Implemented as a shared `is_addressee_only(text, name)`
helper used by the letterhead/signature/gazetteer detectors.

### Combiner

1. Normalize every candidate (`normalize_company_name`).
2. Match each candidate to the roster via fuzzy `token_set_ratio` with the
   existing tier thresholds and margin rules.
3. Group candidates by resolved roster entry; compute a combined score:
   - `combined = max(source_weight * (fuzzy_score/100))` plus a
     **corroboration bonus** for each *additional independent* source that
     resolves to the same entry.
   - **Rule:** ≥2 independent sources agreeing on the same roster entry →
     `company_confidence >= 90` (auto-confirm), even if individual scores
     are moderate.
4. Single source: `company_confidence` = fuzzy score scaled by source weight,
   mapped onto the existing thresholds so weak sources cannot auto-confirm
   alone.
5. No roster match → `company_name` empty or raw candidate with
   `company_confidence = 0`, `needs_review = True`.

**Output shape (unchanged):** `company_name`, `matched_roster_entry`,
`fuzzy_match_score`, `company_confidence`, `tier_used` (now a source string
like `"letterhead+gazetteer"`), `cross_validated`, `confidence_label`,
`needs_review`.

## Context Threading

`get_company_name_for_filename(page_texts, context=None)` where
`context` may carry `{"rel_dir", "original_path"}`. Threaded from
`pipeline.extract_dates_for_batch` (`pipeline.py:194`) and the OCR worker in
`ui/widgets.py`. Default `None` preserves existing behavior for tests.

## Configurable Auto-Confirm Gate

- New config key `company_confidence_threshold`, default `90`.
- Added to `ui/main_window.py` defaults; persisted via `ui/settings_tab.py`.
- Replace the hard-coded `company_confidence >= 90` in `ui/widgets.py` with
  the config value; add a Settings spin box next to the date
  `confidence_threshold`.

## Learning Loop

- Keep `learn_company_name` (roster add on manual confirm).
- New runtime store `%APPDATA%\LumeedQScan\company_hints.json`:
  ```json
  {"folders": {"NEW ZEALAND INSURANCE CORPORATION": "NEW ZEALAND INSURANCE CORPORATION"},
   "letterheads": {"THE NEW ZEALAND INSURANCE COMPANY LIMITED": "NEW ZEALAND INSURANCE CORPORATION"}}
  ```
- On a manual confirm in the Review tab: record `folder→company` (from the
  document's parent folder) and, when the resolved source included
  `letterhead`, record `letterhead-line→company`.
- `detect_learned_hint` consults these with weight 0.95.
- A Review-tab context action "Remember folder as <company>" writes the
  folder mapping explicitly.

## Files

- **New:** `company_resolver.py`, `test_company_resolver.py`;
  runtime `company_hints.json` under `DATA_DIR`.
- **Edit:** `company_extractor.py` (export detectors; keep public API),
  `pipeline.py` (context + resolver call), `ui/widgets.py` (worker call site
  + configurable gate), `ui/review_tab.py` (learning on confirm),
  `ui/main_window.py` + `ui/settings_tab.py` (threshold), `LumeedQScan.spec`
  (bundle new module), `docs` for the plan.

## Testing

- Per-detector unit tests on representative text snippets: letterhead,
  signature block, addressee-only, case caption, body-mention-only. No OCR
  required (feed text).
- Combiner tests: corroboration → auto-confirm; single weak source → review;
  addressee-only → rejected; body-mention vs letterhead → picks the sender.
- Gazetteer tests: roster entry present in text → match; absent → none.
- Integration: resolver run on OCR'd New Zealand sample text → roster match
  to `NEW ZEALAND INSURANCE CORPORATION`.
- Regression: `test_resume_scan.py`, `test_organize.py`, `test_merge.py`
  stay green.

## Rollout Phases

1. Resolver scaffolding + `detect_roster_gazetteer` + `detect_folder_hint` +
   combiner + context threading + tests.
2. `detect_letterhead` + `detect_signature_block` + addressee guard.
3. Learning store + Settings gate + evaluation harness reporting auto-confirm
   rate over `processed/`, `flagged/`, and the staging sample.

## Risks & Mitigations

- **Body mentions other companies** → sender-region weighting; letterhead and
  signature outweigh gazetteer body hits.
- **Addressee picked** → addressee guard + roster validation.
- **Folder name wrong** (input not always grouped) → low weight, corroboration
  only.
- **Performance** → normalize the roster once; token-subsequence checks are
  small relative to OCR cost.
- **Regression** → keep `get_company_name_for_filename` output shape and
  existing tests.

## Open Decisions (resolved)

- Module layout: new `company_resolver.py`. ✅
- Auto-confirm gate default 90, adjustable in Settings. ✅
- Learning scope: both `folder→company` and `letterhead→company`. ✅
