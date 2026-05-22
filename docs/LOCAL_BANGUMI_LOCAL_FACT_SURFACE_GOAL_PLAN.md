# Local Bangumi Local Fact Surface Goal Plan

Generated: 2026-05-19 Asia/Shanghai

## Context

We already have a local evidence model, but it is mostly a filesystem and
package-shape model:

- `src/rename/local_evidence.py`
  - `LocalFileEvidence`
  - `LocalEvidence`
  - `build_local_evidence(...)`
- `tools/run_local_bangumi_mapping_sample_pool.py`
  - `local_evidence_from_raw_sample(...)`
- `src/rename/case_agent/models.py`
  - `LocalFileCard`
  - `LocalClusterCard`
  - `LocalSpanCard`
  - `CaseDossier`
- `src/rename/case_agent/local_package_projection.py`
  - compact root, counts, extension counts, directory summaries, representative
    samples, and raw path/bracket text.

The gap is not "no local evidence model". The gap is that media facts, subtitle
facts, stream facts, and explicit missing-fact states are not first-class
evidence yet. That matters for cases like `sample_0096`, where a human can use
duration, local packaging, and related-subject evidence to understand derivative
shorts, while the Agent currently mostly sees text/path evidence.

This goal is the first local fact surface pass. It must make facts available
without making the fixed layer choose semantic ownership.

## Goal

Extend the existing local evidence path into a typed, best-effort local fact
surface that can be reused by the real pipeline and the sample pool.

The new surface should let the Agent see facts like:

- path hierarchy and parent folder facts;
- raw filename/path tokens without fixed-layer episode interpretation;
- optional duration/container/media facts when a real media file can be probed;
- optional external or embedded subtitle facts when they exist;
- `.strm`/stream facts and explicit reasons duration/container facts are absent;
- missing fact classes when a fact was unavailable, unprobeable, or not present.

The fixed layer must expose facts only. The Agent remains responsible for
forming any hypothesis about derivative shorts, duplicate packaging,
target_absent, supplemental, or mapping.

## Non Goals

- Do not make `sample_0096` derivative SP rows accepted in this goal.
- Do not add Overlord/Pleiades-specific aliases, Bangumi ids, title bridges, or
  file-to-target mappings.
- Do not let fixed code infer "this SP row belongs to this related subject".
- Do not accept unknown rows as generic supplemental just to unblock a package.
- Do not make duration/subtitle facts mandatory. Real inputs may be `.strm`,
  missing files, remote streams, archives, or samples with only raw metadata.
- Do not replace `LocalEvidence`; extend it or add a sidecar fact model so old
  call sites can migrate safely.

## Boundary Rules

Allowed fixed-layer output:

- raw path facts: directory segments, parent folder, basename, filename stem,
  suffix/extension, sibling count, and representative sibling names;
- raw numeric/token facts only when they are clearly labeled as raw text tokens,
  not as episode/season semantics;
- raw media facts: duration seconds, container, video stream count, resolution,
  audio/subtitle stream counts, probe status, and probe error class;
- raw subtitle facts: discovered external subtitles, embedded subtitle tracks,
  language/name metadata, bounded title/header snippets when available, and
  provenance for where the text came from;
- raw stream facts: `.strm` scheme/path class, sanitized target summary, and
  why duration/container probing is unavailable;
- explicit missing facts: `fact_class`, `status`, `reason`, `attempted`, and
  `locator_ref`.

Forbidden fixed-layer output:

- `recommended_target`;
- `candidate_derivative_mapping`;
- `strong_candidate`;
- "likely owner" wording;
- target-shaped suggestions such as `local SP01-SP13 -> Bangumi subject X`;
- sample-specific subject ids, aliases, or title translations;
- automatic `supplemental` / `target_absent` / `mapped` semantic decisions based
  on count, duration, folder name, or related graph coincidence.

Verifier behavior:

- It may verify legal refs, coverage, duplicate target use, and whether an
  Agent-authored claim cites the fact classes it claims to use.
- It must not choose the target or outcome for the Agent.

## Proposed Model

Prefer a sidecar model first, then project the sidecar into existing cards:

- `LocalFactSurface`
  - `root_name`
  - `root_path`
  - `files`
  - `directory_summaries`
  - `missing_fact_summary`
- `LocalFileFact`
  - `file_id`
  - `relative_path`
  - `path_facts`
  - `classification_facts`
  - `container_facts`
  - `subtitle_facts`
  - `stream_facts`
  - `missing_facts`
- `LocalPathFacts`
  - `directory_segments`
  - `parent_folder`
  - `basename`
  - `filename_stem`
  - `extension`
  - `raw_number_tokens`
  - `raw_marker_tokens`
  - `sibling_summary`
