# Local Bangumi Sample 0096 Acceptance Goal Plan

## Objective

Make the Local->Bangumi Case Agent resolution-ledger-first, then use `sample_0096_vcb_studio_overlord` as the focused acceptance sample for that architecture.

The immediate objective is not to tune one sample by hand. The objective is to make the Case Agent's intermediate state truly ledger-driven: AI patches local-owned ledger rows, the fixed layer validates ledger mechanics and candidate debt, and the final package is compiled only from a terminal ledger.

The goal is not to hard-code OVERLORD, Ple Ple Pleiades, Bangumi subject ids, or sample-specific file-to-target mappings. The goal is to teach the AI path general skills for derivative/special/recap packages:

- use local structure, numbering, duration, subtitles, title labels, related graph, and visible target episode surfaces as evidence;
- split local rows when the parent locator is too broad;
- map numbered non-regular groups when ownership is closed;
- use candidate-bearing `manual_review` only when ownership remains localized uncertainty;
- keep mechanical row convergence from polluting semantic candidates.

## Resolution Ledger Architecture Goal

The resolution ledger is the only intermediate fact table for package resolution.

- Each must-account `local://` locator or exact local slice belongs to exactly one ledger row.
- Allowed row statuses are `open`, `evidence_required`, `candidate_must_address`, `mapped`, `manual_review`, `target_absent`, `supplemental`, and `fail_closed`.
- Non-terminal rows cannot compile into the final package.
- Direct `submit` cannot finalize the HumanCaseAgent primary runtime; it is a legacy diagnostic surface only. The accepted package must be compiled by the fixed layer from terminal ledger rows.
- The fixed layer may validate schema, locator legality, exact-once local coverage, overlap, target legality, duplicate target items, count/shape mechanics, and candidate debt discharge.
- The fixed layer must not choose semantic ownership, special/extra meaning, target absence, or Bangumi target candidates for the AI.
- BGM is not the ledger primary key. BGM should remain the legal target space and a derived target-usage index for duplicate/ownership diagnostics; the primary row identity stays local-file/local-slice owned.

Candidate debt is a hard ledger contract, not a prompt hint.

- `must_address_candidates` must persist until explicitly discharged.
- A candidate may be discharged by `mapped` only when the row maps to that candidate.
- A candidate may be discharged by `manual_review` only when the row is `manual_review`, carries that candidate in `manual_review_candidate_targets`, and gives the localized uncertainty.
- A candidate may be discharged by `rejected` only with a concrete contradiction such as count, title, duration, subject, or episode mismatch.
- A candidate may be discharged by `fail_closed` only with a concrete blocker.
- Strong suggested rows from `suggested_submit_shape`, `multi_version_submit_shape`, visible slice pairing, duplicate-variant repair, and same-count non-regular candidate repair must become mapped patch templates or candidate debt. They must not remain only in natural-language repair text.

Patch loop behavior:

- `patch_ledger` is the normal resolution action. It patches rows, preserves valid prior rows, and returns row-level repair feedback.
- If latest verifier feedback exposes strong suggested rows, the next ledger patch must apply those rows or explicitly discharge each corresponding candidate.
- Broad `manual_review` or `supplemental` rows must not hide strong exact-slice candidates.
- Final package submission is compiled from a terminal ledger and then passed through the existing submit verifier.

## Expected 0096 Shape

The focused gate should accept the whole sample with no unresolved files.

Expected row outcomes:

| Local group | Expected outcome | Expected target/candidate |
| --- | --- | --- |
| `Gekijouban Soushuuhen OVERLORD` main 2 files | `mapped` | split slices: `Fushisha no Ou` -> `194036 ... 不死者之王`; `Shikkoku no Senshi` -> `198968 ... 漆黑的英雄` |
| `Gekijouban Soushuuhen OVERLORD/SPs` 4 files | `manual_review` | candidate-bearing if visible candidates exist; do not mark as accepted mapping just to unblock |
| `OVERLORD` main 13 files | `mapped` | `112146/episodes/1-13` |
| `OVERLORD/SPs` 8 files | `mapped` | `193953 Play Play 昴宿星团/episodes/1-8` |
| `OVERLORD II` main 13 files | `mapped` | `211027/episodes/1-13` |
| `OVERLORD II/SPs` 14 files | `mapped` | `234089 Play Play 昴宿星团 2`; handle `SP08_1` / `SP08_2` as same-episode variants, not as a count mismatch failure |
| `OVERLORD III` main 13 files | `mapped` | `242170/episodes/1-13` |
| `OVERLORD III/SPs` 13 files | `mapped` | `251539 Play Play 昴宿星团 3`; do not let unrelated/extra related candidates override the 13-file numbered SP owner |
| `OVERLORD Ple Ple Pleiades` singleton | `manual_review` | candidate should stay in the Ple Ple Pleiades / Play Play family, e.g. `193953 Play Play 昴宿星团`; do not drift to OAD unless local evidence explicitly supports OAD |