- `LocalContainerFacts`
  - `probe_status`: `available`, `missing_file`, `unsupported`, `timeout`,
    `probe_error`, `not_attempted`
  - `duration_seconds`
  - `container_format`
  - `video_stream_count`
  - `audio_stream_count`
  - `subtitle_stream_count`
  - `resolution`
  - `probe_error_class`
- `LocalSubtitleFacts`
  - `external_subtitle_refs`
  - `embedded_track_summary`
  - `language_markers`
  - `bounded_text_snippets`
  - `snippet_source`
- `LocalStreamFacts`
  - `is_stream_file`
  - `stream_scheme`
  - `sanitized_target_summary`
  - `probe_limitation`
- `LocalMissingFact`
  - `fact_class`
  - `status`
  - `reason`
  - `attempted`
  - `source`

Important modeling constraint:

Keep the current `LocalEvidence` no-semantics contract. Existing tests assert
that `LocalFileEvidence` does not expose filename semantics such as episode
tokens directly. If raw number/path tokens become useful, put them under an
explicit `path_facts`/`raw_*` fact surface and document that they are not
episode interpretation.

## Implementation Phases

1. Inventory current fact flow

   - Trace `build_local_evidence(...)` into `Rename`, Case Agent dossier
     construction, local package projection, and sample runner.
   - Identify the exact payloads where `LocalEvidence` is serialized into
     `local_evidence_summary`, `LocalFileCard`, `LocalSpanCard`, and
     `local_package_projection`.
   - Record where sample-pool raw JSON lacks real filesystem access.

2. Add sidecar fact model

   - Add dataclasses or pydantic models for `LocalFactSurface` and file-level
     fact classes.
   - Build it from `LocalEvidence` first so all existing call sites still work.
   - Ensure every optional probe has an explicit missing fact when absent.
   - Keep serialization stable and compact.

3. Add path fact extraction

   - Extract directory segments, parent folder, basename, stem, extension, and
     sibling summaries.
   - If number-like tokens are extracted, label them `raw_number_tokens`.
   - Do not classify tokens as episode, season, special, OVA, SP ownership, or
     target hints.

4. Add best-effort media probe overlay

   - Reuse existing media probing utilities where possible.
   - For zero-byte, missing, `.strm`, unsupported, timeout, or failed probes,
     write missing facts instead of raising mapping errors.
   - Keep probe timeout bounded and configurable.
   - Ensure sample-pool dry builds can run without real media files.

5. Add subtitle fact overlay

   - Detect external subtitle files near video files and embedded subtitle
     stream metadata when available.
   - Use only bounded snippets or metadata. Do not dump full subtitle content
     into AI context.
   - Treat missing subtitles as an explicit fact state, not as failure.

6. Project facts into Case Agent evidence

   - Prefer reusing `local_file_detail` evidence for explicit local locators.
   - If the schema becomes unclear, add a narrow request type such as
     `local_fact_detail`.
   - Bind every short ref to a readable semantic card in the same payload.
   - Keep compact package-level summaries separate from explicit detail
     requests.

7. Update Agent guidance

   - Teach the Agent to request local facts for unresolved SP, special-marker,
     singleton compilation, duplicate-packaging, and suspected derivative-short
     rows before making terminal claims.
   - Teach the Agent that missing facts are real evidence: if duration/subtitle
     facts are unavailable, it should fail closed when the remaining evidence is
     insufficient.
   - Do not tell the Agent that a specific local row belongs to a specific
     Bangumi subject.

8. Update validator/audit

   - Add audit fields for local fact requests, missing fact classes, probe
     status counts, and Agent-authored hypothesis citations.
   - Add support-shape checks only: if the Agent claims derivative mapping from
     duration/related facts, require cited local facts plus cited related graph
     facts.
   - Keep the validator out of semantic target selection.

9. Update docs and sample artifacts

   - Update this plan with actual implementation notes.
   - Update `docs/LOCAL_BANGUMI_MANUAL_REPLAY_SAMPLE_0096.md` with the final
     run directory and whether SP rows remained fail_closed.
   - If fields are added to AI snapshots, document the stable contract.

## Test Plan

Unit tests to add or update:

- `tests/test_local_evidence.py`
  - Existing no-semantics tests remain meaningful.
  - New assertions should check that raw path facts are labeled as raw facts and
    not exposed as fixed-layer episode interpretation.
- `tests/test_local_fact_surface.py`
  - Path facts for nested directories.
  - Missing duration/container facts for `.strm`, missing, unsupported, and
    zero-byte files.
  - Optional duration facts for probeable files using mocked probe utilities.
  - Subtitle metadata/snippet bounds with mocked subtitle files.