Approximate expected file counts:

- `mapped`: about 75 files.
- `manual_review`: 5 files (`Gekijouban ... SPs` 4 + Ple Ple singleton 1).
- `supplemental`: none among the 81 must-account main files, unless a row is genuinely outside the targetable content space after AI-owned review.
- `unresolved`: 0.

## Hard Boundaries

Fixed layer may:

- validate ledger schema, target locator legality, exact-once coverage, overlap, duplicate target items, count/shape mechanics, and candidate debt discharge;
- expose visible local facts and target facts: path hierarchy, labels, numbering, duration, subtitle compact, duplicate variants, Bangumi related subjects, episode surfaces, search provenance;
- detect bad mechanical shapes and tell AI which row must be repaired;
- preserve saved valid ledger rows and compile the final package from terminal ledger rows.

Fixed layer must not:

- map any local row to a Bangumi id because the sample is 0096;
- encode OVERLORD/Pleiades aliases or subject ids as rules;
- decide that SP/OVA/OAD/recap/supplemental/manual_review semantics are true;
- auto-convert a rejected target into a semantic `manual_review_candidate_targets`;
- force a row into `supplemental` or `manual_review` solely because it is non-regular.

AI layer may be taught:

- how to compare local numbered non-regular groups against related same-count Bangumi subjects;
- how to use duration/subtitle/title evidence to upgrade from manual_review to mapped;
- how to split recap/movie parent rows into exact local slices;
- how to handle duplicate same-number local variants as alternate versions of one target episode;
- how to preserve stronger candidates over noisy or mechanically rejected targets;
- when a localized uncertainty should remain `manual_review` without blocking the package.

## Current Regression

The latest row-pressure implementation improved convergence: repeated invalid `patch_ledger` attempts no longer cause 0096 to fail closed.

However, it introduced or exposed two wrong tendencies:

1. Rejected mapped targets can pollute `manual_review_candidate_targets`.
   - Example: `OVERLORD Ple Ple Pleiades` drifted to `OVERLORD OAD`.
   - Example: recap main rows may retain only the last rejected target instead of the two title-matched recap movie candidates.

2. Terminal row pressure can make AI stop at `manual_review` or `supplemental` too early.
   - For numbered Play Play SP groups, local structure + related Bangumi structure + duration/subtitle/title evidence should allow accepted mapped rows.
   - Non-regular does not mean weak evidence by default.

## Design Direction

Keep the resolution ledger architecture, but refine the convergence and evidence-upgrade behavior:

1. Candidate provenance
   - Track candidate provenance in feedback: strong visible candidate, same-count related candidate, title/slice pairing, saved ledger candidate, or rejected mechanical target.
   - `manual_review_candidate_submit_shape` should prefer strong/saved/visible semantic candidates.
   - Rejected mechanical targets may be shown under `do_not_retry_targets_without_new_evidence`, but should not become candidate hints unless independently supported.

2. Non-regular evidence upgrade
   - For non-regular rows, prompt the AI to ask whether local/Bangumi evidence closes ownership before choosing manual_review/supplemental.
   - Evidence that can close ownership includes same numbered range, related subject title family, local title labels, duration consistency, subtitle compact confirmation, and duplicate-variant handling.
   - If evidence is sufficient, mapped is preferred over manual_review.

3. Slice and variant mapping
   - Recap/movie parent rows with multiple file labels should be split into exact local episode slices when each slice has a visible title-paired target.
   - Duplicate same-number local variants should be allowed to map to the same target item as alternate versions when the row is explicitly split by variant locators.