- `tests/test_local_package_projection.py`
  - Projection includes compact fact summaries without exceeding size limits.
  - Projection never emits target-shaped candidate wording.
- `tests/test_local_bangumi_sample_runner.py`
  - Raw sample JSON builds the same fact model shape.
  - Dry build succeeds when duration/subtitle facts are missing.
- Case Agent tests
  - Local fact refs appear with readable cards.
  - Agent payload can request local fact details for explicit local locators.
  - Verifier checks cited fact support shape but does not choose outcomes.

Focused validation commands:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_local_evidence.py tests\test_local_package_projection.py tests\test_local_bangumi_sample_runner.py tests\test_case_agent_dossier.py tests\test_case_agent_orchestrator_agent.py tests\test_ai_models.py -q
.venv\Scripts\python.exe -m compileall src\rename src\ai tools\run_local_bangumi_mapping_sample_pool.py tools\run_local_bangumi_human_gate.py tools\summarize_local_bangumi_human_trace.py
.venv\Scripts\python.exe tools\scan_local_bangumi_boundary_risks.py src\rename\case_agent src\rename\local_evidence.py src\rename\local_fact_surface.py tools\run_local_bangumi_mapping_sample_pool.py --json
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0096 --limit 1 --dry-build --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_local_fact_surface_dry_build_20260519
.venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0096 --max-rounds 12 --sample-timeout-seconds 420 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_local_fact_surface_gate_20260519
```

Broader validation is not required unless this goal touches global verifier,
preflight, execution, or prompt gates outside the local fact surface path.

## Sample 0096 Acceptance Gate

This goal does not require `sample_0096` to become fully accepted.

The expected focused-gate result is the current safe shape:

- main TV rows remain mapped/accepted:
  - `OVERLORD main-episodes` -> regular episodes 1-13;
  - `OVERLORD II main-episodes` -> regular episodes 1-13;
  - `OVERLORD III main-episodes` -> regular episodes 1-13;
- the two recap movie rows remain mapped/accepted;
- non-controversial supplemental material such as previews, CM, PV, Menu,
  NCOP, NCED, and already-safe extras remains accepted as front-filtered
  support-only material where the local filename marker is deterministic;
- evidence-insufficient derivative-short/SP/composite rows may remain
  `fail_closed`, including the current problematic rows:
  - `OVERLORD Ple Ple Pleiades main`;
  - `OVERLORD II SPs special-marker`;
  - `OVERLORD III SPs special-marker`.

Acceptance fails if:

- any previously accepted main TV or recap movie unit regresses to fail_closed;
- a generic supplemental decision is used to hide an unresolved derivative-short
  row;
- fixed code emits target-shaped derivative mapping suggestions;
- the run fails because of provider/schema/tool-boundary errors;
- the result is a naked `budget_exhausted` without exact work-unit blockers;
- the Agent accepts SP/derivative rows without citing the required fact classes.

In short: after this goal, `sample_0096` should still look like "everything that
was already decidable stays accepted; only the evidence-insufficient SP-like
work units are allowed to fail closed."

## 2026-05-19 Implementation Notes

Implemented as a sidecar fact model in `src/rename/local_fact_surface.py`.

Stable contract:

- `LocalFactSurface` is built from `LocalEvidence` and can be serialized through
  `local_fact_surface_to_dict(...)`.
- `LocalFileFact` carries raw `path_facts`, `container_facts`,
  `subtitle_facts`, `stream_facts`, and `missing_facts`.
- `raw_number_tokens` and `raw_marker_tokens` are explicitly path facts only;
  they are not episode, season, special, owner, or target interpretation.
- Missing or unavailable facts are represented as data, not exceptions. The
  dry sample pool therefore emits `not_attempted` for videos without real local
  paths and `unsupported` for non-video files.
- `.strm` files expose stream scheme/path-class facts and an explicit probe
  limitation rather than duration/container claims.
- Subtitle facts include bounded external-subtitle references and snippets.

Projection points updated:

- `LocalEvidence` has an optional `fact_surface`; `build_local_evidence(...)`
  populates it from real local paths when available.
- The raw sample pool uses the same builder through
  `local_evidence_from_raw_sample(...)`; dry-build rows include file fact count,
  probe status counts, and missing-fact summaries.
- `LocalFileCard` now has fact fields, and the Case Agent projections carry
  compact fact summaries into dossier, prompting, query composition, briefing,
  and package projection surfaces.
- HumanCaseAgent keeps fact cards off the initial desk by default. Local facts
  are available through explicit `inspect` scopes such as `facts` or `details`,
  which prevents large packages from being biased by unrequested missing-fact
  summaries.
- Agent guidance was updated to treat local facts as observations only:
  missing duration/subtitle facts do not override visible title, count, slice,
  or target evidence.

Boundary notes:

- The fixed layer does not emit `recommended_target`,
  `candidate_derivative_mapping`, owner recommendations, sample-specific
  Overlord/Pleiades aliases, Bangumi ids, or file-to-target mappings.
- New recovery-frontier entries are mechanical repair categories; they do not
  decide whether SP, derivative, recap, or supplemental semantics are true.
- Boundary scan of the source paths listed below returned `finding_count=0`.

## 2026-05-19 Validation Result

Focused unit regression:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_local_evidence.py tests\test_local_fact_surface.py tests\test_local_package_projection.py tests\test_local_bangumi_sample_runner.py tests\test_case_agent_dossier.py tests\test_case_agent_human_case_agent.py tests\test_case_agent_orchestrator_agent.py tests\test_ai_models.py -q
```

Result: `225 passed`.

Compile:

```powershell
.venv\Scripts\python.exe -m compileall src\rename src\ai tools\run_local_bangumi_mapping_sample_pool.py tools\run_local_bangumi_human_gate.py tools\summarize_local_bangumi_human_trace.py
```

Result: passed.

Boundary scan:

```powershell
.venv\Scripts\python.exe tools\scan_local_bangumi_boundary_risks.py src\rename\case_agent src\rename\local_evidence.py src\rename\local_fact_surface.py tools\run_local_bangumi_mapping_sample_pool.py --json
```

Result: `finding_count=0`.

Dry build:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_mapping_sample_pool.py --sample sample_0096 --limit 1 --dry-build --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_preview_prefilter_dry_build_20260519
```

Result: `dry_build`, `file_count=676`, `video_count=168`,
`main_video_count=81`, `supplemental_candidate_count=87`,
`local_fact_file_count=676`, probe statuses
`not_attempted=168` and `unsupported=508`,
`files_with_external_subtitles=79`.

Focused `sample_0096` gate:

```powershell
.venv\Scripts\python.exe tools\run_local_bangumi_human_gate.py --sample 0096 --max-rounds 12 --sample-timeout-seconds 420 --output-dir tests\sample_pool\generated\local_bangumi_mapping_sample_0096_manual_review_hints_gate2_20260519
```

Result: `status=accepted`, `accepted_contract_ok=true`,
`final_verifier_passed=true`, `turn_count=11`,
`tool_sequence=search -> inspect -> search -> submit -> search -> submit -> submit -> submit -> submit -> submit -> submit`,
`submit_rejection_count=4`, `strict_failure_count=0`,
`legacy_subagent_call_count=0`.

Spot check of final assignment shape:

- 39 files map to visible Bangumi refs: the three regular TV seasons.
- 42 preview-marked files are front-filtered deterministic supplemental
  material and do not enter the HumanCaseAgent must-account contract.
- 42 files are `UNALIGNED` `manual_review`, which is counted as accounted-for
  but not mapped or supplemental.
- TV rows remain mapped: `OVERLORD` 13 files, `OVERLORD II` 13 files, and
  `OVERLORD III` 13 files.
- Numbered SP groups now stay out of mapped ownership when only related
  same-count `Play Play` structure is visible. They are `manual_review` instead
  of mapped or supplemental.
- `OVERLORD Ple Ple Pleiades` remains `manual_review`: the local evidence does
  not prove whether the singleton is a whole-series compilation, a duplicate
  packaging unit, or one visible `Play Play` item, so it must not claim episode
  1 or any other single item as a subject proxy.
- `manual_review` rows may carry low-confidence `review_candidate_targets` for
  human replay; those hints do not populate final `target_ref`, do not create
  `target_refs`, and do not count as mapped/accepted targets.
- Recap movies and movie/theater-manners SPs remain `manual_review` in this
  conservative run; this is accepted-accounted and not unsafe.

## Recommended Goal Command

```text
/goal 按 docs\LOCAL_BANGUMI_LOCAL_FACT_SURFACE_GOAL_PLAN.md 执行本地事实表面建模：扩展现有 LocalEvidence/Case Agent evidence surface，新增可缺失的 path/container/duration/subtitle/stream/missing facts；样本池复用同一模型；固定层只暴露事实，不生成 target/owner/supplemental 结论，不写样本专属 Overlord/Pleiades 规则。验收运行文档里的 unit/compile/boundary scan/dry-build/sample_0096 focused gate；0096 必须保持当前安全形状：main TV、两部 recap movie、非争议 supplemental 维持 accepted/mapped，只有证据不足的 SP/derivative-short/composite work units 可以 fail_closed，不能因新事实模型导致其他已解决部分回退。
```