4. Manual review boundaries
   - `manual_review` remains accepted accounting only for localized uncertainty.
   - It must carry useful candidates when visible candidates exist.
   - It should not hide rows whose ownership has already been closed by upgraded evidence.

5. Prompt and repair frontier
   - The repair frontier should distinguish:
     - "this target shape is mechanically invalid; do not retry it";
     - "these semantic candidates remain plausible";
     - "these local facts can upgrade the evidence".
   - AI must not read row-pressure as a reason to choose low-effort terminal states.

## Implementation Plan

1. Audit current 0096 traces
   - Compare accepted checkpoints:
     - `...resolution_ledger_candidate_shape_20260520`
     - `...resolution_ledger_row_pressure_final_20260520`
   - Identify exactly where LR1, LR4/LR6/LR8, and LR9 diverge.

2. Repair candidate provenance
   - Prevent rejected target/range from being promoted into `manual_review_candidate_submit_shape` without independent semantic support.
   - Preserve visible semantic candidates from search/inspect/saved ledger rows.
   - Add tests that a mechanically rejected OAD candidate cannot overwrite a stronger Ple Ple candidate.

3. Strengthen AI non-regular mapping guidance
   - Update HumanCaseAgent instructions and repair frontier text:
     - non-regular rows can be mapped when evidence closes;
     - manual_review is fallback after upgrade, not the default;
     - supplemental requires concrete packaging/extra reason.
   - Keep this generic; do not mention OVERLORD ids as rules.

4. Ensure slice/variant shapes are easy to submit
   - Verify local slice locators for recap movies are visible and target pairing options expose both recap movie targets.
   - Verify duplicate variant locators for SP08 variants can compile without duplicate-target rejection when same-number variants are intended alternate versions.

5. Validate
   - Unit tests for candidate provenance, non-regular upgrade, split recap mapping, and variant mapping.
   - `compileall src`.
   - Focused pytest:
     - `tests/test_case_agent_human_case_agent.py`
     - `tests/test_case_agent_human_cognitive_workspace.py`
     - `tests/test_local_bangumi_sample_runner.py`
     - `tests/test_case_agent_dossier.py`
     - `tests/test_case_agent_accounted_for_audit.py`
     - `tests/test_local_package_projection.py`
   - Run `sample_0096_vcb_studio_overlord` several times.

## Acceptance Gate

Focused gate passes only when:

- `accepted_contract_ok=true`;
- no unresolved files;
- no wrong accepted mapping;
- recap main files are mapped by slice to the two recap movie targets;
- theater-manners/recap SP row remains localized `manual_review`;
- three regular seasons are mapped;
- Play Play numbered SP groups are mapped, including variant handling for duplicate same-number local files;
- Ple Ple singleton is `manual_review` with a Ple Ple / Play Play family candidate, not OAD drift;
- fixed-layer boundary scan finds no sample-specific title/id mapping rule.

## Goal Text

Use this as the execution goal:

```text
Implement the Resolution Ledger architecture first. HumanCaseAgent should resolve Local->Bangumi packages by patching local-owned ledger rows; direct submit is not a final path. The fixed layer validates local coverage, overlap, target legality, duplicate targets, candidate debt discharge, and final compilation from a terminal ledger only. The fixed layer must not add OVERLORD/Ple Ple/Bangumi id/file-to-target semantic mapping rules. Candidate debt from suggested_submit_shape, multi_version_submit_shape, visible slice pairing, duplicate variants, and same-count non-regular candidate repairs must persist until mapped, candidate-bearing manual_review with localized uncertainty, rejected with concrete contradiction, or fail_closed with blocker. If narrow fixes are insufficient to make 0096 stable within --max-rounds 10, expand the refactor scope to repair frontier, turn budget, patch template projection, and verifier repair feedback so every verifier repair bucket is converted into durable, copyable ledger row choices rather than natural-language-only guidance. Use 0096 as the focused acceptance sample: the target accepted shape is mapped_file_count=76, manual_review_file_count=5, excluded=0, unresolved=0, with manual_review limited to the four recap/theater SP files and the OVERLORD Ple Ple Pleiades singleton. Validate with focused pytest, compileall, focused 0096 runs within --max-rounds 10 when credentials/runtime permit, and a fixed-layer boundary scan for sample-specific semantic hardcoding.
```
