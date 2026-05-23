from __future__ import annotations

import json

from src.rename.case_agent.human_case_agent import (
    AgentLocator,
    CaseCognitiveWorkspace,
    HUMAN_CASE_AGENT_INSTRUCTIONS,
    HumanCaseSession,
    InvestigationAgendaItem,
    InspectToolArgs,
    LocatorRegistry,
    PackageResolution,
    PatchLedgerToolArgs,
    ResolutionLedger,
    ResolutionLedgerCandidateDebt,
    ResolutionLedgerRow,
    ResolutionWorkUnit,
    SUBMIT_REPAIR_GROUP_KEYS,
    SearchToolArgs,
    SubmitToolArgs,
    REPAIR_FRONTIER_SOURCE_KEYS,
    _active_repair_agenda_for_prompt,
    _inspect_tool,
    _register_existing_targets,
    _local_target_title_pairing_options,
    _local_target_title_pairing_options_for_slice,
    _excluded_title_tail_unresolved_after_search_repairs,
    _budget_pressure_tool_choice,
    _case_resolution_goal_strong_candidates,
    _compile_resolution_ledger_to_submit_result,
    _inspect_args_with_required_repair_locators,
    _ledger_with_revise_overlap_rows_pruned,
    _ledger_validation_units_from_issues,
    _one_mapping_option_per_local_slice,
    _query_hints_for_locator,
    _repair_agenda_from_submit_feedback,
    _repair_frontier_rows_from_agenda,
    _repair_has_uninspected_evidence_upgrade_action,
    _search_query_variants,
    _search_tool,
    _subject_card_from_api,
    _subject_with_search_query_provenance,
    _ledger_choice_patch_rows_from_repair,
    _suggested_ledger_patch_rows_from_repair,
    _strong_suggested_submit_shape_rows_from_repair,
    _strong_suggested_ledger_patch_rows_from_repair,
    _patch_ledger_tool,
    _submit_tool,
    _title_season_number_hint,
    _validate_resolution_ledger,
    _terminal_fail_closed_contract_guard_output,
    _target_surface_actions_from_repair,
    _target_non_regular_mapping_support_details,
    _turn_tail,
    _visible_source_query_bridge_targets,
    build_human_case_desk,
    human_case_tool_definitions,
    run_human_case_agent,
)
from src.bangumi.models import BangumiSubject, BangumiSubjectRelation
from src.rename.case_agent.models import (
    BangumiItemCard,
    BangumiSubjectCard,
    CaseBudget,
    CaseContract,
    CaseHeader,
    LocalFileCard,
)
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def _two_episode_workspace() -> CaseEvidenceWorkspace:
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-HUMAN-AGENT"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Show/Show - 01.mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Show/Show - 02.mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=1,
                title="Show",
                name="Show",
                name_cn="Show",
                eps=2,
                total_episodes=2,
                search_query_ref="Show",
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS1", sort=2, ep=2, title="Episode 2"),
        ],
    )


def _special_marker_bundle_workspace(case_id: str = "CASE-SPECIAL-MARKER-BUNDLE") -> CaseEvidenceWorkspace:
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id=case_id),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3", "LF4"],
            allowed_file_refs=["LF1", "LF2", "LF3", "LF4"],
            visible_target_refs=["BE1"],
        ),
        local_files=[
            LocalFileCard(
                ref=f"LF{index}",
                path=f"Pack/SPs/[Group] Franchise Special Theater Manners [{label}].mkv",
                is_main=True,
            )
            for index, label in enumerate(["A1B2C3D4", "B2C3D4E5", "C3D4E5F6", "D4E5F6A7"], start=1)
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=81,
                title="Franchise Specials",
                name="Franchise Specials",
                name_cn="Franchise Specials",
                eps=1,
                total_episodes=1,
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Special 1"),
        ],
    )


def test_title_season_number_hint_reads_cjk_season_tokens():
    assert _title_season_number_hint("OVERLORD II [SP01]") == 2
    assert _title_season_number_hint("OVERLORD \u7b2c\u4e09\u5b63") == 3
    assert _title_season_number_hint("OVERLORD \u7b2c4\u671f") == 4


def test_one_mapping_option_per_local_slice_prefers_unused_targets():
    rows = _one_mapping_option_per_local_slice(
        [
            {"local": "local://movies/episode/1", "target": "target://bangumi/1/episode/1"},
            {"local": "local://movies/episode/1", "target": "target://bangumi/2/episode/1"},
            {"local": "local://movies/episode/2", "target": "target://bangumi/1/episode/1"},
            {"local": "local://movies/episode/2", "target": "target://bangumi/2/episode/1"},
        ]
    )

    assert [row["target"] for row in rows] == [
        "target://bangumi/1/episode/1",
        "target://bangumi/2/episode/1",
    ]


def test_human_agent_prompt_keeps_tools_simple_and_agent_semantic():
    assert "HumanCaseAgent" in HUMAN_CASE_AGENT_INSTRUCTIONS
    tool_names = {tool["function"]["name"] for tool in human_case_tool_definitions()}
    assert "patch_ledger" in tool_names
    assert "submit" in tool_names
    assert "resolution ledger is your explicit case desk" in HUMAN_CASE_AGENT_INSTRUCTIONS
    assert "Use fail_closed only when the local locator remains semantically unresolved" in HUMAN_CASE_AGENT_INSTRUCTIONS
    assert "candidate debt discharge" in HUMAN_CASE_AGENT_INSTRUCTIONS


def test_resolution_ledger_candidate_debt_requires_discharge():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episode/1"
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="manual_review",
                reason="Candidate remains unresolved but was not carried.",
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(target=target, source="visible same-title candidate")
                ],
            )
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    assert any(issue["issue"] == "ledger_candidate_debt_open" for issue in issues)


def test_resolution_ledger_manual_review_candidate_discharges_debt():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episode/1"
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="manual_review",
                reason="Episode title evidence remains unresolved; keep candidate for human replay.",
                manual_review_candidate_targets=[target],
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(target=target, source="visible same-title candidate")
                ],
            )
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    assert not [issue for issue in issues if str(issue["issue"]).startswith("ledger_candidate")]


def test_candidate_debt_feedback_preserves_candidate_shapes_for_frontier():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="supplemental",
                reason="Dropped the visible candidate without discharging it.",
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(
                        target=target,
                        source="active_repair_suggested_submit_shape",
                        mapped_outcome="mapped_special_or_ova",
                        support=[f"{local}/episodes/1-2"],
                        reason="visible suggested target must be addressed",
                    )
                ],
            )
        ]
    )

    issues, row_summaries = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )
    units = _ledger_validation_units_from_issues(issues, row_summaries)
    agenda = _repair_agenda_from_submit_feedback(
        {
            "accepted": False,
            "package": {"issue_counts": {"ledger_candidate_debt_open": 1}},
            "units": units,
        },
        repeated=False,
    )
    frontier = _repair_frontier_rows_from_agenda(agenda)

    candidate_unit = next(unit for unit in units if unit["unit"] == "LR1")
    assert candidate_unit["suggested_submit_shape"][0]["target"] == target
    assert target in candidate_unit["manual_review_candidate_submit_shape"][0]["manual_review_candidate_targets"]
    assert frontier[0]["suggested_submit_shape"][0]["local"] == f"{local}/episodes/1-2"
    assert frontier[0]["high_quality_next_actions"][0].startswith("patch suggested_submit_shape rows")


def test_strong_candidate_debt_feedback_exposes_exact_manual_review_shape():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    exact_local = f"{local}/episodes/1-2"
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="manual_review",
                reason="Broad parent review that does not carry the exact candidate.",
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(
                        target=target,
                        source="manual_review_strong_non_regular_mapping_should_revise",
                        mapped_outcome="mapped_special_or_ova",
                        support=[exact_local],
                        reason="visible strong candidate must be addressed",
                    )
                ],
            )
        ]
    )

    issues, row_summaries = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )
    units = _ledger_validation_units_from_issues(issues, row_summaries)

    candidate_unit = next(unit for unit in units if unit["unit"] == "LR1")
    manual_shape = candidate_unit["manual_review_candidate_submit_shape"][0]
    assert manual_shape["local"] == exact_local
    assert manual_shape["manual_review_candidate_targets"] == [target]
    assert "unresolved" in manual_shape["reason"]


def test_manual_review_slice_pairing_frontier_searches_before_low_confidence_review_shape():
    agenda = _repair_agenda_from_submit_feedback(
        {
            "accepted": False,
            "package": {"issue_counts": {"manual_review_visible_slice_pairing_should_split": 1}},
            "units": [
                {
                    "unit": "LR1",
                    "local": ["local://movie-pack/main-episodes"],
                    "issue": "manual_review_visible_slice_pairing_should_split",
                    "search_queries_to_try": ["Franchise First King"],
                    "manual_review_candidate_submit_shape": [
                        {
                            "local": "local://movie-pack/main-episodes/episode/1",
                            "outcome": "manual_review",
                            "manual_review_candidate_targets": ["target://bangumi/101-franchise-first-king"],
                        }
                    ],
                }
            ],
        },
        repeated=False,
    )
    frontier = _repair_frontier_rows_from_agenda(agenda)

    actions = frontier[0]["high_quality_next_actions"]
    assert actions[0] == "search: Franchise First King"
    assert any("manual_review_candidate_submit_shape" in action for action in actions[1:])


def test_search_query_provenance_ignores_worse_rank_for_existing_subject():
    subject = BangumiSubjectCard(
        ref="BS1",
        subject_id=101,
        title="Movie One",
        search_query_ref="Franchise First Movie",
        search_rank=1,
    )

    unchanged = _subject_with_search_query_provenance(
        subject,
        query="Franchise Second Movie",
        matched_query="Franchise Second Movie",
        rank=3,
    )
    updated = _subject_with_search_query_provenance(
        subject,
        query="Franchise First Movie alias",
        matched_query="Franchise First Movie alias",
        rank=1,
    )

    assert unchanged.search_query_ref == "Franchise First Movie"
    assert unchanged.search_rank == 1
    assert "Franchise First Movie alias" in updated.search_query_ref
    assert updated.search_rank == 1


def test_ledger_coverage_missing_exposes_candidate_local_locators():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR2",
                local=[f"{local}/episode/2"],
                status="manual_review",
                reason="Only the second file was covered.",
            )
        ]
    )

    issues, row_summaries = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )
    units = _ledger_validation_units_from_issues(issues, row_summaries)
    agenda = _repair_agenda_from_submit_feedback(
        {
            "accepted": False,
            "package": {"issue_counts": {"ledger_coverage_missing": 1}},
            "units": units,
        },
        repeated=False,
    )
    frontier = _repair_frontier_rows_from_agenda(agenda)

    coverage_issue = next(issue for issue in issues if issue["issue"] == "ledger_coverage_missing")
    assert coverage_issue["candidate_local_locators"][0]["locator"] == f"{local}/episode/1"
    assert units[0]["candidate_local_locators"][0]["locator"] == f"{local}/episode/1"
    assert frontier[0]["candidate_local_locators"][0]["locator"] == f"{local}/episode/1"
    assert frontier[0]["high_quality_next_actions"][0].startswith("patch rows for candidate_local_locators")


def test_ledger_coverage_overlap_points_to_overlapping_rows():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(row_id="LR1", local=[local], status="manual_review", reason="Parent row."),
            ResolutionLedgerRow(row_id="LR2", local=[f"{local}/episode/2"], status="manual_review", reason="Overlapping slice."),
        ]
    )

    issues, row_summaries = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )
    overlap_issue = next(issue for issue in issues if issue["issue"] == "ledger_coverage_overlap")
    units = _ledger_validation_units_from_issues(issues, row_summaries)

    assert set(overlap_issue["row_ids"]) == {"LR1", "LR2"}
    assert overlap_issue["overlap_rows"][0]["rows"]
    assert {unit["unit"] for unit in units if unit["issue"] == "ledger_coverage_overlap"} == {"LR1", "LR2"}


def test_patch_ledger_complete_rows_can_compile_to_submit_shape():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = registry.subject_locator_by_id[1]
    session = HumanCaseSession(
        case_id="CASE-HUMAN-AGENT",
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="open")]
        ),
    )
    args = PatchLedgerToolArgs(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="mapped",
                target=target,
                mapped_outcome="mapped_regular_span",
                episode_start=1,
                episode_end=2,
                reason="Visible two-episode target matches the two local files.",
            )
        ],
        reason="complete ledger",
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        args,
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert output["accepted"] is True
    assert complete is True
    submit_args = SubmitToolArgs(
        resolution=PackageResolution(
            work_units=[
                ResolutionWorkUnit(
                    unit_label=row.row_id,
                    local=row.local,
                    outcome="mapped_regular_span",
                    target=row.target,
                    episode_start=row.episode_start,
                    episode_end=row.episode_end,
                    reason=row.reason,
                )
                for row in session.resolution_ledger.rows
            ]
        )
    )
    result = _submit_tool(workspace, registry, submit_args)
    assert result.accepted is True
    ledger_result = _compile_resolution_ledger_to_submit_result(
        workspace,
        registry,
        session.resolution_ledger,
    )
    assert ledger_result.accepted is True
    assert ledger_result.output.action == "submit_verdict"
    assert ledger_result.mapped_file_count == 2


def test_human_agent_direct_submit_is_rejected_until_ledger_patch():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = registry.subject_locator_by_id[1]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def call_responses_tool_agent(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "id": "resp_submit",
                    "tool_calls": [
                        {
                            "call_id": "call_submit",
                            "name": "submit",
                            "arguments": json.dumps(
                                {
                                    "resolution": {
                                        "work_units": [
                                            {
                                                "unit_label": "legacy-main",
                                                "local": [local],
                                                "outcome": "mapped_regular_span",
                                                "target": target,
                                                "episode_start": 1,
                                                "episode_end": 2,
                                                "reason": "Legacy direct submit should be converted to ledger feedback.",
                                            }
                                        ],
                                        "package_reason": "legacy direct submit",
                                    },
                                    "repair_strategy": "revise_saved_rows",
                                    "reason": "legacy direct submit",
                                    "dry_run": False,
                                }
                            ),
                        }
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                }
            return {
                "id": "resp_patch",
                "tool_calls": [
                    {
                        "call_id": "call_patch",
                        "name": "patch_ledger",
                        "arguments": json.dumps(
                            {
                                "rows": [
                                    {
                                        "row_id": "LR1",
                                        "local": [local],
                                        "status": "mapped",
                                        "target": target,
                                        "mapped_outcome": "mapped_regular_span",
                                        "episode_start": 1,
                                        "episode_end": 2,
                                        "reason": "Terminal ledger row maps the visible two-episode span.",
                                    }
                                ],
                                "repair_strategy": "revise_saved_rows",
                                "reason": "terminal ledger",
                                "dry_run": False,
                            }
                        ),
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }

    result = run_human_case_agent(workspace, FakeClient(), object(), max_rounds=3)

    assert result.status == "accepted"
    assert result.final_output is not None
    assert result.final_output.action == "submit_verdict"
    audit_notes = [
        audit.get("note")
        for audit in result.final_workspace.judge_request_audits
        if isinstance(audit, dict)
    ]
    assert "human_case_agent_submit_rejected_resolution_ledger_required" in audit_notes
    assert "human_case_agent_resolution_ledger_compiled" in audit_notes


def test_patch_ledger_split_rows_replace_covered_parent_row():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="manual_review", reason="Parent row.")]
        ),
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1_split_1",
                    local=[f"{local}/episode/1"],
                    status="manual_review",
                    reason="First split row.",
                ),
                ResolutionLedgerRow(
                    row_id="LR1_split_2",
                    local=[f"{local}/episode/2"],
                    status="manual_review",
                    reason="Second split row.",
                ),
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert output["accepted"] is True
    assert complete is True
    assert [row.row_id for row in session.resolution_ledger.rows] == ["LR1_split_1", "LR1_split_2"]


def test_resolution_ledger_compile_runs_submit_semantic_repairs_for_numbered_sp_supplemental():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-LEDGER-SP-SUPPLEMENTAL-SEMANTIC-REPAIR"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Pack/SPs/[Group] Franchise [SP01].mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Pack/SPs/[Group] Franchise [SP02].mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=71,
                title="Franchise",
                name="Franchise",
                name_cn="Franchise",
                eps=12,
                total_episodes=12,
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=72,
                title="Play Play Stars",
                name="Play Play Stars",
                name_cn="Play Play Stars",
                eps=2,
                total_episodes=2,
                relation_to_main="side_story",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS2", sort=1, ep=1, title="Short 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=2, ep=2, title="Short 2"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="supplemental",
                target="local extra-material",
                reason="Treat the numbered SP files as bonus extras.",
            )
        ],
        summary="terminal ledger",
    )

    result = _compile_resolution_ledger_to_submit_result(workspace, registry, ledger)

    assert result.accepted is False
    assert result.feedback["from_resolution_ledger"] is True
    assert result.feedback["package"]["issue_counts"]["numbered_special_exclusion_needs_target_evidence"] == 1
    repair = result.feedback["package"]["numbered_special_exclusion_repairs"][0]
    assert repair["same_count_visible_subjects"][0]["target"] == registry.subject_locator_by_id[72]


def test_resolution_ledger_rejects_broad_supplemental_for_unnumbered_special_marker_bundle():
    workspace = _special_marker_bundle_workspace("CASE-LEDGER-SPECIAL-MARKER-SUPPLEMENTAL-REPAIR")
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    local_locator = registry.locators[local]
    support_target = registry.subject_locator_by_id[81]
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="supplemental",
                support=[support_target],
                reason="Treat the theater special bundle as broad bonus SP material.",
            )
        ],
        summary="terminal ledger",
    )

    result = _compile_resolution_ledger_to_submit_result(workspace, registry, ledger)
    agenda = _repair_agenda_from_submit_feedback(result.feedback, repeated=False)
    frontier = _repair_frontier_rows_from_agenda(agenda)
    session = HumanCaseSession(case_id=workspace.header.case_id, resolution_ledger=ledger)
    suggested_rows = _suggested_ledger_patch_rows_from_repair(session, result.feedback)

    assert local.endswith("/special-marker")
    assert not local_locator.episode_file_refs
    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"]["supplemental_special_marker_without_hard_extra_reason"] == 1
    repair = result.feedback["package"]["supplemental_special_marker_repairs"][0]
    shape = repair["manual_review_candidate_submit_shape"][0]
    assert repair["local"] == local
    assert shape["local"] == local
    assert shape["outcome"] == "manual_review"
    assert shape["manual_review_candidate_targets"] == [support_target]
    assert frontier[0]["manual_review_candidate_submit_shape"][0]["local"] == local
    assert suggested_rows[0]["status"] == "manual_review"
    assert suggested_rows[0]["manual_review_candidate_targets"] == [support_target]


def test_patch_ledger_guard_requires_special_marker_manual_review_template():
    workspace = _special_marker_bundle_workspace("CASE-LEDGER-SPECIAL-MARKER-PATCH-GUARD")
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    support_target = registry.subject_locator_by_id[81]
    repair = {
        "accepted": False,
        "package": {
            "issue_counts": {"supplemental_special_marker_without_hard_extra_reason": 1},
            "supplemental_special_marker_repairs": [
                {
                    "unit": "LR1",
                    "local": local,
                    "issue": "supplemental_special_marker_without_hard_extra_reason",
                    "manual_review_candidate_submit_shape": [
                        {
                            "local": local,
                            "outcome": "manual_review",
                            "manual_review_candidate_targets": [support_target],
                            "confidence": "low",
                            "reason": "Special-marker bundle has localized title/target-surface uncertainty.",
                        }
                    ],
                }
            ],
        },
    }
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="supplemental", reason="pending")]
        ),
        observations=[{"tool": "patch_ledger", "output": {"compiled_submit_feedback": repair}}],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[local],
                    status="supplemental",
                    reason="Still only broad theater bonus material.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is False
    assert output["accepted"] is False
    assert output["issue"] == "patch_ledger_suggested_shape_unaddressed"
    assert output["suggested_ledger_patch_rows"][0]["status"] == "manual_review"
    assert output["suggested_ledger_patch_rows"][0]["manual_review_candidate_targets"] == [support_target]
    assert session.resolution_ledger.rows[0].status == "supplemental"


def test_resolution_ledger_allows_special_marker_supplemental_with_hard_non_owner_reason():
    workspace = _special_marker_bundle_workspace("CASE-LEDGER-SPECIAL-MARKER-HARD-EXTRA")
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="supplemental",
                reason="CM/menu preview clips from disc packaging, not a Bangumi-owned story item.",
            )
        ],
        summary="terminal ledger",
    )

    result = _compile_resolution_ledger_to_submit_result(workspace, registry, ledger)

    assert result.accepted is True
    assert "package" not in result.feedback or (
        "supplemental_special_marker_without_hard_extra_reason"
        not in result.feedback.get("package", {}).get("issue_counts", {})
    )


def test_resolution_ledger_canonicalizes_mapped_target_before_candidate_debt_check():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    canonical_target = registry.subject_locator_by_id[1]
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="mapped",
                target="target://bangumi/1",
                mapped_outcome="mapped_regular_span",
                episode_start=1,
                episode_end=2,
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(target=canonical_target, discharge="mapped")
                ],
                reason="Short target locator resolves to the same visible subject.",
            )
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    assert not [issue for issue in issues if str(issue["issue"]).startswith("ledger_candidate")]
    assert ledger.rows[0].target == canonical_target


def test_ledger_compile_feedback_exposes_blocking_units_for_repair_agenda():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = registry.subject_locator_by_id[1]
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="mapped",
                target=target,
                reason="Subject target omitted the required episode range.",
            )
        ]
    )

    result = _compile_resolution_ledger_to_submit_result(workspace, registry, ledger)
    agenda = _repair_agenda_from_submit_feedback(result.feedback, repeated=False)
    frontier = _repair_frontier_rows_from_agenda(agenda)

    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"]["episode_range_required"] == 1
    assert result.feedback["units"][0]["unit"] == "LR1"
    assert agenda["blocking_units"][0]["unit"] == "LR1"
    assert frontier
    assert result.feedback["units"][0]["suggested_submit_shape"][0]["target"].endswith("/episodes/1-2")
    assert any(
        "suggested_submit_shape" in action
        for action in frontier[0]["high_quality_next_actions"]
    )


def test_ledger_feedback_offers_manual_review_shape_for_unvisible_target_candidate():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = registry.subject_locator_by_id[1]

    units = _ledger_validation_units_from_issues(
        [
            {
                "issue": "locator_not_found",
                "row_id": "LR1",
                "locator": "target://bangumi/999-show/episodes/1-2",
                "candidate_target_locators": [{"target": target, "title": "Show"}],
            }
        ],
        [
            {
                "row_id": "LR1",
                "local": [local],
                "status": "mapped",
                "target": "target://bangumi/999-show/episodes/1-2",
                "file_refs": list(workspace.contract.main_file_refs),
            }
        ],
    )
    feedback = {
        "accepted": False,
        "package": {"issue_counts": {"locator_not_found": 1}},
        "units": units,
    }
    agenda = _repair_agenda_from_submit_feedback(feedback, repeated=False)
    frontier = _repair_frontier_rows_from_agenda(agenda)

    shape = units[0]["manual_review_candidate_submit_shape"]
    assert shape[0]["outcome"] == "manual_review"
    assert target in shape[0]["manual_review_candidate_targets"]
    assert any(
        "manual_review_candidate_submit_shape" in action
        for action in frontier[0]["high_quality_next_actions"]
    )


def test_frontier_merges_package_repair_shape_into_blocking_unit():
    agenda = {
        "blocking_units": [
            {
                "unit": "LR4",
                "local": ["local://pack-sps/special-marker"],
                "issue": "numbered_special_exclusion_needs_target_evidence",
            }
        ],
        "numbered_special_exclusion_repairs": [
            {
                "unit": "LR4",
                "local": "local://pack-sps/special-marker",
                "issue": "numbered_special_exclusion_needs_target_evidence",
                "suggested_submit_shape": [
                    {
                        "local": "local://pack-sps/special-marker/episodes/1-8",
                        "target": "target://bangumi/91-play-play/episodes/1-8",
                        "outcome": "mapped_special_or_ova",
                    }
                ],
            }
        ],
    }

    rows = _repair_frontier_rows_from_agenda(agenda)

    assert rows[0]["suggested_submit_shape"][0]["target"] == "target://bangumi/91-play-play/episodes/1-8"
    assert rows[0]["high_quality_next_actions"][0].startswith("patch suggested_submit_shape rows")
    assert any("manual_review" in action for action in rows[0]["high_quality_next_actions"][1:])


def test_strong_candidate_debt_feedback_does_not_offer_low_confidence_manual_shape():
    target = "target://bangumi/91-play-play/episodes/1-8"
    units = _ledger_validation_units_from_issues(
        [
            {
                "issue": "ledger_candidate_manual_review_discharge_missing_target",
                "row_id": "LR4",
                "candidate_target": target,
                "candidate_source": "manual_review_strong_non_regular_mapping_should_revise",
                "candidate_target_locators": [
                    {
                        "target": target,
                        "source": "manual_review_strong_non_regular_mapping_should_revise",
                    }
                ],
                "suggested_submit_shape": [
                    {
                        "local": "local://pack-sps/special-marker/episodes/1-8",
                        "target": target,
                        "outcome": "mapped_special_or_ova",
                    }
                ],
            }
        ],
        [
            {
                "row_id": "LR4",
                "local": ["local://pack-sps/special-marker/episodes/1-8"],
                "target": "",
                "manual_review_candidate_targets": ["target://bangumi/91-play-play"],
                "file_refs": ["LF1"],
            }
        ],
    )
    agenda = _repair_agenda_from_submit_feedback(
        {
            "accepted": False,
            "package": {"issue_counts": {"ledger_candidate_manual_review_discharge_missing_target": 1}},
            "units": units,
        },
        repeated=False,
    )
    frontier = _repair_frontier_rows_from_agenda(agenda)

    assert "manual_review_candidate_submit_shape" not in units[0]
    assert frontier[0]["suggested_submit_shape"][0]["target"] == target
    assert frontier[0]["high_quality_next_actions"][0].startswith("patch suggested_submit_shape rows")


def test_revise_saved_rows_prunes_old_rows_with_same_local_coverage():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    local = desk["local_locators"][0]["locator"]
    current = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(row_id="LR1", local=[local], status="manual_review", reason="old row"),
            ResolutionLedgerRow(row_id="LR1_hold", local=[local], status="manual_review", reason="stale hold row"),
        ]
    )
    patch_rows = [
        ResolutionLedgerRow(
            row_id="LR1",
            local=[local],
            status="mapped",
            target="target://bangumi/1-show/episodes/1-2",
            mapped_outcome="mapped_regular_span",
            reason="replace old review row",
        )
    ]
    proposed = ResolutionLedger(rows=[patch_rows[0], current.rows[1]], version=1)

    pruned = _ledger_with_revise_overlap_rows_pruned(
        registry,
        current,
        proposed,
        patch_rows,
        repair_strategy="revise_saved_rows",
    )

    assert [row.row_id for row in pruned.rows] == ["LR1"]


def test_patch_ledger_prunes_range_parent_when_exact_child_rows_cover_it():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    subject = registry.subject_locator_by_id[1]
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episodes/1-2"],
                    status="manual_review",
                    manual_review_candidate_targets=[subject],
                    reason="Broad range row should be replaced by exact child rows.",
                ),
                ResolutionLedgerRow(
                    row_id="LR1a",
                    local=[f"{local}/episode/1"],
                    status="mapped",
                    mapped_outcome="mapped_explicit_item",
                    target=f"{subject}/episode/1",
                    reason="Exact child row already saved.",
                ),
            ]
        ),
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1b",
                    local=[f"{local}/episode/2"],
                    status="mapped",
                    mapped_outcome="mapped_explicit_item",
                    target=f"{subject}/episode/2",
                    reason="Second exact child row completes the parent coverage.",
                )
            ],
            repair_strategy="repair_single",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert output["accepted"] is True
    assert complete is True
    assert [row.row_id for row in session.resolution_ledger.rows] == ["LR1a", "LR1b"]
    assert output["issue_counts"] == {}


def test_patch_ledger_drops_broad_parent_patch_when_exact_children_already_cover_it():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    subject = registry.subject_locator_by_id[1]
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1a",
                    local=[f"{local}/episode/1"],
                    status="mapped",
                    mapped_outcome="mapped_explicit_item",
                    target=f"{subject}/episode/1",
                    reason="Exact child row already saved.",
                ),
                ResolutionLedgerRow(
                    row_id="LR1b",
                    local=[f"{local}/episode/2"],
                    status="mapped",
                    mapped_outcome="mapped_explicit_item",
                    target=f"{subject}/episode/2",
                    reason="Exact child row already saved.",
                ),
            ]
        ),
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episodes/1-2"],
                    status="manual_review",
                    manual_review_candidate_targets=[subject],
                    reason="Do not re-add a broad review parent over exact mapped children.",
                )
            ],
            repair_strategy="repair_single",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert output["accepted"] is True
    assert complete is True
    assert [row.row_id for row in session.resolution_ledger.rows] == ["LR1a", "LR1b"]
    assert output["issue_counts"] == {}


def test_ledger_coverage_feedback_does_not_create_synthetic_local_patch_locator():
    units = _ledger_validation_units_from_issues(
        [
            {
                "issue": "ledger_coverage_overlap",
                "file_refs": ["LF1", "LF2"],
                "required": "Resolve overlapping coverage by revising existing rows.",
            }
        ],
        [],
    )
    agenda = _repair_agenda_from_submit_feedback(
        {
            "accepted": False,
            "package": {"issue_counts": {"ledger_coverage_overlap": 1}},
            "units": units,
        },
        repeated=False,
    )
    frontier = _repair_frontier_rows_from_agenda(agenda)

    assert units[0]["local"] == []
    assert units[0]["file_refs"] == ["LF1", "LF2"]
    assert frontier
    assert frontier[0]["local"] == []
    assert all(
        not str(local).startswith("local://ledger-") and not str(local).startswith("LF")
        for row in frontier
        for local in row["local"]
    )


def test_frontier_filters_synthetic_local_values_and_exposes_negative_target_absence_shape():
    rows = _repair_frontier_rows_from_agenda(
        {
            "blocking_units": [
                {
                    "unit": "LR4",
                    "local": ["local://ledger-coverage-overlap/blocker", "LF4", "local://pack-sps/special-marker"],
                    "issue": "numbered_special_exclusion_needs_target_evidence",
                    "negative_target_absence_support_candidates": [
                        {"target": "target://bangumi/91-franchise", "title": "Franchise"}
                    ],
                    "negative_target_absence_submit_shape": {
                        "local": "local://pack-sps/special-marker",
                        "outcome": "bangumi_target_absent, supplemental, or non_bangumi",
                        "support": "target://bangumi/91-franchise",
                        "reason": "No corresponding target after inspecting the target surface.",
                    },
                }
            ]
        }
    )

    assert rows[0]["local"] == ["local://pack-sps/special-marker"]
    assert rows[0]["negative_target_absence_support_candidates"][0]["target"] == "target://bangumi/91-franchise"
    assert rows[0]["negative_target_absence_submit_shape"]["local"] == "local://pack-sps/special-marker"
    assert rows[0]["high_quality_next_actions"][0].startswith("patch negative_target_absence_submit_shape")


def test_ledger_feedback_offers_manual_review_shape_for_visible_surface_mismatch():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = registry.subject_locator_by_id[1]

    units = _ledger_validation_units_from_issues(
        [
            {
                "issue": "target_episode_surface_missing",
                "row_id": "LR1",
                "target": f"{target}/episodes/1-3",
                "visible_alternate_subjects": [{"target": target, "title": "Show"}],
            }
        ],
        [
            {
                "row_id": "LR1",
                "local": [local],
                "status": "mapped",
                "target": f"{target}/episodes/1-3",
                "file_refs": list(workspace.contract.main_file_refs),
            }
        ],
    )

    shape = units[0]["manual_review_candidate_submit_shape"]
    assert shape[0]["outcome"] == "manual_review"
    assert target in shape[0]["manual_review_candidate_targets"]


def test_ledger_feedback_does_not_promote_rejected_target_to_review_candidate():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = registry.subject_locator_by_id[1]

    units = _ledger_validation_units_from_issues(
        [
            {
                "issue": "target_episode_surface_missing",
                "row_id": "LR1",
                "target": f"{target}/episodes/3-4",
            }
        ],
        [
            {
                "row_id": "LR1",
                "local": [local],
                "status": "mapped",
                "target": f"{target}/episodes/3-4",
                "file_refs": list(workspace.contract.main_file_refs),
            }
        ],
    )

    assert "manual_review_candidate_submit_shape" not in units[0]
    assert units[0]["target_surface_repairs"][0]["target"] == f"{target}/episodes/3-4"


def test_patch_ledger_rejection_reports_saved_ledger_not_rejected_proposal():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = registry.subject_locator_by_id[1]
    session = HumanCaseSession(
        case_id="CASE-HUMAN-AGENT",
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="open")]
        ),
    )
    args = PatchLedgerToolArgs(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="mapped",
                target=target,
                reason="Invalid because the episode range is missing.",
            )
        ],
        reason="bad patch",
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        args,
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert output["accepted"] is False
    assert complete is False
    assert session.resolution_ledger.rows[0].status == "open"
    assert output["ledger"]["rows"][0]["status"] == "open"
    assert output["units"][0]["unit"] == "LR1"
    assert output["rejected_proposed_row_summaries"][0]["status"] == "mapped"


def test_patch_ledger_repeated_row_rejection_requires_terminal_repair():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = registry.subject_locator_by_id[1]
    session = HumanCaseSession(
        case_id="CASE-HUMAN-AGENT",
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="open")]
        ),
    )
    args = PatchLedgerToolArgs(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="mapped",
                target=target,
                reason="Invalid because the mapped subject is missing an episode span.",
            )
        ],
        reason="bad patch",
    )

    session, first_output, first_complete = _patch_ledger_tool(
        registry,
        session,
        args,
        main_refs=list(workspace.contract.main_file_refs),
    )
    session, second_output, second_complete = _patch_ledger_tool(
        registry,
        session,
        args,
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert first_output["accepted"] is False
    assert first_complete is False
    assert second_output["accepted"] is False
    assert second_complete is False
    assert second_output["repeat_rejection_warning"]["issue"] == "same_ledger_rejection_repeated"
    assert second_output["row_rejection_counts"] == {"LR1": 2}
    unit = second_output["units"][0]
    assert unit["unit"] == "LR1"
    assert unit["row_rejection_count"] == 2
    assert unit["terminal_repair_required"] is True
    assert second_output["blocking_units"][0]["terminal_repair_required"] is True
    assert second_output["repair_frontier"][0]["terminal_repair_required"] is True
    assert any(
        "terminal status" in action
        for action in second_output["repair_frontier"][0]["high_quality_next_actions"]
    )


def test_patch_ledger_saves_valid_rows_when_sibling_row_has_bad_target():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    subject = registry.subject_locator_by_id[1]
    session = HumanCaseSession(
        case_id="CASE-HUMAN-AGENT",
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(row_id="LR1", local=[f"{local}/episode/1"], status="open"),
                ResolutionLedgerRow(row_id="LR2", local=[f"{local}/episode/2"], status="open"),
            ]
        ),
    )
    args = PatchLedgerToolArgs(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[f"{local}/episode/1"],
                status="mapped",
                target=f"{subject}/episode/1",
                reason="Episode 1 closes.",
            ),
            ResolutionLedgerRow(
                row_id="LR2",
                local=[f"{local}/episode/2"],
                status="mapped",
                target=f"{subject}/episode/2 {subject}/episode/1",
                reason="Bad row should not discard LR1.",
            ),
        ],
        reason="partial ledger",
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        args,
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert output["accepted"] is True
    assert output["partial"] is True
    assert complete is False
    assert output["saved_row_count"] == 1
    assert output["rejected_row_ids"] == ["LR2"]
    assert session.resolution_ledger.rows[0].status == "mapped"
    assert session.resolution_ledger.rows[1].status == "open"


def test_resolution_ledger_ignores_free_text_support_without_rejecting_row():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    subject = registry.subject_locator_by_id[1]
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="mapped",
                target=subject,
                mapped_outcome="mapped_regular_span",
                episode_start=1,
                episode_end=2,
                support=["duration/count evidence is strong", subject],
                reason="Visible two-episode target matches the two local files.",
            )
        ]
    )

    issues, rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    assert not issues
    assert rows[0]["support"] == [subject]
    assert rows[0]["ignored_support_issues"]
    assert ledger.rows[0].support == [subject]


def test_resolution_ledger_blocks_mechanical_count_mismatch_before_compile():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episode/1"
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="mapped",
                target=target,
                reason="Two local files cannot map to one ordinary target item.",
            )
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    assert any(issue["issue"] == "ledger_count_mismatch" for issue in issues)
    result = _compile_resolution_ledger_to_submit_result(workspace, registry, ledger)
    assert result.accepted is False


def test_resolution_ledger_count_mismatch_exposes_exact_slice_pairing_repair():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-LEDGER-COUNT-SLICE-PAIRING"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(First King)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Black Hero)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=11,
                title="Gekijouban Soushuuhen FRANCHISE First King",
                name="Gekijouban Soushuuhen FRANCHISE First King",
                name_cn="Gekijouban Soushuuhen FRANCHISE First King",
                eps=1,
                total_episodes=1,
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=12,
                title="Gekijouban Soushuuhen FRANCHISE Black Hero",
                name="Gekijouban Soushuuhen FRANCHISE Black Hero",
                name_cn="Gekijouban Soushuuhen FRANCHISE Black Hero",
                eps=1,
                total_episodes=1,
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Episode 1"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    first_target = f"{registry.subject_locator_by_id[11]}/episode/1"
    second_target = f"{registry.subject_locator_by_id[12]}/episode/1"
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[f"{local}/episode/1", f"{local}/episode/2"],
                status="mapped",
                target=first_target,
                reason="Bad repair tried to map two distinct movie slices to the same single-item target.",
            )
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    issue = next(item for item in issues if item["issue"] == "ledger_count_mismatch")
    assert any(
        option["local"] == f"{local}/episode/2" and option["target"] == second_target
        for option in issue["local_slice_mapping_options"]
    )
    assert issue["suggested_submit_shape"] == [
        {
            **issue["suggested_submit_shape"][0],
            "local": f"{local}/episode/1",
            "target": first_target,
        },
        {
            **issue["suggested_submit_shape"][1],
            "local": f"{local}/episode/2",
            "target": second_target,
        },
    ]


def test_resolution_ledger_fail_closed_target_is_repaired_as_mapping_or_review_hint():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target_subject = registry.subject_locator_by_id[1]
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[f"{local}/episode/1"],
                status="fail_closed",
                target=target_subject,
                reason="Fail closed while still carrying a visible target as a hint.",
            ),
            ResolutionLedgerRow(
                row_id="LR2",
                local=[f"{local}/episode/2"],
                status="mapped",
                mapped_outcome="mapped_explicit_item",
                target=f"{target_subject}/episode/2",
                reason="Second episode maps cleanly.",
            ),
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    issue = next(item for item in issues if item["issue"] == "ledger_fail_closed_target_ignored")
    assert issue["suggested_submit_shape"] == [
        {
            "local": f"{local}/episode/1",
            "target": f"{target_subject}/episode/1",
            "outcome": "mapped_explicit_item",
            "reason": (
                "The fail_closed row already cited this visible target; map it if ownership "
                "is closed, or convert the row to manual_review with this target as a candidate."
            ),
        }
    ]
    assert issue["manual_review_candidate_submit_shape"][0]["manual_review_candidate_targets"] == [target_subject]


def test_resolution_ledger_reject_candidate_needs_contradiction():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episode/1"
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="target_absent",
                reason="No safe target accepted from current evidence.",
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(target=target, discharge="rejected")
                ],
            )
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    assert any(issue["issue"] == "ledger_candidate_debt_open" for issue in issues)


def test_mapped_row_ignores_low_confidence_manual_review_hint_debt():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    subject = registry.subject_locator_by_id[1]
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[f"{local}/episode/1"],
                status="mapped",
                mapped_outcome="mapped_explicit_item",
                target=f"{subject}/episode/1",
                reason="Episode 1 maps directly.",
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(
                        target=f"{subject}/episode/2",
                        source="active_repair_agenda.visible_options.manual_review_candidate_submit_shape",
                        discharge="manual_review",
                        support=[f"{local}/episode/1"],
                        reason="Low-confidence review hint from a visible option.",
                    )
                ],
            ),
            ResolutionLedgerRow(
                row_id="LR2",
                local=[f"{local}/episode/2"],
                status="mapped",
                mapped_outcome="mapped_explicit_item",
                target=f"{subject}/episode/2",
                reason="Episode 2 maps directly.",
            ),
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    assert not [
        issue
        for issue in issues
        if issue["issue"] == "ledger_candidate_manual_review_discharge_missing_target"
    ]


def test_mapped_row_discharges_manual_review_candidate_debt_when_target_matches():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    subject = registry.subject_locator_by_id[1]
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[f"{local}/episode/1"],
                status="mapped",
                mapped_outcome="mapped_special_or_ova",
                target=f"{subject}/episode/1",
                reason="The exact slice maps to the visible candidate subject episode.",
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(
                        target=subject,
                        source="manual_review_strong_non_regular_mapping_should_revise",
                        discharge="manual_review",
                        support=[f"{local}/episode/1"],
                        reason="Stale manual_review debt should be discharged by the mapped row.",
                    )
                ],
            ),
            ResolutionLedgerRow(
                row_id="LR2",
                local=[f"{local}/episode/2"],
                status="mapped",
                mapped_outcome="mapped_special_or_ova",
                target=f"{subject}/episode/2",
                reason="The second exact slice maps directly.",
            ),
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    assert not [
        issue
        for issue in issues
        if issue["issue"].startswith("ledger_candidate_")
        or issue["issue"] == "ledger_strong_candidate_manual_review_requires_contradiction"
    ]


def test_resolution_ledger_strong_repair_candidate_debt_allows_exact_candidate_manual_review():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[f"{local}/episodes/1-2"],
                status="manual_review",
                manual_review_candidate_targets=[target],
                reason="Episode title/count evidence remains unresolved after inspect.",
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(
                        target=target,
                        source="manual_review_strong_non_regular_mapping_should_revise",
                        mapped_outcome="mapped_regular_span",
                        support=[f"{local}/episodes/1-2"],
                        reason="full visible same-count repair",
                    )
                ],
            )
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    assert not [
        issue
        for issue in issues
        if issue["issue"].startswith("ledger_candidate_")
        or issue["issue"] == "ledger_strong_candidate_manual_review_requires_contradiction"
    ]


def test_resolution_ledger_candidate_manual_review_rejects_vague_uncertainty():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="manual_review",
                manual_review_candidate_targets=[target],
                reason="Still ambiguous from current evidence.",
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(
                        target=target,
                        source="manual_review_strong_non_regular_mapping_should_revise",
                        mapped_outcome="mapped_regular_span",
                        support=[local],
                        reason="full visible same-count repair",
                    )
                ],
            )
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    assert any(
        issue["issue"] == "ledger_candidate_debt_open"
        for issue in issues
    )


def test_resolution_ledger_mapped_discharge_blocker_allows_candidate_manual_review():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[f"{local}/episodes/1-2"],
                status="manual_review",
                manual_review_candidate_targets=[target],
                reason="Target episode range evidence remains unresolved after inspect.",
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(
                        target=target,
                        source="active_repair_agenda",
                        mapped_outcome="mapped_regular_span",
                        discharge="manual_review",
                        blocker="ledger_candidate_mapped_discharge_mismatch",
                        support=[f"{local}/episodes/1-2"],
                        reason="Visible repair agenda asked for a mapped discharge.",
                    )
                ],
            )
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    assert not [
        issue
        for issue in issues
        if issue["issue"].startswith("ledger_candidate_")
        or issue["issue"] == "ledger_strong_candidate_manual_review_requires_contradiction"
    ]


def test_resolution_ledger_manual_review_discharge_missing_target_keeps_mapped_shape():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="manual_review",
                reason="Still unresolved from current evidence.",
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(
                        target=target,
                        source="inspect related surface",
                        mapped_outcome="mapped_special_or_ova",
                        discharge="manual_review",
                        blocker="ledger_candidate_manual_review_discharge_missing_target",
                        support=[f"{local}/episodes/1-2"],
                        reason="Visible suggested target should be mapped or concretely contradicted.",
                    )
                ],
            )
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    issue = next(
        item for item in issues
        if item["issue"] == "ledger_candidate_manual_review_discharge_missing_target"
    )
    assert issue["suggested_submit_shape"] == [
            {
                "local": f"{local}/episodes/1-2",
                "target": target,
                "outcome": "mapped_special_or_ova",
                "reason": (
                    "Map this candidate if ownership is closed, or carry it in "
                    "manual_review_candidate_targets with localized uncertainty."
                ),
            }
        ]


def test_resolution_ledger_fail_closed_sibling_blocker_allows_candidate_manual_review():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episode/1"
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[f"{local}/episode/1"],
                status="manual_review",
                manual_review_candidate_targets=[target],
                reason="Sibling episode ownership conflict remains unresolved after mapped-neighbor review.",
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(
                        target=target,
                        source="compiled_submit_feedback",
                        mapped_outcome="mapped_explicit_item",
                        discharge="manual_review",
                        blocker="fail_closed_with_mapped_sibling",
                        support=[f"{local}/episode/1"],
                        reason="Visible sibling repair candidate should be mapped or contradicted.",
                    )
                ],
            )
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=["LF1"],
        require_terminal=True,
    )

    assert not [
        issue
        for issue in issues
        if issue["issue"].startswith("ledger_candidate_")
        or issue["issue"] == "ledger_strong_candidate_manual_review_requires_contradiction"
    ]


def test_resolution_ledger_strong_numbered_candidate_reject_needs_concrete_contradiction():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="supplemental",
                reason="The local files are SP bonus material.",
                must_address_candidates=[
                    ResolutionLedgerCandidateDebt(
                        target=target,
                        source="numbered_special_exclusion_needs_target_evidence",
                        mapped_outcome="mapped_special_or_ova",
                        discharge="rejected",
                        contradiction="This is bonus material and should not be forced into a regular span.",
                        support=[local],
                        reason="visible same-count numbered special owner",
                    )
                ],
            )
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    assert any(
        issue["issue"] == "ledger_strong_candidate_rejected_requires_contradiction"
        for issue in issues
    )


def test_resolution_ledger_moves_manual_review_target_to_candidate_targets():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = registry.subject_locator_by_id[1]
    ledger = ResolutionLedger(
        rows=[
            ResolutionLedgerRow(
                row_id="LR1",
                local=[local],
                status="manual_review",
                target="target://bangumi/1",
                reason="Keep this as a review hint rather than an accepted mapping.",
            )
        ]
    )

    issues, _rows = _validate_resolution_ledger(
        registry,
        ledger,
        main_refs=list(workspace.contract.main_file_refs),
        require_terminal=True,
    )

    assert not issues
    assert ledger.rows[0].target == ""
    assert ledger.rows[0].manual_review_candidate_targets == [target]


def test_subject_card_from_api_compacts_infobox_alias_facts():
    subject = BangumiSubject(
        id=200,
        name="星团短篇",
        name_cn="星团短篇",
        eps=1,
        total_episodes=1,
        infobox=[
            {"key": "别名", "value": [{"v": "Companion Stars"}, {"v": "Franchise Companion"}]},
            {"key": "官方网站", "value": "https://example.invalid"},
        ],
    )

    card = _subject_card_from_api(subject, "BS1")

    assert "别名: Companion Stars / Franchise Companion" in card.infobox_facts
    assert all("example.invalid" not in fact for fact in card.infobox_facts)


def test_infobox_alias_facts_are_visible_target_markers_without_auto_choice():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-INFOBOX-ALIAS-BRIDGE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1"],
            allowed_file_refs=["LF1"],
            visible_target_refs=["BE1"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] FRANCHISE Companion Stars.mkv",
                is_main=True,
            )
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=201,
                title="星团短篇",
                name="星团短篇",
                name_cn="星团短篇",
                eps=1,
                total_episodes=1,
                infobox_facts=["别名: Companion Stars"],
            )
        ],
        bangumi_items=[BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1")],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    subject_locator = registry.subject_locator_by_id[201]

    assert "Companion Stars" in registry.locators[subject_locator].markers

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="agent-chosen-companion-stars",
                        local=[desk["local_locators"][0]["locator"]],
                        outcome="mapped_explicit_item",
                        target=f"{subject_locator}/episode/1",
                    )
                ]
            )
        ),
    )

    assert result.accepted is True


def test_search_merges_source_query_provenance_for_new_subject_seen_twice():
    class FakeBangumiClient:
        def search_subjects(self, query: str):
            return [
                BangumiSubject(
                    id=301,
                    name="Provider Title",
                    name_cn="Provider Title",
                    eps=1,
                    total_episodes=1,
                    search_keyword=query,
                    search_rank=1,
                )
            ]

    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SEARCH-PROVENANCE-MERGE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(),
    )
    registry = build_human_case_desk(workspace)[1]

    updated_workspace, _obs = _search_tool(
        workspace,
        registry,
        FakeBangumiClient(),
        SearchToolArgs(queries=["Broad Franchise", "Exact Alias"]),
        seen_variant_keys=set(),
    )

    assert len(updated_workspace.bangumi_subjects) == 1
    subject = updated_workspace.bangumi_subjects[0]
    assert "Broad Franchise" in subject.search_query_ref
    assert "Exact Alias" in subject.search_query_ref
    target = registry.locators[registry.subject_locator_by_id[301]]
    assert any("Exact Alias" in marker for marker in target.query_markers)


def test_inspect_related_filters_non_anime_but_keeps_unknown_anime_relations():
    class FakeBangumiClient:
        def get_related_subjects(self, subject_id: int):
            return [
                BangumiSubjectRelation(id=401, type=3, relation="\u756a\u5916\u7bc7", name="Opening Song"),
                BangumiSubjectRelation(id=402, type=2, relation="\u7247\u5934\u66f2", name="Opening Song"),
                BangumiSubjectRelation(id=403, type=2, relation="\u6e38\u620f", name="Game Entry"),
                BangumiSubjectRelation(id=404, type=2, relation="\u756a\u5916\u7bc7", name="Side Story"),
            ]

        def get_subject(self, subject_id: int):
            if subject_id in {403, 404}:
                return BangumiSubject(
                    id=subject_id,
                    type=2,
                    name="Game Entry" if subject_id == 403 else "Side Story",
                    name_cn="Game Entry" if subject_id == 403 else "Side Story",
                    eps=1,
                    total_episodes=1,
                )
            return BangumiSubject(id=subject_id, type=3, name="Filtered", name_cn="Filtered")

    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-RELATED-FILTER"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(visible_target_refs=["BS1"]),
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=400,
                title="Main Title",
                name="Main Title",
                name_cn="Main Title",
                eps=12,
                total_episodes=12,
                search_query_ref="Main Title Side Story",
            )
        ],
    )
    _desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    subject_locator = registry.subject_locator_by_id[400]

    updated, observation = _inspect_tool(
        workspace,
        registry,
        FakeBangumiClient(),
        InspectToolArgs(locators=[subject_locator], scope=["related"]),
    )

    related = observation["observations"][0]["related"]
    assert [item["target"] for item in related] == [
        "target://bangumi/403-game-entry",
        "target://bangumi/404-side-story",
    ]
    assert observation["observations"][0]["related_skipped"] == {
        "non_anime_detail": 1,
        "non_anime_type": 1,
    }
    assert 404 in registry.subject_locator_by_id
    assert 403 in registry.subject_locator_by_id
    assert 401 not in registry.subject_locator_by_id
    assert 402 not in registry.subject_locator_by_id
    assert [card.subject_id for card in updated.bangumi_subjects] == [400, 403, 404]


def test_unknown_target_locator_feedback_suggests_visible_query_provenance_candidates():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-TARGET-CANDIDATE-FEEDBACK"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1"],
            allowed_file_refs=["LF1"],
            visible_target_refs=["BE1"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] OVERLORD Ple Ple Pleiades.mkv",
                is_main=True,
            )
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=193953,
                title="Play Play Stars",
                name="Play Play Stars",
                name_cn="Play Play Stars",
                eps=1,
                total_episodes=1,
                search_query_ref="OVERLORD Ple Ple Pleiades",
            )
        ],
        bangumi_items=[BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1")],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="invalid-target",
                        local=[desk["local_locators"][0]["locator"]],
                        outcome="mapped_explicit_item",
                        target="target://bangumi/2784-overlord-ple-ple-pleiades",
                    )
                ]
            )
        ),
    )

    issue = result.feedback["units"][0]["issues"][0]
    assert issue["issue"] == "locator_not_found"
    assert issue["candidate_target_locators"][0]["target"].startswith("target://bangumi/193953-")
    assert issue["candidate_target_locators"][0]["matched_source_query_texts"] == ["OVERLORD Ple Ple Pleiades"]


def test_related_query_provenance_bridge_targets_are_prioritized_without_auto_choice():
    registry = LocatorRegistry()
    local = AgentLocator(
        locator="local://franchise-companion/main",
        kind="local",
        title="Franchise Companion Short",
        representative_labels=("Franchise Companion Short.mkv",),
    )
    registry.add(local)
    registry.add(
        AgentLocator(
            locator="target://bangumi/1-franchise",
            kind="target_subject",
            title="Franchise",
            subject_id=1,
            subject_eps=12,
            query_markers=("Franchise Companion Short",),
            search_rank=1,
        )
    )
    registry.add(
        AgentLocator(
            locator="target://bangumi/2-related-short",
            kind="target_subject",
            title="Related Short",
            subject_id=2,
            subject_eps=1,
            query_markers=("Franchise Companion Short",),
            search_rank=1,
            source_role="related_from_query_subject",
            relation_to_main="prequel",
            relation_path_refs=("BS1",),
        )
    )

    rows = _visible_source_query_bridge_targets(
        registry,
        local,
        local,
        {"franchise", "companion", "short"},
    )

    assert rows[0]["target"] == "target://bangumi/2-related-short"
    assert rows[0]["relevance_layer"] == "related_source_query_bridge"
    assert rows[0]["relation_to_query_subject"] == "prequel"
    assert rows[0]["relation_quality"] == "owner_relevant_related"
    assert rows[0]["title_bridge_quality"] == "title_or_alias_overlap"
    assert rows[0]["answers_current_blocker"] is True
    assert "target://bangumi/1-franchise" in [row["target"] for row in rows]


def test_unknown_related_query_bridge_targets_are_diagnostic_not_filtered():
    registry = LocatorRegistry()
    local = AgentLocator(
        locator="local://franchise-companion/main",
        kind="local",
        title="Franchise Companion Short",
        representative_labels=("Franchise Companion Short.mkv",),
    )
    registry.add(local)
    registry.add(
        AgentLocator(
            locator="target://bangumi/1-franchise",
            kind="target_subject",
            title="Franchise",
            subject_id=1,
            subject_eps=12,
            query_markers=("Franchise Companion Short",),
            search_rank=1,
        )
    )
    registry.add(
        AgentLocator(
            locator="target://bangumi/2-opening-song",
            kind="target_subject",
            title="Opening Song",
            subject_id=2,
            subject_eps=4,
            query_markers=("Franchise Companion Short",),
            search_rank=1,
            source_role="related_from_query_subject",
            relation_to_main="\u7247\u5934\u66f2",
            relation_path_refs=("BS1",),
        )
    )

    rows = _visible_source_query_bridge_targets(
        registry,
        local,
        local,
        {"franchise", "companion", "short"},
    )

    opening_row = next(row for row in rows if row["target"] == "target://bangumi/2-opening-song")
    assert opening_row["relation_quality"] == "weak_related"
    assert opening_row["answers_current_blocker"] is False
    assert "target://bangumi/1-franchise" in [row["target"] for row in rows]


def test_weak_related_source_query_bridge_is_diagnostic_not_blocking_repair_action():
    feedback = {
        "package": {
            "issue_counts": {"fail_closed_title_tail_bridge_uninspected": 1},
            "fail_closed_title_tail_bridge_repairs": [
                {
                    "issue": "fail_closed_title_tail_bridge_uninspected",
                    "local": ["local://franchise-companion/main"],
                    "visible_source_query_bridge_targets": [
                        {
                            "target": "target://bangumi/2-related-short",
                            "target_subject": "target://bangumi/2-related-short",
                            "target_title": "Related Short",
                            "available_action": 'inspect(["target://bangumi/2-related-short"], scope=["details","episodes","related"])',
                            "relation_quality": "weak_related",
                            "title_bridge_quality": "source_query_related_only",
                            "answers_current_blocker": False,
                        }
                    ],
                }
            ],
        },
        "units": [],
    }

    agenda = _repair_agenda_from_submit_feedback(feedback, repeated=False)

    assert agenda["blocking_target_surface_actions"] == []
    assert _target_surface_actions_from_repair(agenda) == []
    assert agenda["diagnostic_target_surface_actions"][0]["relation_quality"] == "weak_related"
    assert agenda["recovery_no_high_quality_action"] is True


def test_allowed_related_without_title_or_shape_support_stays_diagnostic():
    registry = LocatorRegistry()
    local = AgentLocator(
        locator="local://franchise-companion/main",
        kind="local",
        title="Franchise Companion Short",
        representative_labels=("Franchise Companion Short.mkv",),
        file_refs=("LF1",),
    )
    registry.add(local)
    registry.add(
        AgentLocator(
            locator="target://bangumi/2-side-story",
            kind="target_subject",
            title="Side Story",
            subject_id=2,
            subject_eps=12,
            query_markers=("Franchise Companion Short",),
            search_rank=1,
            source_role="related_from_query_subject",
            relation_to_main="side_story",
            relation_quality="owner_relevant_related",
            relation_path_refs=("BS1",),
        )
    )

    rows = _visible_source_query_bridge_targets(
        registry,
        local,
        local,
        {"franchise", "companion", "short"},
    )
    feedback = {
        "package": {
            "issue_counts": {"fail_closed_title_tail_bridge_uninspected": 1},
            "fail_closed_title_tail_bridge_repairs": [
                {
                    "issue": "fail_closed_title_tail_bridge_uninspected",
                    "local": [local.locator],
                    "visible_source_query_bridge_targets": rows,
                }
            ],
        },
        "units": [],
    }

    agenda = _repair_agenda_from_submit_feedback(feedback, repeated=False)

    assert rows[0]["relation_quality"] == "owner_relevant_related"
    assert rows[0]["title_bridge_quality"] == "source_query_related_only"
    assert rows[0]["answers_current_blocker"] is False
    assert agenda["blocking_target_surface_actions"] == []
    assert agenda["diagnostic_target_surface_actions"]

    repairs = _excluded_title_tail_unresolved_after_search_repairs(
        registry,
        [
            {
                "unit": "Franchise Companion Short",
                "local": [local.locator],
                "outcome": "fail_closed",
            }
        ],
        searched_query_variant_keys={
            variant.casefold()
            for query in ("Franchise Companion Short", "Companion Short", "FranchiseCompanionShort")
            for variant in _search_query_variants(query)
        },
        allowed_outcomes={"fail_closed"},
        issue_code="fail_closed_title_tail_bridge_uninspected",
        require_uninspected_bridge_target=True,
    )
    assert repairs == []


def test_allowed_related_with_episode_shape_support_can_be_blocking_action():
    registry = LocatorRegistry()
    local = AgentLocator(
        locator="local://franchise-companion/main",
        kind="local",
        title="Franchise Companion Short",
        representative_labels=("Franchise Companion Short.mkv",),
        file_refs=("LF1",),
    )
    registry.add(local)
    registry.add(
        AgentLocator(
            locator="target://bangumi/2-side-story",
            kind="target_subject",
            title="Side Story",
            subject_id=2,
            subject_eps=1,
            query_markers=("Franchise Companion Short",),
            search_rank=1,
            source_role="related_from_query_subject",
            relation_to_main="side_story",
            relation_quality="owner_relevant_related",
            relation_path_refs=("BS1",),
        )
    )
    registry.add(
        AgentLocator(
            locator="target://bangumi/2-side-story/episode/1",
            kind="target_episode",
            title="Episode 1",
            subject_id=2,
            item_refs=("BE1",),
            episode_start=1,
            episode_end=1,
        )
    )

    rows = _visible_source_query_bridge_targets(
        registry,
        local,
        local,
        {"franchise", "companion", "short"},
    )
    feedback = {
        "package": {
            "issue_counts": {"fail_closed_title_tail_bridge_uninspected": 1},
            "fail_closed_title_tail_bridge_repairs": [
                {
                    "issue": "fail_closed_title_tail_bridge_uninspected",
                    "local": [local.locator],
                    "visible_source_query_bridge_targets": rows,
                }
            ],
        },
        "units": [],
    }

    agenda = _repair_agenda_from_submit_feedback(feedback, repeated=False)

    assert rows[0]["episode_shape_support"] is True
    assert rows[0]["answers_current_blocker"] is True
    assert agenda["blocking_target_surface_actions"] == [
        'inspect(["target://bangumi/2-side-story"], scope=["details","episodes","related"])'
    ]


def test_title_tail_unresolved_repair_adds_generic_root_frontier_without_target_choice():
    registry = LocatorRegistry()
    local = AgentLocator(
        locator="local://azure-tail/main",
        kind="local",
        title="Azure Chronicle Tail",
        representative_labels=("Azure Chronicle Tail.mkv",),
        file_refs=("LF1",),
    )
    registry.add(local)
    registry.add(
        AgentLocator(
            locator="target://bangumi/20-azure-chronicle-2",
            kind="target_subject",
            title="Azure Chronicle 2",
            subject_id=20,
            subject_eps=1,
            query_markers=("Azure Chronicle Tail",),
            search_rank=1,
        )
    )
    registry.add(
        AgentLocator(
            locator="target://bangumi/20-azure-chronicle-2/episode/1",
            kind="target_episode",
            title="Episode 1",
            subject_id=20,
            episode_start=1,
            episode_end=1,
        )
    )
    searched = {
        variant.casefold()
        for query in ("Azure Chronicle Tail",)
        for variant in _search_query_variants(query)
    }

    repairs = _excluded_title_tail_unresolved_after_search_repairs(
        registry,
        [
            {
                "unit": "Azure Chronicle Tail",
                "local": [local.locator],
                "outcome": "fail_closed",
            }
        ],
        searched_query_variant_keys=searched,
        allowed_outcomes={"fail_closed"},
        issue_code="fail_closed_title_tail_bridge_uninspected",
    )

    assert repairs
    assert "Azure Chronicle" in repairs[0]["root_owner_search_queries_to_try"]
    assert "Azure Chronicle" in repairs[0]["search_queries_to_try"]
    assert repairs[0]["visible_source_query_bridge_targets"][0]["answers_current_blocker"] is True


def test_submit_rejection_observation_compacts_to_repair_agenda():
    workspace = _two_episode_workspace()
    _desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(resolution=PackageResolution(work_units=[])),
    )

    agenda = _repair_agenda_from_submit_feedback(result.feedback, repeated=False)
    assert result.accepted is False
    assert agenda["status"] == "repair_required"
    assert agenda["required_missing_work_units"][0]["local"] == ["local://show/main-episodes"]
    assert "units" not in agenda


def test_submit_repair_agenda_surfaces_target_inspect_actions():
    feedback = {
        "package": {"issue_counts": {"target_episode_surface_missing": 1}},
        "units": [
            {
                "unit": "needs-surface",
                "local": ["local://show/main-episodes"],
                "issues": [
                    {
                        "issue": "target_episode_surface_missing",
                        "target": "target://bangumi/1-show/episode/1",
                        "available_action": 'inspect(["target://bangumi/1-show"], scope=["details","episodes","related"])',
                    }
                ],
            }
        ],
    }

    agenda = _repair_agenda_from_submit_feedback(feedback, repeated=False)

    assert agenda["target_surface_actions"] == [
        'inspect(["target://bangumi/1-show"], scope=["details","episodes","related"])'
    ]
    assert "target_surface_actions" in agenda["required_next_action"]


def test_visible_target_surface_missing_episode_does_not_request_reinspect():
    workspace = _two_episode_workspace()
    _desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="missing-third",
                        local=["local://show/main-episodes/episode/1"],
                        outcome="mapped_explicit_item",
                        target="target://bangumi/1-show/episode/3",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    unit_issue = result.feedback["units"][0]["issues"][0]
    assert unit_issue["issue"] == "target_episode_surface_missing"
    assert unit_issue["target_surface_visible"] is True
    assert not str(unit_issue["available_action"]).startswith("inspect(")

    agenda = _repair_agenda_from_submit_feedback(result.feedback, repeated=False)
    assert agenda["target_surface_actions"] == []
    assert agenda["visible_target_surface_missing_units"][0]["target"] == "target://bangumi/1-show/episode/3"
    assert "do not retry" in agenda["required_next_action"]


def test_visible_target_surface_missing_after_regular_max_exposes_continuation_search_queries():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-CONTINUATION-SURFACE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF11", "LF12", "LF13", "LF14"],
            allowed_file_refs=["LF11", "LF12", "LF13", "LF14"],
            visible_target_refs=[f"BE{i}" for i in range(1, 11)],
        ),
        local_files=[
            LocalFileCard(ref=f"LF{i}", path=f"Azure Chronicle/Azure Chronicle - {i:02d}.mkv", is_main=True)
            for i in range(11, 15)
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=100,
                title="Azure Chronicle",
                name="Azure Chronicle",
                name_cn="Azure Chronicle",
                eps=10,
                total_episodes=10,
                search_query_ref="Azure Chronicle",
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{i}", subject_ref="BS1", sort=i, ep=i, title=f"Episode {i}")
            for i in range(1, 11)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    subject_locator = registry.subject_locator_by_id[100]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="Azure Chronicle later episodes",
                        local=[local_locator],
                        outcome="mapped_regular_span",
                        target=f"{subject_locator}/episodes/11-14",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    issue = result.feedback["units"][0]["issues"][0]
    assert issue["issue"] == "target_episode_surface_missing"
    assert issue["target_surface_visible"] is True
    assert issue["continuation_evidence_hint"]["visible_regular_episode_max"] == 10
    assert issue["continuation_evidence_hint"]["requested_episode_start"] == 11
    assert "Azure Chronicle part 2" in issue["search_queries_to_try"]

    agenda = _repair_agenda_from_submit_feedback(result.feedback, repeated=False)
    assert agenda["target_surface_actions"] == []
    assert "Azure Chronicle second season" in agenda["search_queries_to_try"]
    visible_unit = agenda["visible_target_surface_missing_units"][0]
    assert visible_unit["target"] == f"{subject_locator}/episodes/11-14"
    assert "This hint does not choose a target" in visible_unit["continuation_evidence_hint"]["required"]
    assert "title-preserving continuation/part/cour search" in agenda["required_next_action"]


def test_duplicate_target_feedback_is_mechanical_not_semantic_choice():
    workspace = _two_episode_workspace()
    _desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="episode-one",
                        local=["local://show/main-episodes/episode/1"],
                        outcome="mapped_explicit_item",
                        target="target://bangumi/1-show/episode/1",
                    ),
                    ResolutionWorkUnit(
                        unit_label="episode-two-wrong-target",
                        local=["local://show/main-episodes/episode/2"],
                        outcome="mapped_explicit_item",
                        target="target://bangumi/1-show/episode/1",
                    ),
                ]
            )
        ),
    )

    agenda = _repair_agenda_from_submit_feedback(result.feedback, repeated=False)
    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"]["duplicate_target"] == 1
    assert agenda["duplicate_target_details"]
    assert "only one conflicting unit may keep that target item" in agenda["required_next_action"]
    assert "The fixed layer is only checking mechanics" in agenda["required_next_action"]


def test_count_mismatch_repair_prioritizes_split_and_title_tail_search():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SPLIT-FIRST"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Movie Pack/Movie Pack [01(First Arc)].mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Movie Pack/Movie Pack [02(Second Arc)].mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=10,
                title="Movie Pack First Arc",
                name="Movie Pack First Arc",
                name_cn="Movie Pack First Arc",
                eps=1,
                total_episodes=1,
                search_query_ref="Movie Pack First Arc",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=11,
                title="Movie Pack Second Arc",
                name="Movie Pack Second Arc",
                name_cn="Movie Pack Second Arc",
                eps=1,
                total_episodes=1,
                search_query_ref="Movie Pack Second Arc",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="First Arc"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Second Arc"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="movie-parent-to-one-target",
                        local=[local_locator],
                        outcome="mapped_explicit_item",
                        target="target://bangumi/10-movie-pack-first-arc/episode/1",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    unit = result.feedback["units"][0]
    assert unit["issue"] == "count_mismatch"
    assert unit["split_first_repair"]["legal_local_split_locators"]
    assert f"{local_locator}/episode/1" in {
        row["locator"] for row in unit["split_first_repair"]["legal_local_split_locators"]
    }
    assert f"{local_locator}/episode/2" in {
        row["locator"] for row in unit["split_first_repair"]["legal_local_split_locators"]
    }
    assert "First Arc" in unit["search_queries_to_try"]
    assert "Second Arc" in unit["search_queries_to_try"]

    agenda = _repair_agenda_from_submit_feedback(result.feedback, repeated=False)
    blocking = agenda["blocking_units"][0]
    assert blocking["issue"] == "count_mismatch"
    assert blocking["split_first_repair"]["legal_local_split_locators"]
    assert "Second Arc" in agenda["search_queries_to_try"]
    assert len(blocking.get("visible_alternate_subjects") or []) <= 3
    assert "split the local parent" in agenda["required_next_action"]


def test_ledger_coverage_missing_exposes_title_pairing_patch_shape():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-LEDGER-MISSING-TITLE-PAIRING"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Movie Pack/Movie Pack [01(First Arc)].mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Movie Pack/Movie Pack [02(Second Arc)].mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=10,
                title="Movie Pack First Arc",
                name="Movie Pack First Arc",
                name_cn="Movie Pack First Arc",
                eps=1,
                total_episodes=1,
                search_query_ref="Movie Pack First Arc",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=11,
                title="Movie Pack Second Arc",
                name="Movie Pack Second Arc",
                name_cn="Movie Pack Second Arc",
                eps=1,
                total_episodes=1,
                search_query_ref="Movie Pack Second Arc",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="First Arc"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Second Arc"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]

    result = _compile_resolution_ledger_to_submit_result(
        workspace,
        registry,
        ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local_locator}/episode/2"],
                    status="mapped",
                    mapped_outcome="mapped_explicit_item",
                    target="target://bangumi/11-movie-pack-second-arc/episode/1",
                    reason="Second movie slice is already resolved.",
                )
            ]
        ),
    )

    assert result.accepted is False
    issue = next(item for item in result.feedback["issues"] if item["issue"] == "ledger_coverage_missing")
    assert issue["file_refs"] == ["LF1"]
    assert issue["candidate_local_locators"][0]["locator"] == f"{local_locator}/episode/1"
    assert issue["suggested_submit_shape"][0]["local"] == f"{local_locator}/episode/1"
    assert issue["suggested_submit_shape"][0]["target"] == "target://bangumi/10-movie-pack-first-arc/episode/1"


def test_single_file_to_multi_item_target_repair_exposes_item_options():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SINGLE-FILE-ITEM-OPTIONS"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1"],
            allowed_file_refs=["LF1"],
            visible_target_refs=["BE1", "BE2", "BE3"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Companion Short/Companion Short.mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=20,
                title="Companion Short",
                name="Companion Short",
                name_cn="Companion Short",
                eps=3,
                total_episodes=3,
                search_query_ref="Companion Short",
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref="BS1", sort=index, ep=index, title=f"Part {index}")
            for index in range(1, 4)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    subject_locator = registry.subject_locator_by_id[20]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="single-file-to-whole-subject",
                        local=[desk["local_locators"][0]["locator"]],
                        outcome="mapped_regular_span",
                        target=f"{subject_locator}/episodes/1-3",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    unit = result.feedback["units"][0]
    assert unit["issue"] == "count_mismatch"
    assert [row["target"] for row in unit["single_file_target_item_options"]] == [
        f"{subject_locator}/episode/1",
        f"{subject_locator}/episode/2",
        f"{subject_locator}/episode/3",
    ]
    assert "target://.../episode/N item" in " ".join(unit["actionable_options"])

    agenda = _repair_agenda_from_submit_feedback(result.feedback, repeated=False)
    blocking = agenda["blocking_units"][0]
    assert blocking["single_file_target_item_options"][0]["target"] == f"{subject_locator}/episode/1"
    assert "single_file_target_item_options" in agenda["required_next_action"]


def test_singleton_compilation_cannot_use_episode_one_as_subject_proxy():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SINGLETON-COMPILATION-PROXY"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1"],
            allowed_file_refs=["LF1"],
            visible_target_refs=[f"BE{index}" for index in range(1, 9)],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Companion Short/Companion Short Complete.mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=21,
                title="Companion Short",
                name="Companion Short",
                name_cn="Companion Short",
                eps=8,
                total_episodes=8,
                search_query_ref="Companion Short",
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref="BS1", sort=index, ep=index, title=f"Part {index}")
            for index in range(1, 9)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    subject_locator = registry.subject_locator_by_id[21]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="singleton-compilation-to-episode-one",
                        local=[desk["local_locators"][0]["locator"]],
                        outcome="mapped_explicit_item",
                        target=f"{subject_locator}/episode/1",
                        reason="single local file is the visible companion short package",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    unit = result.feedback["units"][0]
    assert unit["issue"] == "single_file_multi_episode_subject_item"
    assert unit["target_subject_episode_count"] == 8
    assert "cannot use one episode item" in unit["required"]
    assert any("mapped_composite_feature" in option for option in unit["actionable_options"])
    assert any("manual_review" in option for option in unit["actionable_options"])

    supplemental_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="singleton-compilation-supplemental",
                        local=[desk["local_locators"][0]["locator"]],
                        outcome="supplemental",
                        support=[subject_locator],
                        reason="Single standalone companion material, not part of the main episode span.",
                    )
                ]
            )
        ),
    )

    assert supplemental_result.accepted is False
    package = supplemental_result.feedback["package"]
    assert (
        package["excluded_singleton_visible_subject_repairs"]
        or package["excluded_visible_title_pairing_repairs"]
    )

    manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="singleton-compilation-manual",
                        local=[desk["local_locators"][0]["locator"]],
                        outcome="manual_review",
                        reason="May be a whole-series compilation; current evidence does not prove the covered episode span.",
                    )
                ]
            )
        ),
    )

    assert manual_result.accepted is True
    assert manual_result.output is not None
    assert manual_result.output.action == "submit_verdict"
    assert manual_result.feedback["manual_review_file_count"] == 1
    assignment = manual_result.output.assignment_intents[0]
    assert assignment.target_ref == "UNALIGNED"
    assert ":manual_review:" in assignment.reason


def test_singleton_explicit_episode_can_map_to_episode_one_of_multi_episode_subject():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SINGLETON-EXPLICIT-EPISODE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1"],
            allowed_file_refs=["LF1"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Companion Short/Companion Short [01].mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=22,
                title="Companion Short",
                name="Companion Short",
                name_cn="Companion Short",
                eps=2,
                total_episodes=2,
                search_query_ref="Companion Short",
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Part 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS1", sort=2, ep=2, title="Part 2"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    subject_locator = registry.subject_locator_by_id[22]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="singleton-episode-one",
                        local=[desk["local_locators"][0]["locator"]],
                        outcome="mapped_explicit_item",
                        target=f"{subject_locator}/episode/1",
                    )
                ]
            )
        ),
    )

    assert result.accepted is True
    assert result.output is not None
    assert result.output.assignment_intents[0].target_ref == "BE1"


def test_singleton_mapping_with_only_broad_franchise_overlap_requires_title_tail_bridge():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SINGLETON-BROAD-FRANCHISE-BRIDGE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1"],
            allowed_file_refs=["LF1"],
            visible_target_refs=["BE1"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Franchise Companion Short/[Group] Franchise Companion Short.mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=23,
                title="Franchise OAD",
                name="Franchise OAD",
                name_cn="Franchise OAD",
                eps=1,
                total_episodes=1,
                search_query_ref="Franchise Companion Short",
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="OAD"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    subject_locator = registry.subject_locator_by_id[23]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="singleton-broad-franchise-map",
                        local=[desk["local_locators"][0]["locator"]],
                        outcome="mapped_special_or_ova",
                        target=subject_locator,
                        reason="Single companion short is a special extra for the broad franchise.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"]["mapped_singleton_broad_title_bridge_missing"] == 1
    repair = result.feedback["package"]["mapped_target_title_bridge_repairs"][0]
    assert repair["issue"] == "mapped_singleton_broad_title_bridge_missing"
    assert repair["target_title_bridge_tokens"] == ["franchise"]
    assert set(repair["unbridged_local_title_tokens"]) >= {"companion", "short"}
    assert repair["manual_review_candidate_submit_shape"] == []
    assert subject_locator in repair["do_not_retry_targets_without_new_evidence"]
    assert "broad franchise overlap alone is not enough" in repair["required"]


def test_singleton_broad_bridge_review_shape_uses_independent_visible_candidate():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SINGLETON-BROAD-BRIDGE-INDEPENDENT-CANDIDATE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1"],
            allowed_file_refs=["LF1"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Franchise Companion Short/[Group] Franchise Companion Short.mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=23,
                title="Franchise OAD",
                name="Franchise OAD",
                name_cn="Franchise OAD",
                eps=1,
                total_episodes=1,
                search_query_ref="Franchise Companion Short",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=24,
                title="Franchise Companion Short Collection",
                name="Franchise Companion Short Collection",
                name_cn="Franchise Companion Short Collection",
                eps=8,
                total_episodes=8,
                search_query_ref="Franchise Companion Short",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="OAD"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Companion 1"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    rejected_subject = registry.subject_locator_by_id[23]
    independent_subject = registry.subject_locator_by_id[24]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="singleton-broad-franchise-map",
                        local=[desk["local_locators"][0]["locator"]],
                        outcome="mapped_special_or_ova",
                        target=rejected_subject,
                        reason="Single companion short is a special extra for the broad franchise.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    repair = result.feedback["package"]["mapped_target_title_bridge_repairs"][0]
    shape = repair["manual_review_candidate_submit_shape"][0]
    assert rejected_subject not in shape["manual_review_candidate_targets"]
    assert any(
        str(candidate).startswith(independent_subject)
        for candidate in shape["manual_review_candidate_targets"]
    )


def test_mapped_exact_slice_with_visible_title_pairing_skips_broad_singleton_bridge_repair():
    target_title = "\u5287\u5834\u7248\u7dcf\u96c6\u7de8 FRANCHISE \u9ed2\u306e\u52c7\u8005"
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SLICE-PAIRING-SKIPS-BROAD-BRIDGE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(First King)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Black Hero)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=24,
                title=target_title,
                name=target_title,
                name_cn=target_title,
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE Black Hero",
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    subject_locator = registry.subject_locator_by_id[24]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="first-slice-review",
                        local=[f"{local}/episode/1"],
                        outcome="manual_review",
                        reason="first slice unresolved in this unit test",
                    ),
                    ResolutionWorkUnit(
                        unit_label="second-slice-map",
                        local=[f"{local}/episode/2"],
                        outcome="mapped_explicit_item",
                        target=f"{subject_locator}/episode/1",
                        reason="AI accepts the visible slice title-pairing evidence for this movie part.",
                    ),
                ]
            )
        ),
    )

    package = result.feedback.get("package") if isinstance(result.feedback, dict) else None
    issue_counts = package.get("issue_counts") if isinstance(package, dict) else {}
    assert "mapped_singleton_broad_title_bridge_missing" not in issue_counts


def test_numbered_sp_related_same_count_subject_prefers_manual_review_over_supplemental():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-NUMBERED-SP-RELATED-SAME-COUNT"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3"],
            allowed_file_refs=["LF1", "LF2", "LF3"],
            visible_target_refs=["BE1", "BE2", "BE3"],
        ),
        local_files=[
            LocalFileCard(
                ref=f"LF{index}",
                path=f"Pack/SPs/[Group] Franchise II [SP{index:02d}].mkv",
                is_main=True,
                subtitle_facts={
                    "external_subtitle_refs": [
                        {
                            "file_id": f"sub_{index:03d}",
                            "relative_path": f"Pack/SPs/[Group] Franchise II [SP{index:02d}].chs.ass",
                        }
                    ],
                    "language_markers": ["chs"],
                    "bounded_text_snippets": [
                        {
                            "source_ref": f"sub_{index:03d}",
                            "text": f"Play2 anchor {index}",
                        }
                    ],
                    "snippet_source": "external_subtitle_file",
                },
            )
            for index in range(1, 4)
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=61,
                title="Franchise II",
                name="Franchise II",
                name_cn="Franchise II",
                eps=13,
                total_episodes=13,
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=62,
                title="Play Play Stars",
                name="Play Play Stars",
                name_cn="Play Play Stars",
                eps=3,
                total_episodes=3,
                relation_to_main="side_story",
            ),
            BangumiSubjectCard(
                ref="BS3",
                subject_id=63,
                title="Franchise II",
                name="Franchise II",
                name_cn="Franchise II",
                eps=3,
                total_episodes=3,
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref="BS2", sort=index, ep=index, title=f"Short {index}")
            for index in range(1, 4)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    main_subject = registry.subject_locator_by_id[61]
    related_subject = registry.subject_locator_by_id[62]
    plain_same_count_subject = registry.subject_locator_by_id[63]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="numbered-sp-no-main-target",
                        local=[local_locator],
                        outcome="supplemental",
                        support=[main_subject],
                        reason="No corresponding Bangumi SP/OAD item is visible on the inspected main-season surface.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"]["numbered_special_exclusion_needs_target_evidence"] == 1
    repair = result.feedback["package"]["numbered_special_exclusion_repairs"][0]
    assert repair["shape_issue"] == "related_continuous_subject_ambiguous"
    assert repair["same_count_visible_subjects"][0]["target"] == related_subject
    assert plain_same_count_subject not in [
        candidate["target"] for candidate in repair["same_count_visible_subjects"]
    ]
    assert repair["same_count_visible_subjects"][0]["ambiguity_reason"] == "related_continuous_same_count"
    assert "manual_review" in repair["allowed_without_target_evidence"]

    mapped_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="numbered-sp-related-count-only",
                        local=[local_locator],
                        outcome="mapped_special_or_ova",
                        target=f"{related_subject}/episodes/1-3",
                        reason="Related side-story subject has the same continuous three-episode structure.",
                    )
                ]
            )
        ),
    )

    assert mapped_result.accepted is False
    mapped_repair = mapped_result.feedback["package"]["mapped_numbered_special_related_count_repairs"][0]
    assert mapped_repair["target_subject"] == related_subject
    assert mapped_repair["file_count"] == 3
    assert "Same-count structure" in mapped_repair["required"]
    upgrade = mapped_repair["evidence_upgrade_options"][0]
    assert upgrade["tool"] == "inspect"
    assert upgrade["scope"] == ["facts", "subtitle_compact"]
    assert upgrade["locators"] == [
        f"{local_locator}/episode/1",
        f"{local_locator}/episode/2",
        f"{local_locator}/episode/3",
    ]
    assert upgrade["fixed_layer_boundary"].startswith("This option is evidence access only")
    agenda = _repair_agenda_from_submit_feedback(mapped_result.feedback, repeated=False)
    blocking_row = next(
        row
        for row in agenda["blocking_units"]
        if row.get("issue") == "mapped_numbered_special_related_count_needs_stronger_evidence"
    )
    assert blocking_row["evidence_upgrade_options"][0]["locators"] == upgrade["locators"]
    frontier_row = next(
        row
        for row in agenda["repair_frontier"]
        if row.get("blocker") == "mapped_numbered_special_related_count_needs_stronger_evidence"
    )
    assert frontier_row["high_quality_next_actions"][0].startswith("inspect evidence_upgrade_options anchors")
    assert any("manual_review" in action for action in frontier_row["high_quality_next_actions"][1:])
    _workspace, upgraded_facts = _inspect_tool(
        workspace,
        registry,
        None,
        InspectToolArgs(locators=[upgrade["locators"][0]], scope=["subtitle_compact"]),
    )
    assert upgraded_facts["observations"][0]["subtitle_compact_cards"][0]["bounded_text_snippets"][0]["text"] == "Play2 anchor 1"

    uninspected_manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="numbered-sp-manual-review",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[f"{related_subject}/episodes/1-3"],
                        confidence="low",
                        reason=(
                            "Visible related short subject has the same continuous count, but current evidence "
                            "does not prove whether these SP files own that target or are alternate packaging."
                        ),
                        open_questions=["Does the local SPxx sequence own the related Play Play short-subject episodes?"],
                    )
                ]
            )
        ),
    )

    assert uninspected_manual_result.accepted is False
    assert uninspected_manual_result.feedback["package"]["issue_counts"]["manual_review_evidence_upgrade_required"] == 1
    pre_upgrade_repair = uninspected_manual_result.feedback["package"]["manual_review_evidence_upgrade_repairs"][0]
    assert pre_upgrade_repair["evidence_upgrade_options"][0]["locators"] == upgrade["locators"]
    upgrade_agenda = _repair_agenda_from_submit_feedback(uninspected_manual_result.feedback, repeated=False)
    repair_session = HumanCaseSession(
        case_id=workspace.header.case_id,
        turn_count=10,
        draft_work_units=[{"unit_label": "saved-main"}],
        observations=[{"tool": "submit", "output": upgrade_agenda}],
    )
    assert _repair_has_uninspected_evidence_upgrade_action(repair_session) is True
    assert _budget_pressure_tool_choice(repair_session, max_turns=12) == {
        "type": "function",
        "function": {"name": "inspect"},
    }
    upgraded_inspect_args, inspect_repair = _inspect_args_with_required_repair_locators(
        repair_session,
        InspectToolArgs(locators=[], scope=[]),
    )
    assert upgraded_inspect_args.locators[:3] == upgrade["locators"]
    assert upgraded_inspect_args.scope == ["facts", "subtitle_compact"]
    assert inspect_repair["required_evidence_upgrade_locators"] == upgrade["locators"]
    repair_session.observations.append(
        {
            "tool": "inspect",
            "output": {"observations": [{"locator": upgrade["locators"][0]}]},
        }
    )
    assert _repair_has_uninspected_evidence_upgrade_action(repair_session) is False
    assert _budget_pressure_tool_choice(repair_session, max_turns=12) == {
        "type": "function",
        "function": {"name": "submit"},
    }

    ledger_repair_session = HumanCaseSession(
        case_id=workspace.header.case_id,
        turn_count=10,
        draft_work_units=[{"unit_label": "saved-main"}],
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[local_locator],
                    status="manual_review",
                    manual_review_candidate_targets=[f"{related_subject}/episodes/1-3"],
                    reason="Visible candidate is unresolved before upgrade.",
                )
            ]
        ),
        observations=[
            {
                "tool": "submit",
                "output": {
                    "accepted": False,
                    "blocking_units": [
                        {
                            "unit": "numbered-sp-manual-review",
                            "local": [local_locator],
                            "issue": "manual_review_strong_non_regular_mapping_should_revise",
                        }
                    ],
                    "manual_review_strong_non_regular_mapping_repairs": [
                        {
                            "unit": "numbered-sp-manual-review",
                            "local": [local_locator],
                            "suggested_submit_shape": [
                                {
                                    "local": f"{local_locator}/episodes/1-3",
                                    "outcome": "mapped_special_or_ova",
                                    "target": f"{related_subject}/episodes/1-3",
                                }
                            ],
                        }
                    ],
                },
            }
        ],
    )
    assert _budget_pressure_tool_choice(ledger_repair_session, max_turns=12) == {
        "type": "function",
        "function": {"name": "patch_ledger"},
    }

    manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="numbered-sp-manual-review",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[f"{related_subject}/episodes/1-3"],
                        confidence="low",
                        reason=(
                            "Visible related short subject has the same continuous count, but inspected evidence "
                            "does not prove whether these SP files own that target or are alternate packaging."
                        ),
                        open_questions=["Does the local SPxx sequence own the related Play Play short-subject episodes?"],
                    )
                ]
            )
        ),
        inspected_locators={upgrade["locators"][0]},
    )

    assert manual_result.accepted is True
    assert manual_result.output is not None
    assert manual_result.feedback["manual_review_file_count"] == 3
    assert all(row.target_ref == "UNALIGNED" for row in manual_result.output.assignment_intents)
    assert all(not row.target_refs for row in manual_result.output.assignment_intents)
    assert all(":manual_review:" in row.reason for row in manual_result.output.assignment_intents)
    manual_unit = manual_result.feedback["units"][0]
    assert manual_unit["review_candidate_targets"] == [f"{related_subject}/episodes/1-3"]
    assert manual_unit["review_candidate_confidence"] == "low"
    assert manual_unit["review_hint_only"] is True
    assert manual_unit["open_questions"] == [
        "Does the local SPxx sequence own the related Play Play short-subject episodes?"
    ]


def test_mixed_duration_numbered_sp_exclusion_exposes_manual_review_shape():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-NUMBERED-SP-MIXED-DURATION-EXCLUSION"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3", "LF4"],
            allowed_file_refs=["LF1", "LF2", "LF3", "LF4"],
            visible_target_refs=["BE1"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/SPs/[Group] Franchise [SP01].mkv",
                is_main=True,
                container_facts={"probe_status": "available", "duration_seconds": 781.0},
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/SPs/[Group] Franchise [SP02].mkv",
                is_main=True,
                container_facts={"probe_status": "available", "duration_seconds": 782.0},
            ),
            LocalFileCard(
                ref="LF3",
                path="Pack/SPs/[Group] Franchise [SP03 Theater Manners 01].mkv",
                is_main=True,
                container_facts={"probe_status": "available", "duration_seconds": 102.0},
            ),
            LocalFileCard(
                ref="LF4",
                path="Pack/SPs/[Group] Franchise [SP04 Theater Manners 02].mkv",
                is_main=True,
                container_facts={"probe_status": "available", "duration_seconds": 102.0},
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(ref="BS1", subject_id=71, title="Franchise", name="Franchise", eps=13),
        ],
        bangumi_items=[BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1")],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    main_subject = registry.subject_locator_by_id[71]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="mixed-sp-target-absent",
                        local=[local_locator],
                        outcome="supplemental",
                        support=[main_subject],
                        reason=(
                            "No corresponding Bangumi SP/OAD item is visible on the inspected target surface; "
                            "treat the numbered SP group as bonus material."
                        ),
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    repair = result.feedback["package"]["numbered_special_exclusion_repairs"][0]
    assert repair["mixed_duration_requires_split_or_manual_review"] is True
    assert repair["local_duration_closure"]["reason"] == "duration_distribution_mixed"
    assert repair["manual_review_candidate_submit_shape"][0]["local"] == local_locator
    assert repair["manual_review_candidate_submit_shape"][0]["outcome"] == "manual_review"


def test_numbered_non_regular_same_count_mapping_accepts_with_duration_closure():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SP-DURATION-CLOSURE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3"],
            allowed_file_refs=["LF1", "LF2", "LF3"],
            visible_target_refs=["BE1", "BE2", "BE3"],
        ),
        local_files=[
            LocalFileCard(
                ref=f"LF{index}",
                path=f"Pack/SPs/[Group] Franchise II [SP{index:02d}].mkv",
                is_main=True,
                container_facts={
                    "probe_status": "available",
                    "duration_seconds": 92.0 + (index % 2),
                    "resolution": "1920x1080",
                },
            )
            for index in range(1, 4)
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=81,
                title="Franchise II",
                name="Franchise II",
                name_cn="Franchise II",
                eps=3,
                total_episodes=3,
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=82,
                title="Play Play Stars",
                name="Play Play Stars",
                name_cn="Play Play Stars",
                eps=3,
                total_episodes=3,
                relation_to_main="side_story",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref="BS2", sort=index, ep=index, title=f"Short {index}")
            for index in range(1, 4)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    main_subject = registry.subject_locator_by_id[81]
    related_subject = registry.subject_locator_by_id[82]

    manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="numbered-sp-saved-review-placeholder",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[f"{related_subject}/episodes/1-3"],
                        reason="Preserve saved manual-review placeholder.",
                    )
                ]
            )
        ),
    )

    assert manual_result.accepted is False
    assert manual_result.feedback["package"]["issue_counts"][
        "manual_review_strong_non_regular_mapping_should_revise"
    ] == 1
    repair = manual_result.feedback["package"]["manual_review_strong_non_regular_mapping_repairs"][0]
    assert repair["target_subject"] == related_subject
    closure = repair["strong_mapping_candidates"][0]["non_regular_evidence_closure"]
    assert closure["strong"] is True
    assert main_subject not in closure["same_count_target_candidates"]
    assert repair["suggested_submit_shape"] == [
        {
            "local": f"{local_locator}/episodes/1-3",
            "outcome": "mapped_special_or_ova",
            "target": f"{related_subject}/episodes/1-3",
            "reason": (
                "continuous numbered non-regular local slice maps to the visible same-count "
                "target slice after local duration/title evidence closes ownership"
            ),
        }
    ]
    agenda = _repair_agenda_from_submit_feedback(manual_result.feedback, repeated=False)
    frontier = next(
        row
        for row in agenda["repair_frontier"]
        if row.get("blocker") == "manual_review_strong_non_regular_mapping_should_revise"
    )
    assert frontier["high_quality_next_actions"][0].startswith("patch suggested_submit_shape rows")
    session = HumanCaseSession(
        case_id="CASE-SP-DURATION-CLOSURE",
        observations=[{"tool": "submit", "output": agenda}],
    )
    strong_candidates = _case_resolution_goal_strong_candidates(session, agenda)
    assert any(
        f"{related_subject}/episodes/1-3" in candidate["target_locators"]
        for candidate in strong_candidates
    )
    terminal_guard = _terminal_fail_closed_contract_guard_output(
        session,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="numbered-sp-budget-stop",
                        local=[f"{local_locator}/episodes/1-3"],
                        outcome="fail_closed",
                        reason="Turn cap reached without naming a contradiction.",
                    )
                ]
            ),
            reason="terminal",
        ),
        max_turns=4,
    )
    assert terminal_guard["issue"] == "terminal_fail_closed_must_address_strong_candidates"

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="numbered-sp-duration-closed",
                        local=[local_locator],
                        outcome="mapped_special_or_ova",
                        target=f"{related_subject}/episodes/1-3",
                        reason="Continuous non-regular short group maps to the unique visible related short subject.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is True
    assert result.output is not None
    assert result.feedback["mapped_file_count"] == 3
    assert all(row.target_ref != "UNALIGNED" for row in result.output.assignment_intents)


def test_numbered_non_regular_direct_query_candidate_accepts_with_duration_closure():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SP-DIRECT-QUERY-DURATION-CLOSURE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3"],
            allowed_file_refs=["LF1", "LF2", "LF3"],
            visible_target_refs=["BE1", "BE2", "BE3"],
        ),
        local_files=[
            LocalFileCard(
                ref=f"LF{index}",
                path=f"Pack/SPs/[Group] Franchise III [SP{index:02d}].mkv",
                is_main=True,
                container_facts={
                    "probe_status": "available",
                    "duration_seconds": 92.092,
                    "resolution": "1920x1080",
                },
            )
            for index in range(1, 4)
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=281,
                title="Franchise III",
                name="Franchise III",
                name_cn="Franchise III",
                eps=3,
                total_episodes=3,
                search_query_ref="Franchise III",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=282,
                title="Play Play Stars 3",
                name="Play Play Stars 3",
                name_cn="Play Play Stars 3",
                eps=3,
                total_episodes=3,
                search_query_ref="Franchise III",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref="BS2", sort=index, ep=index, title=f"Short {index}")
            for index in range(1, 4)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    main_subject = registry.subject_locator_by_id[281]
    direct_query_subject = registry.subject_locator_by_id[282]

    manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="direct-query-sp-review",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[f"{direct_query_subject}/episodes/1-3"],
                        reason="Saved review before deciding whether the direct query short subject owns these SPs.",
                    )
                ]
            )
        ),
    )

    assert manual_result.accepted is False
    repair = manual_result.feedback["package"]["manual_review_strong_non_regular_mapping_repairs"][0]
    assert repair["target_subject"] == direct_query_subject
    closure = repair["strong_mapping_candidates"][0]["non_regular_evidence_closure"]
    assert closure["strong"] is True
    assert closure["reason"] == "non_regular_count_duration_unique_query_bridge"
    assert closure["direct_query_duration_unique"] is True
    assert main_subject not in closure["same_count_target_candidates"]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="direct-query-sp-duration-closed",
                        local=[local_locator],
                        outcome="mapped_special_or_ova",
                        target=f"{direct_query_subject}/episodes/1-3",
                        reason="Continuous numbered SP group has same-count target and tightly matching local duration.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is True
    assert result.feedback["mapped_file_count"] == 3


def test_numbered_non_regular_direct_sp_query_does_not_promote_plain_main_subject():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SP-DIRECT-QUERY-MAIN-POLLUTION"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3"],
            allowed_file_refs=["LF1", "LF2", "LF3"],
            visible_target_refs=["BE1", "BE2", "BE3"],
        ),
        local_files=[
            LocalFileCard(
                ref=f"LF{index}",
                path=f"Pack/SPs/[Group] Franchise II [SP{index:02d}].mkv",
                is_main=True,
                container_facts={
                    "probe_status": "available",
                    "duration_seconds": 92.092,
                    "resolution": "1920x1080",
                },
            )
            for index in range(1, 4)
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=381,
                title="Franchise II",
                name="Franchise II",
                name_cn="Franchise II",
                eps=3,
                total_episodes=3,
                search_query_ref="Franchise II SP01",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=382,
                title="Play Play Stars 2",
                name="Play Play Stars 2",
                name_cn="Play Play Stars 2",
                eps=3,
                total_episodes=3,
                search_query_ref="Franchise II SP01",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref="BS2", sort=index, ep=index, title=f"Short {index}")
            for index in range(1, 4)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    main_subject = registry.subject_locator_by_id[381]
    direct_short_subject = registry.subject_locator_by_id[382]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-direct-query-review",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[main_subject, direct_short_subject],
                        reason="Same-count direct query candidate ownership remains ambiguous after title/count review.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"]["manual_review_strong_non_regular_mapping_should_revise"] == 1
    repair = result.feedback["package"]["manual_review_strong_non_regular_mapping_repairs"][0]
    assert repair["target_subject"] == direct_short_subject
    assert main_subject not in repair["non_regular_evidence_closure"]["same_count_target_candidates"]


def test_manual_review_strong_candidate_normalizes_span_review_hint_to_subject():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SP-SPAN-REVIEW-HINT-SUBJECT-FALLBACK"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3"],
            allowed_file_refs=["LF1", "LF2", "LF3"],
            visible_target_refs=[],
        ),
        local_files=[
            LocalFileCard(
                ref=f"LF{index}",
                path=f"Pack/SPs/[Group] Franchise II [SP{index:02d}].mkv",
                is_main=True,
                container_facts={
                    "probe_status": "available",
                    "duration_seconds": 92.092,
                    "resolution": "1920x1080",
                },
            )
            for index in range(1, 4)
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=481,
                title="Franchise II",
                name="Franchise II",
                name_cn="Franchise II",
                eps=3,
                total_episodes=3,
                search_query_ref="Franchise II",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=482,
                title="Play Play Stars 2",
                name="Play Play Stars 2",
                name_cn="Play Play Stars 2",
                eps=3,
                total_episodes=3,
                search_query_ref="Franchise II",
            ),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    main_subject = registry.subject_locator_by_id[481]
    direct_short_subject = registry.subject_locator_by_id[482]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-span-review-hint",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[
                            f"{direct_short_subject}/episodes/1-3",
                            main_subject,
                        ],
                        reason=(
                            "Localized uncertainty remains for the special-marker bundle; "
                            "use visible candidate targets as low-confidence review hints."
                        ),
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"]["manual_review_strong_non_regular_mapping_should_revise"] == 1
    repair = result.feedback["package"]["manual_review_strong_non_regular_mapping_repairs"][0]
    assert repair["target_subject"] == direct_short_subject
    assert main_subject not in repair["non_regular_evidence_closure"]["same_count_target_candidates"]


def test_numbered_non_regular_distinct_direct_query_can_upgrade_when_duration_unique():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SP-DISTINCT-DIRECT-QUERY-DURATION"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3"],
            allowed_file_refs=["LF1", "LF2", "LF3"],
            visible_target_refs=["BE1", "BE2", "BE3"],
        ),
        local_files=[
            LocalFileCard(
                ref=f"LF{index}",
                path=f"Pack/SPs/[Group] Franchise II [SP{index:02d}].mkv",
                is_main=True,
                container_facts={
                    "probe_status": "available",
                    "duration_seconds": 92.092,
                    "resolution": "1920x1080",
                },
            )
            for index in range(1, 4)
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=391,
                title="Franchise II",
                name="Franchise II",
                name_cn="Franchise II",
                eps=3,
                total_episodes=3,
                search_query_ref="Franchise II",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=392,
                title="Play Play Stars 2",
                name="Play Play Stars 2",
                name_cn="Play Play Stars 2",
                eps=3,
                total_episodes=3,
                search_query_ref="Franchise II",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref="BS2", sort=index, ep=index, title=f"Short {index}")
            for index in range(1, 4)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    main_subject = registry.subject_locator_by_id[391]
    direct_short_subject = registry.subject_locator_by_id[392]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-distinct-direct-query-review",
                        local=[local_locator],
                        outcome="manual_review",
                        reason="Review direct query candidates before accepting a short-form owner.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    repair = result.feedback["package"]["manual_review_strong_non_regular_mapping_repairs"][0]
    assert repair["target_subject"] == direct_short_subject
    closure = repair["non_regular_evidence_closure"]
    assert closure["direct_query_duration_unique"] is True
    assert main_subject not in closure["same_count_target_candidates"]


def test_manual_review_prioritizes_direct_same_count_non_regular_candidate_for_upgrade():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SP-DIRECT-SAME-COUNT-CANDIDATE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3"],
            allowed_file_refs=["LF1", "LF2", "LF3"],
            visible_target_refs=["BE1", "BE2", "BE3"],
        ),
        local_files=[
            LocalFileCard(
                ref=f"LF{index}",
                path=f"Pack/SPs/[Group] Franchise [SP{index:02d}].mkv",
                is_main=True,
                container_facts={
                    "probe_status": "available",
                    "duration_seconds": 182.015,
                    "resolution": "1920x1080",
                },
                subtitle_facts={
                    "language_markers": ["chs"],
                    "bounded_text_snippets": [
                        {"source_ref": f"sub{index}", "text": f"Play{index} Franchise short title"}
                    ],
                    "snippet_source": "external_subtitle_file",
                },
            )
            for index in range(1, 4)
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=181,
                title="Franchise",
                name="Franchise",
                name_cn="Franchise",
                eps=13,
                total_episodes=13,
                search_query_ref="Franchise",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=182,
                title="Play Play Stars",
                name="Play Play Stars",
                name_cn="Play Play Stars",
                eps=3,
                total_episodes=3,
                search_query_ref="Franchise SP01",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref="BS2", sort=index, ep=index, title=f"Play {index}")
            for index in range(1, 4)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    direct_same_count_subject = registry.subject_locator_by_id[182]

    manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-direct-same-count-review",
                        local=[local_locator],
                        outcome="manual_review",
                        reason="Use manual review before addressing the visible same-count Play Play target.",
                    )
                ]
            )
        ),
    )

    assert manual_result.accepted is False
    unit = manual_result.feedback["units"][0]
    assert unit["review_candidate_targets"][0] == f"{direct_same_count_subject}/episodes/1-3"
    assert manual_result.feedback["package"]["issue_counts"][
        "manual_review_strong_non_regular_mapping_should_revise"
    ] == 1
    initial_strong_repair = manual_result.feedback["package"]["manual_review_strong_non_regular_mapping_repairs"][0]
    assert initial_strong_repair["target_subject"] == direct_same_count_subject
    assert initial_strong_repair["suggested_submit_shape"][0]["target"] == (
        f"{direct_same_count_subject}/episodes/1-3"
    )

    broad_addressed_manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-direct-same-count-review-addressed",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[f"{direct_same_count_subject}/episodes/1-3"],
                        confidence="low",
                        reason=(
                            "The visible same-count Play Play target remains ambiguous after upgraded local facts; "
                            "manual review keeps the candidate without assigning ownership."
                        ),
                    )
                ]
            )
        ),
        inspected_locators={
            f"{local_locator}/episode/1",
            f"{local_locator}/episode/2",
            f"{local_locator}/episode/3",
        },
    )

    assert broad_addressed_manual_result.accepted is False
    assert broad_addressed_manual_result.feedback["package"]["issue_counts"][
        "manual_review_strong_non_regular_mapping_should_revise"
    ] == 1
    broad_repair = broad_addressed_manual_result.feedback["package"]["manual_review_strong_non_regular_mapping_repairs"][0]
    assert broad_repair["suggested_submit_shape"][0]["target"] == f"{direct_same_count_subject}/episodes/1-3"

    addressed_manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-direct-same-count-review-addressed-exact",
                        local=[f"{local_locator}/episodes/1-3"],
                        outcome="manual_review",
                        manual_review_candidate_targets=[f"{direct_same_count_subject}/episodes/1-3"],
                        confidence="low",
                        reason=(
                            "The exact SP01-SP03 slice has a duration/title evidence conflict against the visible "
                            "same-count target, so ownership remains unresolved after upgrade."
                        ),
                    )
                ]
            )
        ),
        inspected_locators={
            f"{local_locator}/episode/1",
            f"{local_locator}/episode/2",
            f"{local_locator}/episode/3",
        },
    )

    assert addressed_manual_result.accepted is True
    assert addressed_manual_result.feedback["manual_review_file_count"] == 3

    contradictory_manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-direct-same-count-review-contradiction",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[f"{direct_same_count_subject}/episodes/1-3"],
                        confidence="low",
                        reason=(
                            "The visible same-count Play Play target has a concrete title mismatch against inspected "
                            "local title-card evidence, so ownership remains unresolved after upgrade."
                        ),
                    )
                ]
            )
        ),
        inspected_locators={
            f"{local_locator}/episode/1",
            f"{local_locator}/episode/2",
            f"{local_locator}/episode/3",
        },
    )

    assert contradictory_manual_result.accepted is True
    assert contradictory_manual_result.feedback["manual_review_file_count"] == 3


def test_sp_query_marker_does_not_make_main_season_a_non_regular_target_form():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SP-QUERY-MARKER-NOT-TARGET-FORM"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3"],
            allowed_file_refs=["LF1", "LF2", "LF3"],
            visible_target_refs=["BE1", "BE2", "BE3", "BE4", "BE5", "BE6"],
        ),
        local_files=[
            LocalFileCard(
                ref=f"LF{index}",
                path=f"Pack/SPs/[Group] Franchise II [SP{index:02d}].mkv",
                is_main=True,
                container_facts={"probe_status": "available", "duration_seconds": 92.0},
            )
            for index in range(1, 4)
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=191,
                title="Franchise II",
                name="Franchise II",
                name_cn="Franchise II",
                eps=3,
                total_episodes=3,
                search_query_ref="Franchise II SP01",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=192,
                title="Play Play Stars 2",
                name="Play Play Stars 2",
                name_cn="Play Play Stars 2",
                eps=3,
                total_episodes=3,
                search_query_ref="Franchise II SP01",
            ),
        ],
        bangumi_items=[
            *[
                BangumiItemCard(ref=f"BE{index}", subject_ref="BS1", sort=index, ep=index, title=f"Main {index}")
                for index in range(1, 4)
            ],
            *[
                BangumiItemCard(ref=f"BE{index + 3}", subject_ref="BS2", sort=index, ep=index, title=f"Short {index}")
                for index in range(1, 4)
            ],
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    main_subject = registry.subject_locator_by_id[191]
    short_subject = registry.subject_locator_by_id[192]

    main_support = _target_non_regular_mapping_support_details(
        registry.locators[main_subject],
        {"franchise", "ii"},
    )
    assert main_support["query_non_regular_marker"] is True
    assert main_support["non_regular_target_form"] is False
    assert main_support["strong"] is False

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-review-with-main-and-short-candidates",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[main_subject, short_subject],
                        reason="Review both visible same-count candidates before assigning ownership.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    repair = result.feedback["package"]["manual_review_strong_non_regular_mapping_repairs"][0]
    assert repair["target_subject"] == short_subject
    assert [item["target_subject"] for item in repair["strong_mapping_candidates"]] == [short_subject]
    assert repair["suggested_submit_shape"][0]["target"] == f"{short_subject}/episodes/1-3"


def test_manual_review_strong_candidate_filters_explicit_targets_by_local_season():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SP-EXPLICIT-SEASON-CANDIDATE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3"],
            allowed_file_refs=["LF1", "LF2", "LF3"],
            visible_target_refs=[],
        ),
        local_files=[
            LocalFileCard(
                ref=f"LF{index}",
                path=f"Pack/SPs/[Group] Franchise III [SP{index:02d}].mkv",
                is_main=True,
                container_facts={"probe_status": "available", "duration_seconds": 92.0},
            )
            for index in range(1, 4)
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS2",
                subject_id=302,
                title="Play Play Stars 2",
                name="Play Play Stars 2",
                name_cn="Play Play Stars 2",
                eps=3,
                total_episodes=3,
                search_query_ref="Franchise",
                relation_to_main="side_story",
            ),
            BangumiSubjectCard(
                ref="BS3",
                subject_id=303,
                title="Play Play Stars 3",
                name="Play Play Stars 3",
                name_cn="Play Play Stars 3",
                eps=3,
                total_episodes=3,
                search_query_ref="Franchise",
                relation_to_main="side_story",
            ),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    season_two_subject = registry.subject_locator_by_id[302]
    season_three_subject = registry.subject_locator_by_id[303]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-explicit-season-review",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[season_two_subject, season_three_subject],
                        reason="Keep visible derivative candidates for review.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    repair = result.feedback["package"]["manual_review_strong_non_regular_mapping_repairs"][0]
    assert repair["target_subject"] == season_three_subject
    assert repair["suggested_submit_shape"][0]["target"] == f"{season_three_subject}/episodes/1-3"
    assert season_two_subject not in repair["non_regular_evidence_closure"]["same_count_target_candidates"]


def test_duplicate_numbered_variant_locator_can_accept_same_episode_multi_versions():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SP-DUPLICATE-VARIANT"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3", "LF4"],
            allowed_file_refs=["LF1", "LF2", "LF3", "LF4"],
            visible_target_refs=["BE1", "BE2", "BE3"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Pack/SPs/[Group] Play Play Stars [SP01].mkv", is_main=True),
            LocalFileCard(
                ref="LF2",
                path="Pack/SPs/[Group] Play Play Stars [SP02_1].mkv",
                is_main=True,
                container_facts={"probe_status": "available", "duration_seconds": 92.0},
            ),
            LocalFileCard(
                ref="LF3",
                path="Pack/SPs/[Group] Play Play Stars [SP02_2].mkv",
                is_main=True,
                container_facts={"probe_status": "available", "duration_seconds": 92.0},
            ),
            LocalFileCard(ref="LF4", path="Pack/SPs/[Group] Play Play Stars [SP03].mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=91,
                title="Play Play Stars",
                name="Play Play Stars",
                name_cn="Play Play Stars",
                eps=3,
                total_episodes=3,
                relation_to_main="side_story",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref="BS1", sort=index, ep=index, title=f"Short {index}")
            for index in range(1, 4)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    duplicate_variants = desk["local_locators"][0]["duplicate_episode_variant_locators"]
    assert [row["locator"] for row in duplicate_variants] == [
        f"{local_locator}/episode/2/variant/1",
        f"{local_locator}/episode/2/variant/2",
    ]
    first_variant, first_issue = registry.resolve(f"{local_locator}/episode/2/variant/1")
    second_variant, second_issue = registry.resolve(f"{local_locator}/episode/2/variant/2")
    assert first_issue is None
    assert second_issue is None
    assert first_variant is not None and first_variant.file_refs == ("LF2",)
    assert second_variant is not None and second_variant.file_refs == ("LF3",)
    _workspace, inspected = _inspect_tool(
        workspace,
        registry,
        None,
        InspectToolArgs(locators=[f"{local_locator}/episode/2/variant/1"], scope=["facts"]),
    )
    assert inspected["observations"][0]["local_fact_cards"][0]["container_facts"]["duration_seconds"] == 92.0

    subject_locator = registry.subject_locator_by_id[91]
    manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-duplicate-group-review",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[subject_locator],
                        reason="Only the duplicate SP02 variant choice is unresolved.",
                    )
                ]
            )
        ),
    )

    assert manual_result.accepted is False
    duplicate_repairs = manual_result.feedback["package"]["manual_review_duplicate_variant_repairs"]
    assert duplicate_repairs[0]["local_unique_episode_count"] == 3
    assert duplicate_repairs[0]["duplicate_episode_variant_locators"][0]["locator"] == f"{local_locator}/episode/2/variant/1"
    assert [row["local"] for row in duplicate_repairs[0]["multi_version_submit_shape"]] == [
        f"{local_locator}/episode/1",
        f"{local_locator}/episode/2/variant/1",
        f"{local_locator}/episode/2/variant/2",
        f"{local_locator}/episode/3",
    ]

    broad_ambiguous_manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-duplicate-target-ambiguous-review",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[subject_locator],
                        confidence="low",
                        reason=(
                            "Target ownership remains ambiguous after the available evidence; current facts do not "
                            "prove whether these SP files own the related short subject."
                        ),
                    )
                ]
            )
        ),
        inspected_locators={f"{local_locator}/episode/2/variant/1"},
    )

    assert broad_ambiguous_manual_result.accepted is False
    assert broad_ambiguous_manual_result.feedback["package"]["issue_counts"][
        "manual_review_duplicate_variant_should_split"
    ] == 1
    broad_duplicate_repair = broad_ambiguous_manual_result.feedback["package"]["manual_review_duplicate_variant_repairs"][0]
    assert broad_duplicate_repair["suggested_submit_shape"] == broad_duplicate_repair["multi_version_submit_shape"]

    ambiguous_manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-duplicate-target-ambiguous-review-1",
                        local=[f"{local_locator}/episode/1"],
                        outcome="manual_review",
                        manual_review_candidate_targets=[f"{subject_locator}/episode/1"],
                        confidence="low",
                        reason="Exact local SP01 target ownership remains ambiguous after upgraded local title evidence.",
                    ),
                    ResolutionWorkUnit(
                        unit_label="sp-duplicate-target-ambiguous-review-2a",
                        local=[f"{local_locator}/episode/2/variant/1"],
                        outcome="manual_review",
                        manual_review_candidate_targets=[f"{subject_locator}/episode/2"],
                        confidence="low",
                        reason="Exact local SP02 variant 1 target ownership remains ambiguous after duration evidence.",
                    ),
                    ResolutionWorkUnit(
                        unit_label="sp-duplicate-target-ambiguous-review-2b",
                        local=[f"{local_locator}/episode/2/variant/2"],
                        outcome="manual_review",
                        manual_review_candidate_targets=[f"{subject_locator}/episode/2"],
                        confidence="low",
                        reason="Exact local SP02 variant 2 target ownership remains ambiguous after duration evidence.",
                    ),
                    ResolutionWorkUnit(
                        unit_label="sp-duplicate-target-ambiguous-review-3",
                        local=[f"{local_locator}/episode/3"],
                        outcome="manual_review",
                        manual_review_candidate_targets=[f"{subject_locator}/episode/3"],
                        confidence="low",
                        reason="Exact local SP03 target ownership remains ambiguous after upgraded local title evidence.",
                    ),
                ]
            )
        ),
        inspected_locators={f"{local_locator}/episode/2/variant/1"},
    )

    assert ambiguous_manual_result.accepted is True
    assert ambiguous_manual_result.output is not None
    assert ambiguous_manual_result.feedback["manual_review_file_count"] == 4

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-1",
                        local=[f"{local_locator}/episode/1"],
                        outcome="mapped_special_or_ova",
                        target=f"{subject_locator}/episode/1",
                        reason="Local numbered short maps to the visible related short episode.",
                    ),
                    ResolutionWorkUnit(
                        unit_label="sp-2-version-a",
                        local=[f"{local_locator}/episode/2/variant/1"],
                        outcome="mapped_special_or_ova",
                        target=f"{subject_locator}/episode/2",
                        reason="Duplicate variants share the same local episode title and duration; accept as alternate multi-version file.",
                    ),
                    ResolutionWorkUnit(
                        unit_label="sp-2-version-b",
                        local=[f"{local_locator}/episode/2/variant/2"],
                        outcome="mapped_special_or_ova",
                        target=f"{subject_locator}/episode/2",
                        reason="Duplicate alternate variant of local SP02 with the same duration; accept as alternate multi-version file.",
                    ),
                    ResolutionWorkUnit(
                        unit_label="sp-3",
                        local=[f"{local_locator}/episode/3"],
                        outcome="mapped_special_or_ova",
                        target=f"{subject_locator}/episode/3",
                        reason="Local numbered short maps to the visible related short episode.",
                    ),
                ]
            )
        ),
    )

    assert result.accepted is True
    assert result.output is not None
    assert result.feedback["mapped_file_count"] == 4
    assert result.feedback["excluded_file_count"] == 0
    assert result.feedback["allowed_multi_version_duplicate_target_count"] == 1
    by_file = {row.file_ref: row for row in result.output.assignment_intents}
    assert by_file["LF2"].target_ref == "BE2"
    assert by_file["LF3"].target_ref == "BE2"
    assert "duplicate_like" in by_file["LF3"].risk_flags


def test_frontier_keeps_multi_version_shape_before_numbered_sp_manual_fallback():
    rows = _repair_frontier_rows_from_agenda(
        {
            "blocking_units": [
                {
                    "local": ["local://pack-sps/special-marker"],
                    "issue": "numbered_special_exclusion_needs_target_evidence",
                    "required": "Use the visible duplicate variant submit shape before falling back to review.",
                    "multi_version_submit_shape": [
                        {
                            "local": "local://pack-sps/special-marker/episode/1",
                            "target": "target://bangumi/91-play-play/episode/1",
                            "outcome": "mapped_special_or_ova",
                        },
                        {
                            "local": "local://pack-sps/special-marker/episode/2/variant/1",
                            "target": "target://bangumi/91-play-play/episode/2",
                            "outcome": "mapped_special_or_ova",
                        },
                        {
                            "local": "local://pack-sps/special-marker/episode/2/variant/2",
                            "target": "target://bangumi/91-play-play/episode/2",
                            "outcome": "mapped_special_or_ova",
                        },
                    ],
                }
            ]
        }
    )

    actions = rows[0]["high_quality_next_actions"]
    assert actions[0].startswith("patch the listed multi_version_submit_shape rows")
    assert any("manual_review" in action for action in actions[1:])
    assert [row["local"] for row in rows[0]["multi_version_submit_shape"]] == [
        "local://pack-sps/special-marker/episode/1",
        "local://pack-sps/special-marker/episode/2/variant/1",
        "local://pack-sps/special-marker/episode/2/variant/2",
    ]


def test_ledger_overlap_with_duplicate_variant_review_candidates_exposes_multi_version_shape():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-OVERLAP-DUPLICATE-VARIANT-SHAPE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3", "LF4"],
            allowed_file_refs=["LF1", "LF2", "LF3", "LF4"],
            visible_target_refs=["BE1", "BE2", "BE3"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Pack/SPs/[Group] Play Play Stars [SP01].mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Pack/SPs/[Group] Play Play Stars [SP02_1].mkv", is_main=True),
            LocalFileCard(ref="LF3", path="Pack/SPs/[Group] Play Play Stars [SP02_2].mkv", is_main=True),
            LocalFileCard(ref="LF4", path="Pack/SPs/[Group] Play Play Stars [SP03].mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=390,
                title="Play Play Stars",
                name="Play Play Stars",
                name_cn="Play Play Stars",
                eps=3,
                total_episodes=3,
                relation_to_main="side_story",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref="BS1", sort=index, ep=index, title=f"Short {index}")
            for index in range(1, 4)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    subject = registry.subject_locator_by_id[390]

    result = _compile_resolution_ledger_to_submit_result(
        workspace,
        registry,
        ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[local_locator],
                    status="manual_review",
                    manual_review_candidate_targets=[subject],
                    reason="Broad parent review kept while child rows were split.",
                ),
                ResolutionLedgerRow(
                    row_id="LR1_split_2",
                    local=[f"{local_locator}/episode/2/variant/1"],
                    status="manual_review",
                    manual_review_candidate_targets=[f"{subject}/episode/2"],
                    reason="Duplicate variant still review-only.",
                ),
                ResolutionLedgerRow(
                    row_id="LR1_split_3",
                    local=[f"{local_locator}/episode/2/variant/2"],
                    status="manual_review",
                    manual_review_candidate_targets=[f"{subject}/episode/2"],
                    reason="Duplicate variant still review-only.",
                ),
                ResolutionLedgerRow(
                    row_id="LR1_split_4",
                    local=[f"{local_locator}/episode/3"],
                    status="manual_review",
                    manual_review_candidate_targets=[f"{subject}/episode/3"],
                    reason="Trailing child row overlaps the broad parent.",
                ),
            ]
        ),
    )

    assert result.accepted is False
    issue = next(item for item in result.feedback["issues"] if item["issue"] == "ledger_coverage_overlap")
    assert [
        (row["local"], row["target"], row["outcome"])
        for row in issue["multi_version_submit_shape"]
    ] == [
        (f"{local_locator}/episode/1", f"{subject}/episode/1", "mapped_special_or_ova"),
        (f"{local_locator}/episode/2/variant/1", f"{subject}/episode/2", "mapped_special_or_ova"),
        (f"{local_locator}/episode/2/variant/2", f"{subject}/episode/2", "mapped_special_or_ova"),
        (f"{local_locator}/episode/3", f"{subject}/episode/3", "mapped_special_or_ova"),
    ]


def test_strong_suggested_rows_promote_complete_overlap_multi_version_shape():
    parent = "local://pack-sps/special-marker"
    subject = "target://bangumi/390-play-play-stars"
    weak_shape = [
        {
            "local": f"{parent}/episode/1",
            "target": f"{subject}/episode/1",
            "outcome": "mapped_special_or_ova",
            "reason": "strong candidate debt template",
        },
        {
            "local": f"{parent}/episode/2/variant/1",
            "target": f"{subject}/episode/2",
            "outcome": "mapped_special_or_ova",
            "reason": "strong candidate debt template",
        },
        {
            "local": f"{parent}/episode/3",
            "target": f"{subject}/episode/3",
            "outcome": "mapped_special_or_ova",
            "reason": "strong candidate debt template",
        },
    ]
    complete_shape = [
        {
            "local": f"{parent}/episode/1",
            "target": f"{subject}/episode/1",
            "outcome": "mapped_special_or_ova",
            "reason": "complete overlap repair template",
        },
        {
            "local": f"{parent}/episode/2/variant/1",
            "target": f"{subject}/episode/2",
            "outcome": "mapped_special_or_ova",
            "reason": "complete overlap repair template",
        },
        {
            "local": f"{parent}/episode/2/variant/2",
            "target": f"{subject}/episode/2",
            "outcome": "mapped_special_or_ova",
            "reason": "complete overlap repair template",
        },
        {
            "local": f"{parent}/episode/3",
            "target": f"{subject}/episode/3",
            "outcome": "mapped_special_or_ova",
            "reason": "complete overlap repair template",
        },
    ]
    session = HumanCaseSession(
        case_id="CASE-STRONG-OVERLAP-MULTI-VERSION-TEMPLATE",
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(row_id="LR6", local=[parent], status="manual_review", reason="broad parent"),
                ResolutionLedgerRow(
                    row_id="LR6_split_2",
                    local=[f"{parent}/episode/2/variant/1"],
                    status="manual_review",
                    reason="first duplicate variant",
                ),
                ResolutionLedgerRow(
                    row_id="LR6_split_3",
                    local=[f"{parent}/episode/2/variant/2"],
                    status="manual_review",
                    reason="second duplicate variant",
                ),
            ]
        ),
    )
    repair = {
        "accepted": False,
        "issue_counts": {
            "ledger_strong_candidate_manual_review_requires_contradiction": 1,
            "ledger_coverage_overlap": 1,
        },
        "units": [
            {
                "unit": "LR6",
                "local": [parent],
                "issues": [
                    {
                        "issue": "ledger_strong_candidate_manual_review_requires_contradiction",
                        "row_id": "LR6",
                        "local": [parent],
                        "suggested_submit_shape": weak_shape,
                    },
                    {
                        "issue": "ledger_coverage_overlap",
                        "row_ids": ["LR6", "LR6_split_2", "LR6_split_3"],
                        "multi_version_submit_shape": complete_shape,
                    },
                ],
            }
        ],
    }

    rows = _strong_suggested_ledger_patch_rows_from_repair(session, repair)

    assert [row["local"][0] for row in rows[:4]] == [row["local"] for row in complete_shape]
    assert [row["row_id"] for row in rows[:4]] == ["LR6", "LR6_split_2", "LR6_split_3", "LR6_split_4"]
    assert all(row["status"] == "mapped" for row in rows[:4])


def test_ledger_missing_duplicate_variant_exposes_complete_multi_version_shape():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-MISSING-DUPLICATE-VARIANT-SHAPE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3", "LF4"],
            allowed_file_refs=["LF1", "LF2", "LF3", "LF4"],
            visible_target_refs=["BE1", "BE2", "BE3"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Pack/SPs/[Group] Play Play Stars [SP01].mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Pack/SPs/[Group] Play Play Stars [SP02_1].mkv", is_main=True),
            LocalFileCard(ref="LF3", path="Pack/SPs/[Group] Play Play Stars [SP02_2].mkv", is_main=True),
            LocalFileCard(ref="LF4", path="Pack/SPs/[Group] Play Play Stars [SP03].mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=392,
                title="Play Play Stars",
                name="Play Play Stars",
                name_cn="Play Play Stars",
                eps=3,
                total_episodes=3,
                relation_to_main="side_story",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref="BS1", sort=index, ep=index, title=f"Short {index}")
            for index in range(1, 4)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    subject = registry.subject_locator_by_id[392]
    result = _compile_resolution_ledger_to_submit_result(
        workspace,
        registry,
        ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local_locator}/episode/1"],
                    status="mapped",
                    mapped_outcome="mapped_special_or_ova",
                    target=f"{subject}/episode/1",
                    reason="Local numbered short maps to the visible related short episode.",
                ),
                ResolutionLedgerRow(
                    row_id="LR1_split_2",
                    local=[f"{local_locator}/episode/2/variant/1"],
                    status="mapped",
                    mapped_outcome="mapped_special_or_ova",
                    target=f"{subject}/episode/2",
                    reason="Duplicate variant accepts same episode target as multi-version.",
                ),
                ResolutionLedgerRow(
                    row_id="LR1_split_4",
                    local=[f"{local_locator}/episode/3"],
                    status="mapped",
                    mapped_outcome="mapped_special_or_ova",
                    target=f"{subject}/episode/3",
                    reason="Local numbered short maps to the visible related short episode.",
                ),
            ]
        ),
    )

    assert result.accepted is False
    issue = next(item for item in result.feedback["issues"] if item["issue"] == "ledger_coverage_missing")
    assert issue["file_refs"] == ["LF3"]
    assert [
        (row["local"], row["target"], row["outcome"])
        for row in issue["multi_version_submit_shape"]
    ] == [
        (f"{local_locator}/episode/1", f"{subject}/episode/1", "mapped_special_or_ova"),
        (f"{local_locator}/episode/2/variant/1", f"{subject}/episode/2", "mapped_special_or_ova"),
        (f"{local_locator}/episode/2/variant/2", f"{subject}/episode/2", "mapped_special_or_ova"),
        (f"{local_locator}/episode/3", f"{subject}/episode/3", "mapped_special_or_ova"),
    ]
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local_locator], status="manual_review")]
        ),
    )
    suggested = _suggested_ledger_patch_rows_from_repair(session, result.feedback)
    assert [row["row_id"] for row in suggested] == ["LR1", "LR1_split_2", "LR1_split_3", "LR1_split_4"]


def test_patch_ledger_overlap_suggested_shape_guard_allows_exact_candidate_manual_review():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-OVERLAP-SUGGESTED-SHAPE-GUARD"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Pack/SPs/[Group] Play Play Stars [SP01].mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Pack/SPs/[Group] Play Play Stars [SP02].mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(ref="BS1", subject_id=391, title="Play Play Stars", eps=2, total_episodes=2),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Short 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS1", sort=2, ep=2, title="Short 2"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    subject = registry.subject_locator_by_id[391]
    shape = [
        {
            "local": f"{local_locator}/episodes/1-2",
            "target": f"{subject}/episodes/1-2",
            "outcome": "mapped_special_or_ova",
            "reason": "visible split template from overlap repair",
        }
    ]
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local_locator], status="manual_review", reason="pending")]
        ),
        observations=[
            {
                "tool": "patch_ledger",
                "output": {
                    "accepted": False,
                    "issue_counts": {"ledger_coverage_overlap": 1},
                    "repair_frontier": [{"multi_version_submit_shape": shape}],
                },
            }
        ],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[local_locator],
                    status="manual_review",
                    manual_review_candidate_targets=[subject],
                    reason="Target episode surface remains uninspected, so ownership is unresolved.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is True
    assert output["accepted"] is True
    assert session.resolution_ledger.rows[0].status == "manual_review"
    assert session.resolution_ledger.rows[0].manual_review_candidate_targets == [subject]


def test_patch_ledger_overlap_suggested_shape_guard_rejects_broad_candidate_manual_review():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-OVERLAP-SUGGESTED-SHAPE-BROAD-REVIEW"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3"],
            allowed_file_refs=["LF1", "LF2", "LF3"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Pack/SPs/[Group] Play Play Stars [SP01].mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Pack/SPs/[Group] Play Play Stars [SP02].mkv", is_main=True),
            LocalFileCard(ref="LF3", path="Pack/SPs/[Group] Play Play Stars [SP03].mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(ref="BS1", subject_id=391, title="Play Play Stars", eps=2, total_episodes=2),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Short 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS1", sort=2, ep=2, title="Short 2"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    subject = registry.subject_locator_by_id[391]
    shape = [
        {
            "local": f"{local_locator}/episodes/1-2",
            "target": f"{subject}/episodes/1-2",
            "outcome": "mapped_special_or_ova",
            "reason": "visible split template from overlap repair",
        }
    ]
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local_locator], status="manual_review", reason="pending")]
        ),
        observations=[
            {
                "tool": "patch_ledger",
                "output": {
                    "accepted": False,
                    "issue_counts": {"ledger_coverage_overlap": 1},
                    "repair_frontier": [{"multi_version_submit_shape": shape}],
                },
            }
        ],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[local_locator],
                    status="manual_review",
                    manual_review_candidate_targets=[subject],
                    reason="This broad parent still contains an extra unresolved SP03 file.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is False
    assert output["accepted"] is False
    assert output["issue"] == "patch_ledger_suggested_shape_unaddressed"
    assert output["suggested_ledger_patch_rows"][0]["local"] == [f"{local_locator}/episodes/1-2"]
    assert session.resolution_ledger.rows[0].status == "manual_review"


def test_frontier_preserves_compacted_json_string_submit_shapes():
    shape_rows = [
        {
            "local": "local://pack-sps/special-marker/episodes/1-7",
            "target": "target://bangumi/91-play-play/episodes/1-7",
            "outcome": "mapped_special_or_ova",
        },
        {
            "local": "local://pack-sps/special-marker/episode/8/variant/1",
            "target": "target://bangumi/91-play-play/episode/8",
            "outcome": "mapped_special_or_ova",
        },
    ]
    feedback = {
        "accepted": False,
        "package": {"issue_counts": {"manual_review_duplicate_variant_should_split": 1}},
        "units": [
            {
                "unit": "LR6",
                "local": ["local://pack-sps/special-marker"],
                "issues": [
                    {
                        "issue": "manual_review_duplicate_variant_should_split",
                        "multi_version_submit_shape": [
                            json.dumps(row, ensure_ascii=False)
                            for row in shape_rows
                        ],
                    }
                ],
            }
        ],
    }

    agenda = _repair_agenda_from_submit_feedback(feedback, repeated=False)
    frontier = _repair_frontier_rows_from_agenda(agenda)

    assert frontier[0]["repair_priority"] == 0
    assert frontier[0]["multi_version_submit_shape"] == shape_rows
    assert frontier[0]["high_quality_next_actions"][0].startswith(
        "patch the listed multi_version_submit_shape rows"
    )


def test_suggested_submit_shape_exposes_ledger_patch_rows_with_local_locators():
    session = HumanCaseSession(
        case_id="CASE-LEDGER-PATCH-TEMPLATE",
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR4",
                    local=["local://pack-sps/special-marker"],
                    status="manual_review",
                    reason="Candidate unresolved before duration closure.",
                )
            ]
        ),
    )
    repair = {
        "manual_review_strong_non_regular_mapping_repairs": [
            {
                "unit": "LR4",
                "local": ["local://pack-sps/special-marker"],
                "suggested_submit_shape": [
                    {
                        "local": "local://pack-sps/special-marker/episodes/1-2",
                        "outcome": "mapped_special_or_ova",
                        "target": "target://bangumi/91-play-play/episodes/1-2",
                        "reason": "visible same-count target closes ownership",
                    },
                    {
                        "local": "local://pack-sps/special-marker/episode/3",
                        "outcome": "mapped_special_or_ova",
                        "target": "target://bangumi/91-play-play/episode/3",
                        "reason": "visible same-count target closes ownership",
                    },
                ],
            }
        ]
    }

    rows = _suggested_ledger_patch_rows_from_repair(session, repair)

    assert rows == [
        {
            "row_id": "LR4",
            "local": ["local://pack-sps/special-marker/episodes/1-2"],
            "reason": "visible same-count target closes ownership",
            "status": "mapped",
            "mapped_outcome": "mapped_special_or_ova",
            "target": "target://bangumi/91-play-play/episodes/1-2",
            "confidence": "high",
        },
        {
            "row_id": "LR4_split_2",
            "local": ["local://pack-sps/special-marker/episode/3"],
            "reason": "visible same-count target closes ownership",
            "status": "mapped",
            "mapped_outcome": "mapped_special_or_ova",
            "target": "target://bangumi/91-play-play/episode/3",
            "confidence": "high",
        },
    ]
    assert all(not row["local"][0].startswith("LF") for row in rows)


def test_patch_ledger_must_address_strong_suggested_shape():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    repair = {
        "accepted": False,
        "blocking_units": [
            {
                "unit": "LR1",
                "local": [local],
                "issue": "manual_review_strong_non_regular_mapping_should_revise",
            }
        ],
        "manual_review_strong_non_regular_mapping_repairs": [
            {
                "unit": "LR1",
                "local": [local],
                "issue": "manual_review_strong_non_regular_mapping_should_revise",
                "suggested_submit_shape": [
                    {
                        "local": f"{local}/episodes/1-2",
                        "target": target,
                        "outcome": "mapped_special_or_ova",
                        "reason": "visible same-count target closes ownership",
                    }
                ],
                "manual_review_candidate_submit_shape": [
                    {
                        "local": f"{local}/episodes/1-2",
                        "outcome": "manual_review",
                        "manual_review_candidate_targets": [target],
                        "confidence": "low",
                        "reason": "low-confidence replay hint",
                    }
                ],
            }
        ],
    }
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="manual_review", reason="pending")]
        ),
        observations=[{"tool": "submit", "output": repair}],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episodes/1-2"],
                    status="manual_review",
                    manual_review_candidate_targets=[target],
                    reason="Target episode range evidence remains unresolved after inspect.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is True
    assert output["accepted"] is True
    assert session.resolution_ledger.rows[0].status == "manual_review"
    assert session.resolution_ledger.rows[0].manual_review_candidate_targets == [target]


def test_nested_package_manual_review_shape_becomes_ledger_patch_row_and_guard():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = registry.subject_locator_by_id[1]
    repair = {
        "accepted": False,
        "issue_counts": {"numbered_special_exclusion_needs_target_evidence": 1},
        "package": {
            "numbered_special_exclusion_repairs": [
                {
                    "unit": "LR2",
                    "issue": "numbered_special_exclusion_needs_target_evidence",
                    "local": local,
                    "manual_review_candidate_submit_shape": [
                        {
                            "local": local,
                            "outcome": "manual_review",
                            "manual_review_candidate_targets": [target],
                            "confidence": "low",
                            "reason": "localized numbered special uncertainty",
                        }
                    ],
                }
            ]
        },
    }
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR2", local=[local], status="supplemental", reason="unsupported")]
        ),
        observations=[{"tool": "patch_ledger", "output": repair}],
    )

    rows = _suggested_ledger_patch_rows_from_repair(session, repair)
    assert rows[0]["row_id"] == "LR2"
    assert rows[0]["status"] == "manual_review"
    assert rows[0]["manual_review_candidate_targets"] == [target]

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[ResolutionLedgerRow(row_id="LR2", local=[local], status="supplemental", reason="still unsupported")],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is False
    assert output["issue"] == "patch_ledger_suggested_shape_unaddressed"
    assert output["suggested_ledger_patch_rows"][0]["status"] == "manual_review"


def test_patch_ledger_strong_suggested_shape_guard_rejects_unrelated_patch():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-STRONG-SUGGESTED-PATCH-SKIP"),
        budget=CaseBudget(max_judge_rounds=8),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3"],
            allowed_file_refs=["LF1", "LF2", "LF3"],
            visible_target_refs=["BE1", "BE2", "BE3"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Pack A/SPs/A SP01.mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Pack A/SPs/A SP02.mkv", is_main=True),
            LocalFileCard(ref="LF3", path="Pack B/B 01.mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(ref="BS1", subject_id=1, title="A Specials", eps=2, total_episodes=2),
            BangumiSubjectCard(ref="BS2", subject_id=2, title="B", eps=1, total_episodes=1),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="A1"),
            BangumiItemCard(ref="BE2", subject_ref="BS1", sort=2, ep=2, title="A2"),
            BangumiItemCard(ref="BE3", subject_ref="BS2", sort=1, ep=1, title="B1"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    strong_local = desk["local_locators"][0]["locator"]
    unrelated_local = desk["local_locators"][1]["locator"]
    strong_target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    unrelated_target = f"{registry.subject_locator_by_id[2]}/episode/1"
    repair = {
        "accepted": False,
        "manual_review_strong_non_regular_mapping_repairs": [
            {
                "unit": "LR1",
                "local": [strong_local],
                "issue": "manual_review_strong_non_regular_mapping_should_revise",
                "suggested_submit_shape": [
                    {
                        "local": f"{strong_local}/episodes/1-2",
                        "target": strong_target,
                        "outcome": "mapped_special_or_ova",
                        "reason": "visible same-count target closes ownership",
                    }
                ],
            }
        ],
    }
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(row_id="LR1", local=[strong_local], status="manual_review", reason="pending"),
                ResolutionLedgerRow(row_id="LR2", local=[unrelated_local], status="open"),
            ]
        ),
        observations=[{"tool": "submit", "output": repair}],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR2",
                    local=[unrelated_local],
                    status="mapped",
                    mapped_outcome="mapped_explicit_item",
                    target=unrelated_target,
                    reason="Patch an unrelated row first.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is False
    assert output["accepted"] is False
    assert output["issue"] == "patch_ledger_suggested_shape_unaddressed"
    assert output["unaddressed_suggested_shapes"][0]["issue"] == "patch_ledger_suggested_shape_not_patched"
    manual_shape = output["unaddressed_suggested_shapes"][0]["manual_review_candidate_submit_shape"][0]
    assert manual_shape["local"] == f"{strong_local}/episodes/1-2"
    assert manual_shape["manual_review_candidate_targets"] == [strong_target]
    manual_patch_row = output["manual_review_candidate_ledger_patch_rows"][0]
    assert manual_patch_row["row_id"] == "LR1"
    assert manual_patch_row["local"] == [f"{strong_local}/episodes/1-2"]
    assert manual_patch_row["status"] == "manual_review"
    assert manual_patch_row["manual_review_candidate_targets"] == [strong_target]
    assert "unresolved" in manual_patch_row["reason"]
    persisted_rows = _suggested_ledger_patch_rows_from_repair(session, output)
    assert any(
        row["status"] == "manual_review" and row["manual_review_candidate_targets"] == [strong_target]
        for row in persisted_rows
    )
    assert session.resolution_ledger.rows[1].status == "open"


def test_tool_choice_forces_patch_ledger_for_open_strong_suggested_rows():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="manual_review", reason="pending")]
        ),
        observations=[
            {
                "tool": "submit",
                "output": {
                    "accepted": False,
                    "manual_review_strong_non_regular_mapping_repairs": [
                        {
                            "unit": "LR1",
                            "local": [local],
                            "issue": "manual_review_strong_non_regular_mapping_should_revise",
                            "suggested_submit_shape": [
                                {
                                    "local": f"{local}/episodes/1-2",
                                    "target": target,
                                    "outcome": "mapped_special_or_ova",
                                    "reason": "visible same-count target closes ownership",
                                }
                            ],
                        }
                    ],
                },
            }
        ],
    )

    assert _budget_pressure_tool_choice(session, max_turns=12) == {
        "type": "function",
        "function": {"name": "patch_ledger"},
    }


def test_patch_ledger_strong_suggested_guard_canonicalizes_target_slug_variants():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    slug_variant_target = "target://bangumi/1-show-title-variant/episodes/1-2"
    repair = {
        "accepted": False,
        "manual_review_strong_non_regular_mapping_repairs": [
            {
                "unit": "LR1",
                "local": [local],
                "issue": "manual_review_strong_non_regular_mapping_should_revise",
                "suggested_submit_shape": [
                    {
                        "local": f"{local}/episodes/1-2",
                        "target": target,
                        "outcome": "mapped_special_or_ova",
                        "reason": "visible same-count target closes ownership",
                    }
                ],
            }
        ],
    }
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="manual_review", reason="pending")]
        ),
        observations=[{"tool": "submit", "output": repair}],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episodes/1-2"],
                    status="mapped",
                    mapped_outcome="mapped_special_or_ova",
                    target=slug_variant_target,
                    reason="Map by visible Bangumi id and episode span; slug text may vary.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is True
    assert output["accepted"] is True
    assert output["ledger"]["rows"][0]["target"] == target


def test_patch_ledger_guard_suggested_rows_become_persistent_candidate_debt():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="manual_review", reason="pending")]
        ),
        observations=[
            {
                "tool": "patch_ledger",
                "output": {
                    "accepted": False,
                    "issue": "patch_ledger_suggested_shape_unaddressed",
                    "issue_counts": {"patch_ledger_suggested_shape_unaddressed": 1},
                    "suggested_ledger_patch_rows": [
                        {
                            "row_id": "LR1",
                            "local": [f"{local}/episodes/1-2"],
                            "status": "mapped",
                            "mapped_outcome": "mapped_special_or_ova",
                            "target": target,
                            "reason": "strong mapped template from guard",
                        }
                    ],
                },
            }
        ],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episodes/1-2"],
                    status="manual_review",
                    manual_review_candidate_targets=[target],
                    reason="Target episode range evidence remains unresolved after inspect.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is True
    assert output["accepted"] is True
    assert output["ledger"]["rows"][0]["must_address_candidates"][0]["target"] == target


def test_unaddressed_suggested_shapes_project_to_frontier_choices():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    repair = {
        "accepted": False,
        "issue": "patch_ledger_suggested_shape_unaddressed",
        "issue_counts": {"patch_ledger_suggested_shape_unaddressed": 1},
        "blocking_units": [
            {
                "unit": local,
                "local": [local],
                "issue": "patch_ledger_suggested_shape_unaddressed",
            }
        ],
        "unaddressed_suggested_shapes": [
            {
                "local_parent": local,
                "issue": "patch_ledger_suggested_shape_unaddressed",
                "suggested_submit_shape": [
                    {
                        "local": f"{local}/episodes/1-2",
                        "target": target,
                        "outcome": "mapped_special_or_ova",
                        "reason": "map if ownership closes",
                    }
                ],
                "manual_review_candidate_submit_shape": [
                    {
                        "local": f"{local}/episodes/1-2",
                        "outcome": "manual_review",
                        "manual_review_candidate_targets": [target],
                        "confidence": "low",
                        "reason": "title/count evidence remains unresolved",
                    }
                ],
            }
        ],
    }

    frontier = _repair_frontier_rows_from_agenda(repair)
    choice_rows = _ledger_choice_patch_rows_from_repair(
        HumanCaseSession(
            case_id=workspace.header.case_id,
            resolution_ledger=ResolutionLedger(
                rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="manual_review")]
            ),
        ),
        repair,
    )

    assert frontier[0]["blocker"] == "patch_ledger_suggested_shape_unaddressed"
    assert frontier[0]["suggested_submit_shape"][0]["target"] == target
    assert frontier[0]["manual_review_candidate_submit_shape"][0]["manual_review_candidate_targets"] == [target]
    assert {row["status"] for row in choice_rows} == {"mapped", "manual_review"}


def test_active_repair_agenda_exposes_manual_choice_rows_for_unaddressed_shapes():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="manual_review")]
        ),
        cognitive_workspace=CaseCognitiveWorkspace(
            investigation_agenda=[
                InvestigationAgendaItem(
                    agenda_id="REPAIR-1-unaddressed",
                    status="open",
                    locators=[local],
                    blocking_issue="patch_ledger_suggested_shape_unaddressed",
                    next_action="copy a ledger row choice",
                    closure_condition="candidate debt is discharged",
                )
            ]
        ),
        observations=[
            {
                "tool": "patch_ledger",
                "output": {
                    "accepted": False,
                    "issue": "patch_ledger_suggested_shape_unaddressed",
                    "issue_counts": {"patch_ledger_suggested_shape_unaddressed": 1},
                    "blocking_units": [
                        {
                            "unit": local,
                            "local": [local],
                            "issue": "patch_ledger_suggested_shape_unaddressed",
                        }
                    ],
                    "suggested_ledger_patch_rows": [
                        {
                            "row_id": "LR1",
                            "local": [f"{local}/episodes/1-2"],
                            "status": "mapped",
                            "mapped_outcome": "mapped_special_or_ova",
                            "target": target,
                            "reason": "map if ownership closes",
                        }
                    ],
                    "manual_review_candidate_ledger_patch_rows": [
                        {
                            "row_id": "LR1",
                            "local": [f"{local}/episodes/1-2"],
                            "status": "manual_review",
                            "manual_review_candidate_targets": [target],
                            "confidence": "low",
                            "reason": "title/count evidence remains unresolved",
                        }
                    ],
                    "unaddressed_suggested_shapes": [
                        {
                            "local_parent": local,
                            "issue": "patch_ledger_suggested_shape_unaddressed",
                            "suggested_submit_shape": [
                                {
                                    "local": f"{local}/episodes/1-2",
                                    "target": target,
                                    "outcome": "mapped_special_or_ova",
                                    "reason": "map if ownership closes",
                                }
                            ],
                            "manual_review_candidate_submit_shape": [
                                {
                                    "local": f"{local}/episodes/1-2",
                                    "outcome": "manual_review",
                                    "manual_review_candidate_targets": [target],
                                    "confidence": "low",
                                    "reason": "title/count evidence remains unresolved",
                                }
                            ],
                        }
                    ],
                },
            }
        ],
    )

    agenda = _active_repair_agenda_for_prompt(session)

    assert agenda
    assert agenda[0]["repair_frontier"]["suggested_submit_shape"][0]["target"] == target
    choice_statuses = {row["status"] for row in agenda[0]["ledger_choice_patch_rows"]}
    assert choice_statuses == {"mapped", "manual_review"}


def test_patch_ledger_must_address_nested_compiled_feedback_shape():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    compiled_feedback = {
        "package": {
            "issue_counts": {"manual_review_strong_non_regular_mapping_should_revise": 1},
            "manual_review_strong_non_regular_mapping_repairs": [
                {
                    "unit": "LR1",
                    "local": [local],
                    "issue": "manual_review_strong_non_regular_mapping_should_revise",
                    "suggested_submit_shape": [
                        {
                            "local": f"{local}/episodes/1-2",
                            "target": target,
                            "outcome": "mapped_special_or_ova",
                            "reason": "visible same-count target closes ownership",
                        }
                    ],
                }
            ],
        }
    }
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="manual_review", reason="pending")]
        ),
        observations=[
            {
                "tool": "patch_ledger",
                "output": {
                    "accepted": False,
                    "compiled_submit_accepted": False,
                    "compiled_submit_feedback": compiled_feedback,
                },
            }
        ],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episodes/1-2"],
                    status="manual_review",
                    manual_review_candidate_targets=[target],
                    reason="Target episode range evidence remains unresolved after inspect.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is True
    assert output["accepted"] is True
    assert session.resolution_ledger.rows[0].status == "manual_review"
    assert session.resolution_ledger.rows[0].manual_review_candidate_targets == [target]


def test_patch_ledger_must_address_numbered_special_suggested_shape():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    repair = {
        "accepted": False,
        "package": {
            "issue_counts": {"numbered_special_exclusion_needs_target_evidence": 1},
            "numbered_special_exclusion_repairs": [
                {
                    "unit": "LR1",
                    "local": local,
                    "issue": "numbered_special_exclusion_needs_target_evidence",
                    "suggested_submit_shape": [
                        {
                            "local": f"{local}/episodes/1-2",
                            "target": target,
                            "outcome": "mapped_special_or_ova",
                            "reason": "same-count non-regular target closes ownership",
                        }
                    ],
                }
            ],
        },
    }
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="supplemental", reason="pending")]
        ),
        observations=[{"tool": "patch_ledger", "output": {"compiled_submit_feedback": repair}}],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[local],
                    status="supplemental",
                    reason="Still just bonus material.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is False
    assert output["accepted"] is False
    assert output["issue"] == "patch_ledger_suggested_shape_unaddressed"
    assert output["suggested_ledger_patch_rows"][0]["local"] == [f"{local}/episodes/1-2"]
    assert session.resolution_ledger.rows[0].status == "supplemental"


def test_patch_ledger_guard_requires_numbered_special_manual_review_template():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    candidate = registry.subject_locator_by_id[1]
    repair = {
        "accepted": False,
        "package": {
            "issue_counts": {"numbered_special_exclusion_needs_target_evidence": 1},
            "numbered_special_exclusion_repairs": [
                {
                    "unit": "LR1",
                    "local": local,
                    "issue": "numbered_special_exclusion_needs_target_evidence",
                    "manual_review_candidate_submit_shape": [
                        {
                            "local": local,
                            "outcome": "manual_review",
                            "manual_review_candidate_targets": [candidate],
                            "confidence": "low",
                            "reason": "Numbered SP ownership remains localized uncertainty.",
                        }
                    ],
                }
            ],
        },
    }
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="supplemental", reason="pending")]
        ),
        observations=[{"tool": "patch_ledger", "output": {"compiled_submit_feedback": repair}}],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[local],
                    status="supplemental",
                    reason="Still unsupported supplemental.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is False
    assert output["accepted"] is False
    assert output["issue"] == "patch_ledger_suggested_shape_unaddressed"
    assert output["suggested_ledger_patch_rows"][0]["status"] == "manual_review"
    assert output["suggested_ledger_patch_rows"][0]["manual_review_candidate_targets"] == [candidate]
    assert session.resolution_ledger.rows[0].status == "supplemental"


def test_suggested_ledger_patch_rows_preserve_split_row_id_from_issue():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episode/2"
    split_local = f"{local}/episode/2"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(row_id="LR1", local=[local], status="manual_review", reason="parent"),
                ResolutionLedgerRow(row_id="LR1_split_2", local=[f"{local}/episode/1"], status="manual_review"),
                ResolutionLedgerRow(row_id="LR1_split_4", local=[split_local], status="manual_review"),
            ]
        ),
    )
    repair = {
        "accepted": False,
        "issue_counts": {"ledger_candidate_debt_open": 1},
        "units": [
            {
                "unit": "LR1_split_4",
                "local": [split_local],
                "issues": [
                    {
                        "issue": "ledger_candidate_debt_open",
                        "row_id": "LR1_split_4",
                        "local": [split_local],
                        "suggested_submit_shape": [
                            {
                                "local": split_local,
                                "target": target,
                                "outcome": "mapped_special_or_ova",
                                "reason": "close the exact split debt",
                            }
                        ],
                    }
                ],
            }
        ],
    }

    rows = _suggested_ledger_patch_rows_from_repair(session, repair)

    assert rows[0]["row_id"] == "LR1_split_4"
    assert rows[0]["local"] == [split_local]
    assert rows[0]["target"] == target


def test_turn_tail_preserves_manual_review_candidate_ledger_patch_rows():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    manual_patch_row = {
        "row_id": "LR1",
        "local": [f"{local}/episodes/1-2"],
        "status": "manual_review",
        "manual_review_candidate_targets": [target],
        "reason": "Target title evidence remains unresolved for this exact slice.",
        "confidence": "low",
    }
    mapped_patch_row = {
        "row_id": "LR1",
        "local": [f"{local}/episodes/1-2"],
        "status": "mapped",
        "mapped_outcome": "mapped_special_or_ova",
        "target": target,
        "reason": "visible candidate debt",
        "confidence": "high",
    }
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="manual_review")]
        ),
        observations=[
            {
                "tool": "patch_ledger",
                "output": {
                    "accepted": False,
                    "issue_counts": {"ledger_candidate_debt_open": 1},
                    "ledger": {"status_counts": {"manual_review": 1}, "rows": []},
                    "suggested_ledger_patch_rows": [mapped_patch_row],
                    "must_address_suggested_ledger_patch_rows": [mapped_patch_row],
                    "manual_review_candidate_ledger_patch_rows": [manual_patch_row],
                    "required_next_action": "copy the candidate discharge rows",
                },
            }
        ],
    )

    tail = _turn_tail({"resolution_contract": {"must_account_locator_count": 2}}, session, max_turns=10)
    latest_output = tail["case_memory"]["latest_submit_repair"]["output"]
    turn_budget = tail["case_memory"]["turn_budget"]
    assert latest_output["manual_review_candidate_ledger_patch_rows"] == [manual_patch_row]
    assert turn_budget["manual_review_candidate_ledger_patch_rows"] == [manual_patch_row]


def test_turn_tail_exposes_unified_ledger_choice_patch_rows():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="manual_review")]
        ),
        observations=[
            {
                "tool": "submit",
                "output": {
                    "accepted": False,
                    "issue_counts": {"excluded_title_tail_unresolved_after_search": 1},
                    "repair_frontier": [
                        {
                            "blocker": "excluded_title_tail_unresolved_after_search",
                            "local": [local],
                            "manual_review_candidate_submit_shape": [
                                {
                                    "local": local,
                                    "outcome": "manual_review",
                                    "manual_review_candidate_targets": [target],
                                    "reason": "Target title surface remains unresolved for this exact locator.",
                                }
                            ],
                            "fail_closed_submit_shape": [
                                {
                                    "local": local,
                                    "outcome": "fail_closed",
                                    "reason": "Exact locator remains unresolved after exhausted bridge evidence.",
                                }
                            ],
                        }
                    ],
                },
            }
        ],
    )

    tail = _turn_tail({"resolution_contract": {"must_account_locator_count": 2}}, session, max_turns=10)
    rows = tail["case_memory"]["turn_budget"]["ledger_choice_patch_rows"]

    assert [row["status"] for row in rows] == ["manual_review", "fail_closed"]
    assert rows[0]["row_id"] == "LR1"
    assert rows[0]["manual_review_candidate_targets"] == [target]


def test_repair_frontier_source_keys_cover_submit_repair_buckets():
    assert set(SUBMIT_REPAIR_GROUP_KEYS).issubset(set(REPAIR_FRONTIER_SOURCE_KEYS))


def test_ledger_choice_rows_keep_mapped_and_manual_review_options_for_same_local():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="open")]),
    )
    repair = {
        "repair_frontier": [
            {
                "blocker": "manual_review_strong_non_regular_mapping_should_revise",
                "suggested_submit_shape": [
                    {
                        "local": f"{local}/episodes/1-2",
                        "target": target,
                        "outcome": "mapped_special_or_ova",
                        "reason": "strong mapped choice",
                    }
                ],
                "manual_review_candidate_submit_shape": [
                    {
                        "local": f"{local}/episodes/1-2",
                        "outcome": "manual_review",
                        "manual_review_candidate_targets": [target],
                        "reason": "exact unresolved target choice",
                    }
                ],
            }
        ]
    }

    rows = _ledger_choice_patch_rows_from_repair(session, repair)

    assert [row["status"] for row in rows[:2]] == ["mapped", "manual_review"]
    assert rows[0]["local"] == [f"{local}/episodes/1-2"]
    assert rows[1]["local"] == [f"{local}/episodes/1-2"]


def test_strong_suggested_shape_recovers_from_row_shaped_candidate_debt():
    repair = {
        "accepted": False,
        "issue_counts": {"ledger_candidate_debt_open": 1},
        "must_address_suggested_ledger_patch_rows": [
            {
                "row_id": "LR0",
                "local": ["local://pack/special-marker/episodes/1-2"],
                "status": "mapped",
                "mapped_outcome": "mapped_special_or_ova",
                "target": "target://bangumi/1-short/episodes/1-13",
                "reason": "stale broad candidate debt",
            },
            {
                "row_id": "LR1",
                "local": ["local://pack/special-marker/episodes/1-2"],
                "status": "mapped",
                "mapped_outcome": "mapped_special_or_ova",
                "target": "target://bangumi/1-short/episodes/1-2",
                "reason": "visible candidate debt",
            }
        ],
    }

    rows = _strong_suggested_submit_shape_rows_from_repair(repair)

    assert rows == [
        {
            "local": "local://pack/special-marker/episodes/1-2",
            "target": "target://bangumi/1-short/episodes/1-2",
            "outcome": "mapped_special_or_ova",
            "reason": "visible candidate debt",
            "row_id": "LR1",
        }
    ]


def test_patch_ledger_imports_suggested_shape_as_candidate_debt():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="open")]
        ),
        observations=[
            {
                "tool": "submit",
                "output": {
                    "accepted": False,
                    "repair_frontier": [
                        {
                            "suggested_submit_shape": [
                                {
                                    "local": f"{local}/episodes/1-2",
                                    "target": target,
                                    "outcome": "mapped_special_or_ova",
                                    "reason": "visible suggested target must be addressed",
                                }
                            ]
                        }
                    ],
                },
            }
        ],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[local],
                    status="supplemental",
                    reason="Treat as extras without addressing the visible target.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is False
    assert output["accepted"] is False
    assert output["issue_counts"]["ledger_candidate_debt_open"] == 1
    manual_patch_row = output["manual_review_candidate_ledger_patch_rows"][0]
    assert manual_patch_row["local"] == [f"{local}/episodes/1-2"]
    assert manual_patch_row["manual_review_candidate_targets"] == [target]


def test_patch_ledger_saves_valid_slice_when_only_remaining_issue_is_coverage_missing():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    subject = registry.subject_locator_by_id[1]
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="open")]
        ),
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episode/2"],
                    status="mapped",
                    mapped_outcome="mapped_explicit_item",
                    target=f"{subject}/episode/2",
                    reason="Resolve one exact slice first; sibling coverage remains open.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is False
    assert output["accepted"] is True
    assert output["partial"] is True
    assert output["save_issue_counts"] == {"ledger_coverage_missing": 1}
    assert session.resolution_ledger.rows[0].local == [f"{local}/episode/2"]
    assert session.resolution_ledger.rows[0].status == "mapped"


def test_patch_ledger_projection_preserves_save_issue_suggested_rows():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    output = {
        "accepted": True,
        "partial": True,
        "issue_counts": {"ledger_coverage_missing": 1},
        "issues": [
            {
                "issue": "ledger_coverage_missing",
                "file_refs": ["LF1"],
            }
        ],
        "save_issues": [
            {
                "issue": "ledger_candidate_debt_open",
                "row_id": "LR1",
                "candidate_target": target,
                "suggested_submit_shape": [
                    {
                        "local": f"{local}/episodes/1-2",
                        "target": target,
                        "outcome": "mapped_special_or_ova",
                        "reason": "saved candidate debt still needs a row choice",
                    }
                ],
            }
        ],
    }
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="open")]),
    )

    rows = _suggested_ledger_patch_rows_from_repair(session, output)

    assert rows[0]["row_id"] == "LR1"
    assert rows[0]["local"] == [f"{local}/episodes/1-2"]
    assert rows[0]["target"] == target


def test_patch_ledger_coverage_missing_guard_requires_visible_missing_shape():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episode/1"
    existing_target = f"{registry.subject_locator_by_id[1]}/episode/2"
    missing_shape = {
        "local": f"{local}/episode/1",
        "target": target,
        "outcome": "mapped_explicit_item",
        "reason": "Visible title-pairing candidate for the missing local slice.",
    }
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR2",
                    local=[f"{local}/episode/2"],
                    status="mapped",
                    mapped_outcome="mapped_explicit_item",
                    target=existing_target,
                    reason="Second slice is already resolved.",
                )
            ]
        ),
        observations=[
            {
                "tool": "patch_ledger",
                "output": {
                    "accepted": False,
                    "issue_counts": {"ledger_coverage_missing": 1},
                    "repair_frontier": [
                        {
                            "local": [],
                            "blocker": "ledger_coverage_missing",
                            "suggested_submit_shape": [missing_shape],
                        }
                    ],
                },
            }
        ],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR2",
                    local=[f"{local}/episode/2"],
                    status="mapped",
                    mapped_outcome="mapped_explicit_item",
                    target=existing_target,
                    reason="Repeat the already resolved slice instead of covering the missing one.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is False
    assert output["accepted"] is False
    assert output["issue"] == "patch_ledger_suggested_shape_unaddressed"
    assert output["suggested_ledger_patch_rows"][0]["local"] == [f"{local}/episode/1"]
    assert output["suggested_ledger_patch_rows"][0]["target"] == target


def test_patch_ledger_coverage_missing_guard_allows_exact_candidate_manual_review():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episode/1"
    existing_target = f"{registry.subject_locator_by_id[1]}/episode/2"
    missing_shape = {
        "local": f"{local}/episode/1",
        "target": target,
        "outcome": "mapped_explicit_item",
        "reason": "Visible title-pairing candidate for the missing local slice.",
    }
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR2",
                    local=[f"{local}/episode/2"],
                    status="mapped",
                    mapped_outcome="mapped_explicit_item",
                    target=existing_target,
                    reason="Second slice is already resolved.",
                )
            ]
        ),
        observations=[
            {
                "tool": "patch_ledger",
                "output": {
                    "accepted": False,
                    "issue_counts": {"ledger_coverage_missing": 1},
                    "repair_frontier": [
                        {
                            "local": [],
                            "blocker": "ledger_coverage_missing",
                            "suggested_submit_shape": [missing_shape],
                        }
                    ],
                },
            }
        ],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episode/1"],
                    status="manual_review",
                    manual_review_candidate_targets=[target],
                    reason="Target episode title surface remains unresolved after inspecting the missing slice.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is True
    assert output["accepted"] is True
    assert {row.row_id for row in session.resolution_ledger.rows} == {"LR1", "LR2"}
    assert session.resolution_ledger.rows[1].status == "manual_review"
    assert session.resolution_ledger.rows[1].manual_review_candidate_targets == [target]


def test_patch_ledger_mapped_row_discharges_imported_suggested_debt():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="open")]
        ),
        observations=[
            {
                "tool": "submit",
                "output": {
                    "accepted": False,
                    "repair_frontier": [
                        {
                            "suggested_submit_shape": [
                                {
                                    "local": f"{local}/episodes/1-2",
                                    "target": target,
                                    "outcome": "mapped_special_or_ova",
                                    "reason": "visible suggested target must be addressed",
                                }
                            ]
                        }
                    ],
                },
            }
        ],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episodes/1-2"],
                    status="mapped",
                    mapped_outcome="mapped_special_or_ova",
                    target=target,
                    reason="Map to the visible suggested target.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is True
    assert output["accepted"] is True
    assert output["ledger"]["rows"][0]["must_address_candidates"][0]["target"] == target


def test_patch_ledger_preserved_strong_debt_is_discharged_by_candidate_manual_review():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[local],
                    status="candidate_must_address",
                    must_address_candidates=[
                        ResolutionLedgerCandidateDebt(
                            target=target,
                            source="manual_review_strong_non_regular_mapping_should_revise",
                            mapped_outcome="mapped_special_or_ova",
                            support=[f"{local}/episodes/1-2"],
                            reason="strong local duration/count closure",
                        )
                    ],
                )
            ]
        ),
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episodes/1-2"],
                    status="manual_review",
                    manual_review_candidate_targets=[target],
                    reason="Duration/title evidence remains unresolved after inspect.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is True
    assert output["accepted"] is True
    assert output["ledger"]["rows"][0]["manual_review_candidate_targets"] == [target]


def test_patch_ledger_rejects_broad_strong_debt_manual_review_without_concrete_anchor():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[local],
                    status="candidate_must_address",
                    must_address_candidates=[
                        ResolutionLedgerCandidateDebt(
                            target=target,
                            source="manual_review_strong_non_regular_mapping_should_revise",
                            mapped_outcome="mapped_special_or_ova",
                            support=[f"{local}/episodes/1-2"],
                            reason="strong local duration/count closure",
                        )
                    ],
                )
            ]
        ),
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[local],
                    status="manual_review",
                    manual_review_candidate_targets=[target],
                    reason="The visible same-count target remains unresolved after inspection.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is False
    assert output["accepted"] is False
    assert output["issue_counts"]["ledger_candidate_debt_open"] == 1
    assert output["suggested_ledger_patch_rows"][0]["target"] == target


def test_patch_ledger_preserved_strong_debt_is_discharged_by_mapping():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[local],
                    status="candidate_must_address",
                    must_address_candidates=[
                        ResolutionLedgerCandidateDebt(
                            target=target,
                            source="manual_review_strong_non_regular_mapping_should_revise",
                            mapped_outcome="mapped_special_or_ova",
                            support=[f"{local}/episodes/1-2"],
                            reason="strong local duration/count closure",
                        )
                    ],
                )
            ]
        ),
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episodes/1-2"],
                    status="mapped",
                    mapped_outcome="mapped_special_or_ova",
                    target=target,
                    reason="Map the strong candidate debt.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is True
    assert output["accepted"] is True
    assert output["ledger"]["rows"][0]["must_address_candidates"][0]["target"] == target


def test_patch_ledger_generic_candidate_debt_allows_candidate_manual_review():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[local],
                    status="candidate_must_address",
                    must_address_candidates=[
                        ResolutionLedgerCandidateDebt(
                            target=target,
                            source="repair_frontier.suggested_submit_shape",
                            mapped_outcome="mapped_special_or_ova",
                            support=[f"{local}/episodes/1-2"],
                            reason="visible candidate must be carried if unresolved",
                        )
                    ],
                )
            ]
        ),
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episodes/1-2"],
                    status="manual_review",
                    manual_review_candidate_targets=[target],
                    reason="Target episode range evidence remains unresolved after inspect.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is True
    assert output["accepted"] is True
    assert output["ledger"]["rows"][0]["manual_review_candidate_targets"] == [target]


def test_patch_ledger_repair_frontier_strong_blocker_debt_allows_exact_candidate_manual_review():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episodes/1-2"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="open")]
        ),
        observations=[
            {
                "tool": "patch_ledger",
                "output": {
                    "accepted": False,
                    "repair_frontier": [
                        {
                            "blocker": "manual_review_strong_non_regular_mapping_should_revise",
                            "suggested_submit_shape": [
                                {
                                    "local": f"{local}/episodes/1-2",
                                    "target": target,
                                    "outcome": "mapped_special_or_ova",
                                    "reason": "visible strong closure must be addressed",
                                }
                            ],
                        }
                    ],
                },
            }
        ],
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episodes/1-2"],
                    status="manual_review",
                    manual_review_candidate_targets=[target],
                    reason="Target episode range evidence remains unresolved after inspect.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )

    assert complete is True
    assert output["accepted"] is True
    assert output["ledger"]["rows"][0]["manual_review_candidate_targets"] == [target]


def test_patch_ledger_rejection_preserves_repair_buckets_and_choice_rows():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-COMPILED-REPAIR-PERSISTS"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Pack/Movie Pack [01(First Arc)].mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Pack/Movie Pack [02(Second Arc)].mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=601,
                title="Movie Pack First Arc",
                name="Movie Pack First Arc",
                name_cn="Movie Pack First Arc",
                eps=1,
                total_episodes=1,
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=602,
                title="Movie Pack Second Arc",
                name="Movie Pack Second Arc",
                name_cn="Movie Pack Second Arc",
                eps=1,
                total_episodes=1,
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="First Arc"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Second Arc"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    first_target = f"{registry.subject_locator_by_id[601]}/episode/1"
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(
            rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="open")]
        ),
    )

    session, output, complete = _patch_ledger_tool(
        registry,
        session,
        PatchLedgerToolArgs(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episode/1"],
                    status="mapped",
                    mapped_outcome="mapped_explicit_item",
                    target=first_target,
                    reason="First slice maps cleanly.",
                ),
                ResolutionLedgerRow(
                    row_id="LR2",
                    local=[f"{local}/episode/2"],
                    status="supplemental",
                    reason="Leftover second slice without hard non-owner reason.",
                )
            ],
            repair_strategy="revise_saved_rows",
        ),
        main_refs=list(workspace.contract.main_file_refs),
    )
    submit_result = _compile_resolution_ledger_to_submit_result(workspace, registry, session.resolution_ledger)
    compiled_agenda = _repair_agenda_from_submit_feedback(submit_result.feedback, repeated=False)
    output = {
        **output,
        "accepted": False,
        "issue_counts": submit_result.feedback["package"]["issue_counts"],
        "repair_frontier": compiled_agenda["repair_frontier"],
        **{
            key: compiled_agenda.get(key)
            for key in ("excluded_slice_mapped_sibling_repairs",)
            if compiled_agenda.get(key)
        },
    }
    output["ledger_choice_patch_rows"] = _ledger_choice_patch_rows_from_repair(session, output)

    assert complete is True
    assert submit_result.accepted is False
    assert output["excluded_slice_mapped_sibling_repairs"]
    assert output["repair_frontier"]
    assert output["ledger_choice_patch_rows"]


def test_ledger_duplicate_target_exposes_manual_review_candidate_shape():
    workspace = _two_episode_workspace()
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[1]}/episode/1"
    result = _compile_resolution_ledger_to_submit_result(
        workspace,
        registry,
        ResolutionLedger(
            rows=[
                ResolutionLedgerRow(
                    row_id="LR1",
                    local=[f"{local}/episode/1"],
                    status="mapped",
                    mapped_outcome="mapped_explicit_item",
                    target=target,
                    reason="first claimant",
                ),
                ResolutionLedgerRow(
                    row_id="LR2",
                    local=[f"{local}/episode/2"],
                    status="mapped",
                    mapped_outcome="mapped_explicit_item",
                    target=target,
                    reason="second claimant",
                ),
            ]
        ),
    )

    assert result.accepted is False
    assert result.feedback["issue_counts"]["ledger_duplicate_target"] == 1
    units = {unit["unit"]: unit for unit in result.feedback["units"]}
    assert units["LR1"]["manual_review_candidate_submit_shape"][0]["manual_review_candidate_targets"] == [target]
    assert units["LR2"]["manual_review_candidate_submit_shape"][0]["manual_review_candidate_targets"] == [target]


def test_duplicate_variant_repair_ignores_plain_regular_season_candidate():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SP-DUPLICATE-VARIANT-CANDIDATE-QUALITY"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2", "LF3", "LF4"],
            allowed_file_refs=["LF1", "LF2", "LF3", "LF4"],
            visible_target_refs=["BE1", "BE2", "BE3"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Pack/SPs/[Group] Play Play Stars [SP01].mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Pack/SPs/[Group] Play Play Stars [SP02_1].mkv", is_main=True),
            LocalFileCard(ref="LF3", path="Pack/SPs/[Group] Play Play Stars [SP02_2].mkv", is_main=True),
            LocalFileCard(ref="LF4", path="Pack/SPs/[Group] Play Play Stars [SP03].mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=191,
                title="Franchise Regular Season",
                name="Franchise Regular Season",
                name_cn="Franchise Regular Season",
                eps=3,
                total_episodes=3,
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=192,
                title="Play Play Stars",
                name="Play Play Stars",
                name_cn="Play Play Stars",
                eps=3,
                total_episodes=3,
                relation_to_main="side_story",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref="BS2", sort=index, ep=index, title=f"Short {index}")
            for index in range(1, 4)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    regular_subject = registry.subject_locator_by_id[191]
    related_subject = registry.subject_locator_by_id[192]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-duplicate-group-review",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[regular_subject, related_subject],
                        reason="Only the duplicate SP02 variant choice is unresolved.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    repair = result.feedback["package"]["manual_review_duplicate_variant_repairs"][0]
    assert repair["candidate_targets"][0]["target"] == related_subject
    assert all(row["target"].startswith(related_subject) for row in repair["multi_version_submit_shape"])


def test_duplicate_variant_repair_uses_manual_review_candidate_and_season_hint():
    local_files = [
        LocalFileCard(
            ref=f"LF{index}",
            path=f"Pack/SPs/[Group] Franchise II [SP{index:02d}].mkv",
            is_main=True,
        )
        for index in range(1, 8)
    ]
    local_files.extend(
        [
            LocalFileCard(ref="LF8", path="Pack/SPs/[Group] Franchise II [SP08_1].mkv", is_main=True),
            LocalFileCard(ref="LF9", path="Pack/SPs/[Group] Franchise II [SP08_2].mkv", is_main=True),
        ]
    )
    local_files.extend(
        [
            LocalFileCard(
                ref=f"LF{index + 1}",
                path=f"Pack/SPs/[Group] Franchise II [SP{index:02d}].mkv",
                is_main=True,
            )
            for index in range(9, 14)
        ]
    )
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SP-DUPLICATE-VARIANT-SEASON-CANDIDATE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=[f"LF{index}" for index in range(1, 15)],
            allowed_file_refs=[f"LF{index}" for index in range(1, 15)],
            visible_target_refs=[],
        ),
        local_files=local_files,
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS2",
                subject_id=202,
                title="Play Play Stars 2",
                name="Play Play Stars 2",
                name_cn="Play Play Stars 2",
                eps=13,
                total_episodes=13,
                relation_to_main="side_story",
            ),
            BangumiSubjectCard(
                ref="BS4",
                subject_id=204,
                title="Play Play Stars 4",
                name="Play Play Stars 4",
                name_cn="Play Play Stars 4",
                eps=13,
                total_episodes=13,
                relation_to_main="side_story",
            ),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    season_two_subject = registry.subject_locator_by_id[202]
    season_four_subject = registry.subject_locator_by_id[204]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-duplicate-review",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[season_two_subject],
                        reason="Duplicate SP08 variant choice remains unresolved.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    repair = result.feedback["package"]["manual_review_duplicate_variant_repairs"][0]
    assert repair["candidate_targets"][0]["target"] == season_two_subject
    assert all(row["target"].startswith(season_two_subject) for row in repair["multi_version_submit_shape"])
    assert all(row["target"] != season_four_subject for row in repair["candidate_targets"])


def test_parent_manual_review_with_full_slice_pairings_must_split():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-MOVIE-SLICE-PAIRING-MANUAL"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/Gekijouban Franchise/[Group] Gekijouban Franchise [01(Alpha Movie)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/Gekijouban Franchise/[Group] Gekijouban Franchise [02(Beta Movie)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=101,
                title="Franchise Alpha Movie",
                name="Franchise Alpha Movie",
                name_cn="Franchise Alpha Movie",
                eps=1,
                total_episodes=1,
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=102,
                title="Franchise Beta Movie",
                name="Franchise Beta Movie",
                name_cn="Franchise Beta Movie",
                eps=1,
                total_episodes=1,
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Movie"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Movie"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    subject_1 = registry.subject_locator_by_id[101]
    subject_2 = registry.subject_locator_by_id[102]

    manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="movie-parent-manual",
                        local=[local_locator],
                        outcome="manual_review",
                        reason="Preserve saved manual-review placeholder.",
                    )
                ]
            )
        ),
    )

    assert manual_result.accepted is False
    assert manual_result.feedback["package"]["issue_counts"][
        "manual_review_visible_slice_pairing_should_split"
    ] == 1
    repair = manual_result.feedback["package"]["manual_review_visible_slice_pairing_repairs"][0]
    assert [row["local"] for row in repair["suggested_submit_shape"]] == [
        f"{local_locator}/episode/1",
        f"{local_locator}/episode/2",
    ]

    fail_closed_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="movie-parent-fail-closed",
                        local=[local_locator],
                        outcome="fail_closed",
                        reason="The parent movie bundle remains unresolved.",
                    )
                ]
            )
        ),
    )

    assert fail_closed_result.accepted is False
    fail_closed_repair = fail_closed_result.feedback["package"]["fail_closed_slice_pairing_repairs"][0]
    assert [row["local"] for row in fail_closed_repair["suggested_submit_shape"]] == [
        f"{local_locator}/episode/1",
        f"{local_locator}/episode/2",
    ]

    exact_fail_closed_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="beta-movie-slice-fail-closed",
                        local=[f"{local_locator}/episode/2"],
                        outcome="fail_closed",
                        reason="The exact Beta Movie slice remains unresolved.",
                    )
                ]
            )
        ),
    )

    assert exact_fail_closed_result.accepted is False
    exact_fail_closed_repair = exact_fail_closed_result.feedback["package"]["fail_closed_slice_pairing_repairs"][0]
    assert exact_fail_closed_repair["suggested_submit_shape"][0]["local"] == f"{local_locator}/episode/2"
    assert exact_fail_closed_repair["suggested_submit_shape"][0]["target"] == f"{subject_2}/episode/1"

    slice_manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="beta-movie-slice-manual",
                        local=[f"{local_locator}/episode/2"],
                        outcome="manual_review",
                        support=[subject_2],
                        reason="Visible slice title is Beta Movie, but the paired target was not addressed.",
                    )
                ]
            )
        ),
    )

    assert slice_manual_result.accepted is False
    assert slice_manual_result.feedback["package"]["issue_counts"][
        "manual_review_visible_slice_pairing_should_split"
    ] == 1
    slice_repair = slice_manual_result.feedback["package"]["manual_review_visible_slice_pairing_repairs"][0]
    assert slice_repair["suggested_submit_shape"][0]["local"] == f"{local_locator}/episode/2"
    assert slice_repair["suggested_submit_shape"][0]["target"] == f"{subject_2}/episode/1"

    addressed_slice_manual_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="alpha-movie-addressed-context",
                        local=[f"{local_locator}/episode/1"],
                        outcome="mapped_explicit_item",
                        target=f"{subject_1}/episode/1",
                        reason="Local slice title matches the visible Alpha Movie subject.",
                    ),
                        ResolutionWorkUnit(
                            unit_label="beta-movie-slice-manual-addressed",
                            local=[f"{local_locator}/episode/2"],
                            outcome="manual_review",
                            manual_review_candidate_targets=[f"{subject_2}/episode/1"],
                            confidence="low",
                            reason=(
                                "The visible Beta Movie candidate has a concrete title mismatch against inspected "
                                "local title-card evidence after comparison; manual review should keep the candidate "
                                "without assigning ownership."
                            ),
                        )
                ]
            )
        ),
    )

    assert addressed_slice_manual_result.accepted is True
    assert addressed_slice_manual_result.feedback["manual_review_file_count"] == 1
    addressed_manual_unit = next(
        unit for unit in addressed_slice_manual_result.feedback["units"]
        if unit.get("outcome") == "manual_review"
    )
    assert addressed_manual_unit["review_candidate_targets"] == [
        f"{subject_2}/episode/1"
    ]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="alpha-movie",
                        local=[f"{local_locator}/episode/1"],
                        outcome="mapped_explicit_item",
                        target=f"{subject_1}/episode/1",
                        reason="Local slice title matches the visible Alpha Movie subject.",
                    ),
                    ResolutionWorkUnit(
                        unit_label="beta-movie",
                        local=[f"{local_locator}/episode/2"],
                        outcome="mapped_explicit_item",
                        target=f"{subject_2}/episode/1",
                        reason="Local slice title matches the visible Beta Movie subject.",
                    ),
                ]
            )
        ),
    )

    assert result.accepted is True
    assert result.feedback["mapped_file_count"] == 2


def test_subtitle_compact_is_on_demand_not_default_local_facts():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SUBTITLE-COMPACT-ON-DEMAND"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1"],
            allowed_file_refs=["LF1"],
            visible_target_refs=[],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/SPs/[Group] Franchise [SP01].mkv",
                is_main=True,
                subtitle_facts={
                    "external_subtitle_refs": [
                        {"file_id": "sub_001", "relative_path": "Pack/SPs/[Group] Franchise [SP01].chs.ass"}
                    ],
                    "language_markers": ["chs"],
                    "bounded_text_snippets": [{"source_ref": "sub_001", "text": "Play1 Test title"}],
                    "snippet_source": "external_subtitle_file",
                },
            )
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    local_locator = desk["local_locators"][0]["locator"]

    _workspace, facts_result = _inspect_tool(
        workspace,
        registry,
        None,
        InspectToolArgs(locators=[local_locator], scope=["facts"]),
    )
    fact_cards = facts_result["observations"][0]["local_fact_cards"]
    assert fact_cards[0]["subtitle_facts"]["bounded_text_snippets"] == []

    _workspace, subtitle_result = _inspect_tool(
        workspace,
        registry,
        None,
        InspectToolArgs(locators=[local_locator], scope=["subtitle_compact"]),
    )
    subtitle_cards = subtitle_result["observations"][0]["subtitle_compact_cards"]
    assert subtitle_cards[0]["bounded_text_snippets"][0]["text"] == "Play1 Test title"
    assert subtitle_cards[0]["compact_policy"]["on_demand_only"] is True


def test_mapped_numbered_special_can_use_subtitle_compact_title_overlap():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SP-SUBTITLE-COMPACT-CLOSURE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/SPs/[Group] Franchise [SP01].mkv",
                is_main=True,
                subtitle_facts={
                    "bounded_text_snippets": [{"source_ref": "sub1", "text": "Play Play Stars"}],
                    "snippet_source": "external_subtitle_file",
                },
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/SPs/[Group] Franchise [SP02].mkv",
                is_main=True,
                subtitle_facts={
                    "bounded_text_snippets": [{"source_ref": "sub2", "text": "Play2 Short title"}],
                    "snippet_source": "external_subtitle_file",
                },
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(ref="BS1", subject_id=71, title="Play Play Stars", eps=2, total_episodes=2),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Short 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS1", sort=2, ep=2, title="Short 2"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    target = f"{registry.subject_locator_by_id[71]}/episodes/1-2"

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-subtitle-title-overlap",
                        local=[f"{local_locator}/episodes/1-2"],
                        outcome="mapped_special_or_ova",
                        target=target,
                        reason="Mapped using visible same-count short target.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is True
    assert result.feedback["mapped_file_count"] == 2


def test_manual_review_candidate_targets_are_review_hints_not_mappings():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-MANUAL-REVIEW-CANDIDATE-HINT"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1"],
            allowed_file_refs=["LF1"],
            visible_target_refs=["BE1"],
        ),
        local_files=[LocalFileCard(ref="LF1", path="Pack/SPs/[Group] Franchise [SP01].mkv", is_main=True)],
        bangumi_subjects=[
            BangumiSubjectCard(ref="BS1", subject_id=71, title="Play Play Stars", eps=1, total_episodes=1),
        ],
        bangumi_items=[BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Short 1")],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    candidate = f"{registry.subject_locator_by_id[71]}/episode/1"

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-review",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[candidate],
                        confidence="low",
                        reason="Plausible related short target, but ownership is not proved.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is True
    assert result.output is not None
    assignment = result.output.assignment_intents[0]
    assert assignment.target_ref == "UNALIGNED"
    assert assignment.target_refs == []
    assert result.mapped_file_count == 0
    assert result.manual_review_file_count == 1
    assert result.feedback["units"][0]["review_candidate_targets"] == [candidate]

    invalid_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="sp-review-invalid",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=["target://bangumi/not-visible"],
                        confidence="low",
                        reason="Plausible related short target, but ownership is not proved.",
                    )
                ]
            )
        ),
    )

    assert invalid_result.accepted is False
    assert invalid_result.feedback["units"][0]["issues"][0]["review_hint_only"] is True


def test_unseasoned_singleton_mapping_to_season_target_is_repaired():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SEASON-MISMATCH"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1"],
            allowed_file_refs=["LF1"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Companion/Companion Stars.mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=31,
                title="Companion Stars",
                name="Companion Stars",
                name_cn="Companion Stars",
                eps=1,
                total_episodes=1,
                search_query_ref="Companion Stars",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=32,
                title="Companion Stars 4",
                name="Companion Stars 4",
                name_cn="Companion Stars 4",
                eps=1,
                total_episodes=1,
                search_query_ref="Companion Stars",
            ),
            BangumiSubjectCard(
                ref="BS3",
                subject_id=33,
                title="Companion Archive",
                name="Companion Archive",
                name_cn="Companion Archive",
                eps=1,
                total_episodes=1,
                search_query_ref="Companion Stars",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Episode 1"),
            BangumiItemCard(ref="BE3", subject_ref="BS3", sort=1, ep=1, title="Episode 1"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    season_target = f"{registry.subject_locator_by_id[32]}/episode/1"

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="unseasoned-to-season-four",
                        local=[desk["local_locators"][0]["locator"]],
                        outcome="mapped_explicit_item",
                        target=season_target,
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"]["mapped_title_season_mismatch"] == 1
    repair = result.feedback["package"]["mapped_title_season_mismatch_repairs"][0]
    assert repair["target"] == season_target
    assert repair["visible_unseasoned_alternates"][0]["target"] == registry.subject_locator_by_id[31]
    assert all(item["target"] != registry.subject_locator_by_id[33] for item in repair["visible_unseasoned_alternates"])
    assert repair["search_queries_to_try"] == ["Companion Stars"]

    agenda = _repair_agenda_from_submit_feedback(result.feedback, repeated=False)
    assert agenda["mapped_title_season_mismatch_repairs"]
    assert "Companion Stars" in agenda["search_queries_to_try"]
    assert "mapped_title_season_mismatch_repairs" in agenda["required_next_action"]


def test_excluded_episode_slice_with_mapped_sibling_requires_concrete_reason():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-EXCLUDED-SLICE-SIBLING"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Movie Pack/Movie Pack [01(First Arc)].mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Movie Pack/Movie Pack [02(Second Arc)].mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=41,
                title="Movie Pack First Arc",
                name="Movie Pack First Arc",
                name_cn="Movie Pack First Arc",
                eps=1,
                total_episodes=1,
                search_query_ref="Movie Pack First Arc",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=42,
                title="Movie Pack Second Arc",
                name="Movie Pack Second Arc",
                name_cn="Movie Pack Second Arc",
                eps=1,
                total_episodes=1,
                search_query_ref="Movie Pack Second Arc",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="First Arc"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Second Arc"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    parent = desk["local_locators"][0]["locator"]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="part-one",
                        local=[f"{parent}/episode/1"],
                        outcome="mapped_explicit_item",
                        target="target://bangumi/41-movie-pack-first-arc/episode/1",
                    ),
                    ResolutionWorkUnit(
                        unit_label="part-two-leftover",
                        local=[f"{parent}/episode/2"],
                        outcome="supplemental",
                        support=["target://bangumi/41-movie-pack-first-arc"],
                        reason="leftover second part after the first target was used",
                    ),
                ]
            )
        ),
    )

    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"]["excluded_slice_with_mapped_sibling"] == 1
    repair = result.feedback["package"]["excluded_slice_mapped_sibling_repairs"][0]
    assert repair["local"] == f"{parent}/episode/2"
    assert repair["local_target_title_pairing_options"][0]["target"] == "target://bangumi/42-movie-pack-second-arc/episode/1"

    agenda = _repair_agenda_from_submit_feedback(result.feedback, repeated=False)
    assert agenda["excluded_slice_mapped_sibling_repairs"]
    assert "excluded_slice_mapped_sibling_repairs" in agenda["required_next_action"]

    broad_extra_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="part-one",
                        local=[f"{parent}/episode/1"],
                        outcome="mapped_explicit_item",
                        target="target://bangumi/41-movie-pack-first-arc/episode/1",
                    ),
                    ResolutionWorkUnit(
                        unit_label="part-two-leftover",
                        local=[f"{parent}/episode/2"],
                        outcome="supplemental",
                        support=["target://bangumi/41-movie-pack-first-arc"],
                        reason="Second recap film file is extra overflow for this recap set.",
                    ),
                ]
            )
        ),
    )

    assert broad_extra_result.accepted is False
    assert broad_extra_result.feedback["package"]["issue_counts"]["excluded_slice_with_mapped_sibling"] == 1


def test_fail_closed_parent_with_visible_slice_pairings_requires_split_granularity():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-FAIL-CLOSED-SLICE-PAIRING"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Movie Pack/Movie Pack [01(First Arc)].mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Movie Pack/Movie Pack [02(Second Arc)].mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=51,
                title="Movie Pack First Arc",
                name="Movie Pack First Arc",
                name_cn="Movie Pack First Arc",
                eps=1,
                total_episodes=1,
                search_query_ref="Movie Pack First Arc",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=52,
                title="Movie Pack Second Arc",
                name="Movie Pack Second Arc",
                name_cn="Movie Pack Second Arc",
                eps=1,
                total_episodes=1,
                search_query_ref="Movie Pack Second Arc",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="First Arc"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Second Arc"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    parent = desk["local_locators"][0]["locator"]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="movie-pack-unresolved",
                        local=[parent],
                        outcome="fail_closed",
                        reason="The two-part ownership is unresolved.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"]["fail_closed_with_visible_slice_pairing"] == 1
    repair = result.feedback["package"]["fail_closed_slice_pairing_repairs"][0]
    assert repair["local_slice_mapping_options"]
    assert repair["local_slice_mapping_options"][0]["local"].startswith(f"{parent}/episode/")
    assert repair["suggested_submit_shape"][0]["local"].startswith(f"{parent}/episode/")
    assert result.feedback["suggested_ledger_patch_rows"][0]["status"] == "mapped"
    assert result.feedback["ledger_choice_patch_rows"]


def test_fail_closed_title_tail_bridge_requires_inspection_before_terminal_blocker():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-FAIL-CLOSED-TITLE-BRIDGE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1"],
            allowed_file_refs=["LF1"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Pack/FRANCHISE Companion Stars.mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=61,
                title="Original Soundtrack",
                name="Original Soundtrack",
                name_cn="Original Soundtrack",
                eps=1,
                total_episodes=1,
                search_query_ref="Companion Stars",
            ),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    locator, issue = registry.resolve(local)
    assert issue is None
    searched = {hint.casefold() for hint in _query_hints_for_locator(locator, limit=8)}

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="companion-stars-unresolved",
                        local=[local],
                        outcome="fail_closed",
                        reason="No visible title bridge is safe enough.",
                    )
                ]
            )
        ),
        searched_query_variant_keys=searched,
    )

    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"]["fail_closed_title_tail_bridge_uninspected"] == 1
    repair = result.feedback["package"]["fail_closed_title_tail_bridge_repairs"][0]
    assert repair["visible_source_query_bridge_targets"][0]["available_action"].startswith("inspect(")


def test_query_variants_include_distinctive_suffix_without_work_alias():
    variants = _search_query_variants("Franchise Name Side Story")

    assert "Name Side Story" in variants
    assert "Side Story" in variants


def test_search_keeps_late_rank_title_tail_candidate_visible():
    class FakeBangumiClient:
        def search_subjects(self, keyword: str):
            return [
                BangumiSubject(id=100 + index, name=f"Broad Franchise {index}", name_cn=f"Broad Franchise {index}", eps=13)
                for index in range(1, 6)
            ] + [
                BangumiSubject(id=206, name="Franchise Side Story Part 1", name_cn="Franchise Side Story Part 1", eps=1),
                BangumiSubject(id=207, name="Franchise Side Story Part 2", name_cn="Franchise Side Story Part 2", eps=1),
            ]

    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SEARCH-DEPTH"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(main_file_refs=["LF1"], allowed_file_refs=["LF1"]),
        local_files=[LocalFileCard(ref="LF1", path="Franchise/Franchise Side Story.mkv", is_main=True)],
    )
    _desk, registry = build_human_case_desk(workspace)

    updated, output = _search_tool(
        workspace,
        registry,
        FakeBangumiClient(),
        SearchToolArgs(queries=["Franchise Side Story"], reason="search title-tail"),
    )

    targets = [
        result["target"]
        for query in output["queries"]
        for result in query["results"]
    ]
    assert any("206-franchise-side-story-part-1" in target for target in targets)
    assert any(int(subject.subject_id) == 207 for subject in updated.bangumi_subjects)


def test_singleton_main_exclusion_requires_title_tail_search_or_blocker():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SINGLETON-EXCLUSION"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1"],
            allowed_file_refs=["LF1"],
            visible_target_refs=["BE1"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Franchise Name/Franchise Name Side Story.mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=20,
                title="Franchise Name",
                name="Franchise Name",
                name_cn="Franchise Name",
                eps=12,
                total_episodes=12,
                search_query_ref="Franchise Name",
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="side-story-as-extra",
                        local=[local_locator],
                        outcome="non_bangumi",
                        support=["target://bangumi/20-franchise-name"],
                        reason="No visible Bangumi owner for this side story.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"]["excluded_title_tail_search_not_exhausted"] == 1
    repair = result.feedback["package"]["excluded_title_tail_search_repairs"][0]
    assert "Side Story" in repair["search_queries_to_try"]
    agenda = _repair_agenda_from_submit_feedback(result.feedback, repeated=False)
    assert "Side Story" in agenda["search_queries_to_try"]


def test_title_pairing_options_do_not_let_source_query_noise_outrank_visible_title_bridge():
    first_title = "\u5287\u5834\u7248\u7dcf\u96c6\u7de8 FRANCHISE First King"
    second_title = "\u5287\u5834\u7248\u7dcf\u96c6\u7de8 FRANCHISE Black Hero"
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-PAIRING-SOURCE-QUERY-NOISE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2", "BE3", "BE4", "BE5"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(First King)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Black Hero)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=101,
                title="\u5287\u5834\u7248 Other Show",
                name="\u5287\u5834\u7248 Other Show",
                name_cn="\u5287\u5834\u7248 Other Show",
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE Black Hero",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=102,
                title="FRANCHISE OAD",
                name="FRANCHISE OAD",
                name_cn="FRANCHISE OAD",
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE Black Hero",
            ),
            BangumiSubjectCard(
                ref="BS3",
                subject_id=103,
                title=first_title,
                name=first_title,
                name_cn=first_title,
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE First King | FRANCHISE Black Hero",
            ),
            BangumiSubjectCard(
                ref="BS4",
                subject_id=104,
                title=second_title,
                name=second_title,
                name_cn=second_title,
                eps=1,
                total_episodes=1,
                search_query_ref="broad one | broad two | broad three | broad four | FRANCHISE Black Hero",
            ),
            BangumiSubjectCard(
                ref="BS5",
                subject_id=105,
                title="\u5287\u5834\u7248 FRANCHISE Holy Kingdom",
                name="\u5287\u5834\u7248 FRANCHISE Holy Kingdom",
                name_cn="\u5287\u5834\u7248 FRANCHISE Holy Kingdom",
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE Black Hero",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref=f"BS{index}", sort=1, ep=1, title="Episode 1")
            for index in range(1, 6)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    first_subject_locator = registry.subject_locator_by_id[103]

    options = _local_target_title_pairing_options(
        registry,
        [local_locator],
        demoted_targets={first_subject_locator},
    )
    second_slice_options = [
        option
        for option in options
        if str(option.get("local_slice") or "").endswith("/episode/2")
    ]

    assert second_slice_options
    assert "104-" in second_slice_options[0]["target"]
    assert all("101-" not in str(option["target"]) for option in second_slice_options)
    assert second_slice_options[0]["shared_title_tail_tokens"]
    assert set(second_slice_options[0]["shared_source_query_tail_tokens"]) >= {"black", "hero"}
    assert set(second_slice_options[0]["shared_media_form_tokens"]) >= {"movie", "recap"}
    assert any(option["already_mapped_sibling_target"] for option in second_slice_options)


def test_title_pairing_options_keep_form_family_bridge_without_query_tail_match():
    target_title = "\u5287\u5834\u7248\u7dcf\u96c6\u7de8 FRANCHISE Companion"
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-PAIRING-FORM-FAMILY"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(First King)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Black Hero)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=201,
                title=target_title,
                name=target_title,
                name_cn=target_title,
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE",
            ),
        ],
        bangumi_items=[BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1")],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)

    options = _local_target_title_pairing_options(registry, [desk["local_locators"][0]["locator"]])

    assert any("201-" in str(option["target"]) for option in options)
    form_bridge = next(option for option in options if "201-" in str(option["target"]))
    assert form_bridge["match_strength"] == "title_plus_media_form"
    assert form_bridge["shared_source_query_tail_tokens"] == []
    assert set(form_bridge["shared_media_form_tokens"]) >= {"movie", "recap"}

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="first-slice-unresolved",
                        local=[f'{desk["local_locators"][0]["locator"]}/episode/1'],
                        outcome="fail_closed",
                        reason="first slice unresolved in this unit test",
                    ),
                    ResolutionWorkUnit(
                        unit_label="second-slice-excluded",
                        local=[f'{desk["local_locators"][0]["locator"]}/episode/2'],
                        outcome="non_bangumi",
                        reason="excluding without addressing the visible form-family target",
                    ),
                ]
            )
        ),
    )

    package = result.feedback.get("package") if isinstance(result.feedback, dict) else None
    issue_counts = package.get("issue_counts") if isinstance(package, dict) else {}
    assert "excluded_local_has_visible_title_pairing_target" not in issue_counts


def test_exact_manual_review_slice_must_carry_visible_weak_candidate():
    target_title = "\u5287\u5834\u7248\u7dcf\u96c6\u7de8 FRANCHISE Companion"
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-MANUAL-WEAK-SLICE-CANDIDATE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(First King)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Black Hero)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=211,
                title=target_title,
                name=target_title,
                name_cn=target_title,
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE",
            ),
        ],
        bangumi_items=[BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1")],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    target_subject = registry.subject_locator_by_id[211]
    candidate = f"{target_subject}/episode/1"

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="first-slice-addressed",
                        local=[f"{local_locator}/episode/1"],
                        outcome="manual_review",
                        manual_review_candidate_targets=[candidate],
                        reason="The visible companion candidate remains ambiguous for the first slice.",
                    ),
                    ResolutionWorkUnit(
                        unit_label="second-slice-unaddressed",
                        local=[f"{local_locator}/episode/2"],
                        outcome="manual_review",
                        reason="Manual review without carrying the visible weak candidate.",
                    ),
                ]
            )
        ),
    )

    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"][
        "manual_review_visible_slice_pairing_should_split"
    ] == 1
    repair = result.feedback["package"]["manual_review_visible_slice_pairing_repairs"][0]
    assert repair["candidate_targets"][0]["target"] == candidate
    assert repair["suggested_submit_shape"] == []


def test_exact_manual_review_slice_with_candidate_ignores_weak_isolated_tail_noise():
    target_title = "\u5287\u5834\u7248\u7dcf\u96c6\u7de8 FRANCHISE Companion"
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-MANUAL-WEAK-TAIL-NOISE-ADDRESSED"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(First King)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Black Hero)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=212,
                title=target_title,
                name=target_title,
                name_cn=target_title,
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=213,
                title="Unrelated Black",
                name="Unrelated Black",
                name_cn="Unrelated Black",
                eps=1,
                total_episodes=1,
                search_query_ref="Black Hero",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Episode 1"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    candidate = f"{registry.subject_locator_by_id[212]}/episode/1"

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="first-slice-review",
                        local=[f"{local_locator}/episode/1"],
                        outcome="manual_review",
                        manual_review_candidate_targets=[candidate],
                        reason="The visible same-franchise candidate remains ambiguous for the first slice.",
                    ),
                    ResolutionWorkUnit(
                        unit_label="second-slice-review",
                        local=[f"{local_locator}/episode/2"],
                        outcome="manual_review",
                        manual_review_candidate_targets=[candidate],
                        reason="The visible same-franchise candidate remains ambiguous for the second slice.",
                    ),
                ]
            )
        ),
    )

    assert result.accepted is True
    assert result.feedback["manual_review_file_count"] == 2


def test_exact_manual_review_slice_with_wrong_candidate_still_repairs_pairing():
    target_title = "\u5287\u5834\u7248\u7dcf\u96c6\u7de8 FRANCHISE Companion"
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-MANUAL-WRONG-SLICE-CANDIDATE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(First King)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Black Hero)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=214,
                title=target_title,
                name=target_title,
                name_cn=target_title,
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=215,
                title="Unrelated Black",
                name="Unrelated Black",
                name_cn="Unrelated Black",
                eps=1,
                total_episodes=1,
                search_query_ref="Black Hero",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Episode 1"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    correct_candidate = f"{registry.subject_locator_by_id[214]}/episode/1"
    wrong_candidate = f"{registry.subject_locator_by_id[215]}/episode/1"

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="first-slice-wrong-review-candidate",
                        local=[f"{local_locator}/episode/1"],
                        outcome="manual_review",
                        manual_review_candidate_targets=[wrong_candidate],
                        reason="Manual review carries an unrelated visible target.",
                    ),
                    ResolutionWorkUnit(
                        unit_label="second-slice-review",
                        local=[f"{local_locator}/episode/2"],
                        outcome="manual_review",
                        manual_review_candidate_targets=[wrong_candidate],
                        reason="The visible same-franchise candidate remains ambiguous for the second slice.",
                    ),
                ]
            )
        ),
    )

    assert result.accepted is False
    repair = result.feedback["package"]["manual_review_visible_slice_pairing_repairs"][0]
    assert repair["candidate_targets"][0]["target"] == correct_candidate


def test_parent_manual_review_must_split_hard_slice_title_pairings_without_contradiction():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-MANUAL-HARD-SLICE-PAIRING-DEBT"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(First King)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Black Hero)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=214,
                title="Gekijouban Soushuuhen FRANCHISE First King",
                name="Gekijouban Soushuuhen FRANCHISE First King",
                name_cn="Gekijouban Soushuuhen FRANCHISE First King",
                eps=1,
                total_episodes=1,
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=215,
                title="Gekijouban Soushuuhen FRANCHISE Black Hero",
                name="Gekijouban Soushuuhen FRANCHISE Black Hero",
                name_cn="Gekijouban Soushuuhen FRANCHISE Black Hero",
                eps=1,
                total_episodes=1,
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Episode 1"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    first_target = f"{registry.subject_locator_by_id[214]}/episode/1"
    second_target = f"{registry.subject_locator_by_id[215]}/episode/1"

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="parent-manual-review-with-hard-candidates",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[first_target, second_target],
                        reason=(
                            "Both slice candidates remain generally ambiguous for human review because the broad "
                            "parent would create a count mismatch."
                        ),
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    repair = result.feedback["package"]["manual_review_visible_slice_pairing_repairs"][0]
    assert [
        (row["local"], row["target"], row["outcome"])
        for row in repair["suggested_submit_shape"]
    ] == [
        (f"{local_locator}/episode/1", first_target, "mapped_explicit_item"),
        (f"{local_locator}/episode/2", second_target, "mapped_explicit_item"),
    ]


def test_parent_manual_review_candidate_does_not_hide_required_slice_split():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-PARENT-REVIEW-PARTIAL-CANDIDATE-SPLIT"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(Alpha Movie)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Beta Movie)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=217,
                title="Gekijouban Soushuuhen FRANCHISE Alpha Movie",
                name="Gekijouban Soushuuhen FRANCHISE Alpha Movie",
                name_cn="Gekijouban Soushuuhen FRANCHISE Alpha Movie",
                eps=1,
                total_episodes=1,
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=218,
                title="Gekijouban Soushuuhen FRANCHISE Beta Movie",
                name="Gekijouban Soushuuhen FRANCHISE Beta Movie",
                name_cn="Gekijouban Soushuuhen FRANCHISE Beta Movie",
                eps=1,
                total_episodes=1,
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Movie"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Movie"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    first_target = f"{registry.subject_locator_by_id[217]}/episode/1"
    second_target = f"{registry.subject_locator_by_id[218]}/episode/1"

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="parent-review-partial-candidate",
                        local=[local_locator],
                        outcome="manual_review",
                        manual_review_candidate_targets=[first_target],
                        reason="The first visible candidate is carried, but the parent is still too broad.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    repair = result.feedback["package"]["manual_review_visible_slice_pairing_repairs"][0]
    assert [
        (row["local"], row["target"], row["outcome"])
        for row in repair["suggested_submit_shape"]
    ] == [
        (f"{local_locator}/episode/1", first_target, "mapped_explicit_item"),
        (f"{local_locator}/episode/2", second_target, "mapped_explicit_item"),
    ]


def test_mapped_exact_slices_accept_source_query_tail_and_media_form_pairing():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-MAPPED-SOURCE-QUERY-SLICE-PAIRING"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(First King)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Black Hero)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=216,
                title="Gekijouban Soushuuhen FRANCHISE",
                name="Gekijouban Soushuuhen FRANCHISE",
                name_cn="Gekijouban Soushuuhen FRANCHISE",
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE First King",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=217,
                title="Gekijouban Soushuuhen FRANCHISE",
                name="Gekijouban Soushuuhen FRANCHISE",
                name_cn="Gekijouban Soushuuhen FRANCHISE",
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE Black Hero",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Episode 1"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    first_target = f"{registry.subject_locator_by_id[216]}/episode/1"
    second_target = f"{registry.subject_locator_by_id[217]}/episode/1"

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="first-query-tail-mapped",
                        local=[f"{local_locator}/episode/1"],
                        outcome="mapped_explicit_item",
                        target=first_target,
                        reason="Local slice tail and source-query tail identify the first recap feature.",
                    ),
                    ResolutionWorkUnit(
                        unit_label="second-query-tail-mapped",
                        local=[f"{local_locator}/episode/2"],
                        outcome="mapped_explicit_item",
                        target=second_target,
                        reason="Local slice tail and source-query tail identify the second recap feature.",
                    ),
                ]
            )
        ),
    )

    assert result.accepted is True
    issue_counts = result.feedback.get("package", {}).get("issue_counts", {})
    assert "mapped_singleton_broad_title_bridge_missing" not in issue_counts


def test_single_tail_token_pairing_is_evidence_not_hard_submit_blocker():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-PAIRING-NOISE-SINGLE-TOKEN"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(First King)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Black Hero)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=202,
                title="Unrelated Black",
                name="Unrelated Black",
                name_cn="Unrelated Black",
                eps=1,
                total_episodes=1,
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=203,
                title="FRANCHISE Companion",
                name="FRANCHISE Companion",
                name_cn="FRANCHISE Companion",
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE Black Hero",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Episode 1"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)

    options = _local_target_title_pairing_options(registry, [desk["local_locators"][0]["locator"]])
    assert any(option["shared_title_tail_tokens"] == ["black"] for option in options)
    assert "203-" in str(options[0]["target"])
    assert options[0]["match_strength"] == "title_plus_source_query"

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="first-slice-unresolved",
                        local=[f'{desk["local_locators"][0]["locator"]}/episode/1'],
                        outcome="fail_closed",
                        reason="first slice unresolved in this unit test",
                    ),
                    ResolutionWorkUnit(
                        unit_label="second-slice-excluded",
                        local=[f'{desk["local_locators"][0]["locator"]}/episode/2'],
                        outcome="non_bangumi",
                        reason="single-token unrelated title evidence is not enough to choose a Bangumi owner",
                    ),
                ]
            )
        ),
    )

    package = result.feedback.get("package") if isinstance(result.feedback, dict) else None
    issue_counts = package.get("issue_counts") if isinstance(package, dict) else {}
    assert "excluded_local_has_visible_title_pairing_target" not in issue_counts


def test_source_query_tail_plus_media_form_must_be_addressed_for_manual_review_slice():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-MANUAL-SOURCE-QUERY-FORM-CANDIDATE"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(First King)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Black Hero)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=204,
                title="Gekijouban Soushuuhen FRANCHISE Companion",
                name="Gekijouban Soushuuhen FRANCHISE Companion",
                name_cn="Gekijouban Soushuuhen FRANCHISE Companion",
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE Black Hero",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    target_subject = registry.subject_locator_by_id[204]

    second_slice_options = _local_target_title_pairing_options_for_slice(
        registry,
        f"{local_locator}/episode/2",
    )
    assert second_slice_options
    option = second_slice_options[0]
    assert option["target"] == f"{target_subject}/episode/1"
    assert option["shared_title_tail_tokens"] == []
    assert set(option["shared_source_query_tail_tokens"]) >= {"black", "hero"}
    assert set(option["shared_media_form_tokens"]) >= {"movie", "recap"}

    parent_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="parent-review",
                        local=[local_locator],
                        outcome="manual_review",
                        reason="Broad parent manual review before addressing the visible second-slice candidate.",
                    )
                ]
            )
        ),
    )

    assert parent_result.accepted is False
    parent_repair = parent_result.feedback["package"]["manual_review_visible_slice_pairing_repairs"][0]
    assert parent_repair["suggested_submit_shape"] == []
    assert parent_repair["candidate_targets"][0]["target"] == f"{target_subject}/episode/1"
    assert parent_repair["candidate_targets"][0]["local_slice"] == f"{local_locator}/episode/2"
    assert parent_repair["unpaired_local_slices"] == [f"{local_locator}/episode/1"]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="first-slice-unresolved",
                        local=[f"{local_locator}/episode/1"],
                        outcome="fail_closed",
                        reason="first slice unresolved in this unit test",
                    ),
                    ResolutionWorkUnit(
                        unit_label="second-slice-review",
                        local=[f"{local_locator}/episode/2"],
                        outcome="manual_review",
                        reason="Manual review before addressing the visible source-query/media-form candidate.",
                    ),
                ]
            )
        ),
    )

    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"][
        "manual_review_visible_slice_pairing_should_split"
    ] == 1
    repair = result.feedback["package"]["manual_review_visible_slice_pairing_repairs"][0]
    assert repair["suggested_submit_shape"][0]["target"] == f"{target_subject}/episode/1"


def test_parent_manual_review_exposes_ordered_media_form_slice_mapping_options():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-ORDERED-MEDIA-FORM-SLICES"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2", "BE3"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(Alpha Roman)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Beta Roman)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(ref="BS1", subject_id=401, title="\u5287\u5834\u7248\u7dcf\u96c6\u7de8 FRANCHISE One", eps=1),
            BangumiSubjectCard(ref="BS2", subject_id=402, title="\u5287\u5834\u7248\u7dcf\u96c6\u7de8 FRANCHISE Two", eps=1),
            BangumiSubjectCard(ref="BS3", subject_id=403, title="\u5287\u5834\u7248 FRANCHISE Future", eps=1),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Movie 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Movie 2"),
            BangumiItemCard(ref="BE3", subject_ref="BS3", sort=1, ep=1, title="Movie 3"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="parent-review",
                        local=[local_locator],
                        outcome="manual_review",
                        reason="Broad parent review has not addressed same-count media-form slice candidates.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is False
    repair = result.feedback["package"]["manual_review_visible_slice_pairing_repairs"][0]
    assert [
        (row["local"], row["target"], row["outcome"])
        for row in repair["suggested_submit_shape"]
    ] == [
        (f"{local_locator}/episode/1", f"{registry.subject_locator_by_id[401]}/episode/1", "mapped_explicit_item"),
        (f"{local_locator}/episode/2", f"{registry.subject_locator_by_id[402]}/episode/1", "mapped_explicit_item"),
    ]
    assert all("403" not in row["target"] for row in repair["suggested_submit_shape"])


def test_mapped_slice_cannot_consume_target_still_cited_by_manual_sibling():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-MAPPED-SLICE-MANUAL-SIBLING-CONFLICT"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Franchise [01(Alpha Movie)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Franchise [02(Beta Movie)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=221,
                title="Franchise Alpha Movie",
                name="Franchise Alpha Movie",
                name_cn="Franchise Alpha Movie",
                eps=1,
                total_episodes=1,
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=222,
                title="Franchise Beta Movie",
                name="Franchise Beta Movie",
                name_cn="Franchise Beta Movie",
                eps=1,
                total_episodes=1,
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Movie"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Movie"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local_locator = desk["local_locators"][0]["locator"]
    beta_target = f"{registry.subject_locator_by_id[222]}/episode/1"

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="alpha-slice-wrongly-uses-beta-target",
                        local=[f"{local_locator}/episode/1"],
                        outcome="mapped_explicit_item",
                        target=beta_target,
                        reason="The first slice is being mapped to the visible movie target.",
                    ),
                    ResolutionWorkUnit(
                        unit_label="beta-slice-manual-candidate",
                        local=[f"{local_locator}/episode/2"],
                        outcome="manual_review",
                        manual_review_candidate_targets=[beta_target],
                        reason="The visible Beta Movie candidate remains ambiguous for this sibling slice.",
                    ),
                ]
            )
        ),
    )

    assert result.accepted is False
    assert result.feedback["package"]["issue_counts"][
        "mapped_slice_target_contested_by_manual_review_sibling"
    ] == 1
    repair = result.feedback["package"]["mapped_slice_manual_sibling_repairs"][0]
    assert repair["mapped_local"] == f"{local_locator}/episode/1"
    assert repair["manual_review_local"] == f"{local_locator}/episode/2"
    assert repair["target"] == beta_target


def test_title_pairing_options_do_not_invent_translated_tail_equivalents():
    first_title = "\u5287\u5834\u7248\u7dcf\u96c6\u7de8 FRANCHISE \u9752\u3044\u9a0e\u58eb"
    second_title = "\u5287\u5834\u7248\u7dcf\u96c6\u7de8 FRANCHISE \u8d64\u3044\u82b1"
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-PAIRING-TRANSLATED-TAIL"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
            visible_target_refs=["BE1", "BE2"],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [01(Aoi Kishi)].mkv",
                is_main=True,
            ),
            LocalFileCard(
                ref="LF2",
                path="Pack/[Group] Gekijouban Soushuuhen FRANCHISE [02(Akai Hana)].mkv",
                is_main=True,
            ),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=301,
                title=first_title,
                name=first_title,
                name_cn=first_title,
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE",
            ),
            BangumiSubjectCard(
                ref="BS2",
                subject_id=302,
                title=second_title,
                name=second_title,
                name_cn=second_title,
                eps=1,
                total_episodes=1,
                search_query_ref="FRANCHISE",
            ),
        ],
        bangumi_items=[
            BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1"),
            BangumiItemCard(ref="BE2", subject_ref="BS2", sort=1, ep=1, title="Episode 1"),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)

    options = _local_target_title_pairing_options(registry, [desk["local_locators"][0]["locator"]])
    episode1_options = [option for option in options if str(option["local_slice"]).endswith("/episode/1")]
    episode2_options = [option for option in options if str(option["local_slice"]).endswith("/episode/2")]

    assert episode1_options
    assert episode2_options
    assert all(not option["shared_title_tail_tokens"] for option in episode1_options)
    assert all(not option["shared_title_tail_tokens"] for option in episode2_options)
    assert not any(
        {"aoi", "kishi", "akai", "hana"}.intersection(
            set(option["shared_title_tokens"]) | set(option["shared_source_query_tokens"])
        )
        for option in [*episode1_options, *episode2_options]
    )


def test_episode_slice_tail_repair_does_not_inherit_sibling_title_tokens():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SLICE-TAIL-NO-SIBLING"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(main_file_refs=["LF1", "LF2"], allowed_file_refs=["LF1", "LF2"]),
        local_files=[
            LocalFileCard(ref="LF1", path="Pack/[Group] FRANCHISE [01(Aoi Kishi)].mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Pack/[Group] FRANCHISE [02(Akai Hana)].mkv", is_main=True),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    parent = next(row["locator"] for row in desk["local_locators"] if row["locator"].endswith("/main-episodes"))
    slice_two = f"{parent}/episode/2"
    slice_two_locator, issue = registry.resolve(slice_two)
    assert issue is None
    searched = {hint.casefold() for hint in _query_hints_for_locator(slice_two_locator, limit=8)}

    repairs = _excluded_title_tail_unresolved_after_search_repairs(
        registry,
        [{"unit": "part 2", "outcome": "supplemental", "local": [slice_two]}],
        searched_query_variant_keys=searched,
    )

    assert repairs
    tokens = set(repairs[0]["unbridged_title_tail_tokens"])
    assert {"akai", "hana"}.issubset(tokens)
    assert "aoi" not in tokens
    assert "kishi" not in tokens
    assert any("Akai Hana" in hint for hint in repairs[0]["searched_query_hints"])
    assert not any("Aoi Kishi" in hint for hint in repairs[0]["searched_query_hints"])


def test_title_tail_unresolved_repair_projects_terminal_row_choices():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-TITLE-TAIL-ROW-CHOICES"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(main_file_refs=["LF1"], allowed_file_refs=["LF1"]),
        local_files=[
            LocalFileCard(ref="LF1", path="Pack/[Group] FRANCHISE Companion Stars.mkv", is_main=True),
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=501,
                title="Companion Stars Side Story",
                name="Companion Stars Side Story",
                name_cn="Companion Stars Side Story",
                eps=1,
                total_episodes=1,
                search_query_ref="Companion Stars",
            )
        ],
        bangumi_items=[BangumiItemCard(ref="BE1", subject_ref="BS1", sort=1, ep=1, title="Episode 1")],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)
    local = desk["local_locators"][0]["locator"]
    locator, issue = registry.resolve(local)
    assert issue is None
    searched = {
        variant.casefold()
        for query in _query_hints_for_locator(locator, limit=8)
        for variant in _search_query_variants(query)
    }

    repairs = _excluded_title_tail_unresolved_after_search_repairs(
        registry,
        [{"unit": "companion-stars", "outcome": "supplemental", "local": [local]}],
        searched_query_variant_keys=searched,
    )

    assert repairs
    repair = repairs[0]
    assert repair["manual_review_candidate_submit_shape"][0]["local"] == local
    assert repair["manual_review_candidate_submit_shape"][0]["manual_review_candidate_targets"] == [
        "target://bangumi/501-companion-stars-side-story/episode/1"
    ]
    assert repair["fail_closed_submit_shape"][0]["local"] == local
    agenda = _repair_agenda_from_submit_feedback(
        {
            "package": {
                "issue_counts": {"excluded_title_tail_unresolved_after_search": 1},
                "excluded_title_tail_unresolved_repairs": repairs,
            },
            "units": [],
        },
        repeated=False,
    )
    frontier = _repair_frontier_rows_from_agenda(agenda)
    assert frontier[0]["manual_review_candidate_submit_shape"][0]["local"] == local
    assert frontier[0]["fail_closed_submit_shape"][0]["local"] == local
    session = HumanCaseSession(
        case_id=workspace.header.case_id,
        resolution_ledger=ResolutionLedger(rows=[ResolutionLedgerRow(row_id="LR1", local=[local], status="open")]),
    )
    rows = _ledger_choice_patch_rows_from_repair(session, agenda)
    assert [row["status"] for row in rows] == ["manual_review", "fail_closed"]


def test_fail_closed_singleton_gets_visible_title_tail_pairing_repair():
    target_title = "Play Play Companion Stars"
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SINGLETON-FAIL-CLOSED-PAIRING"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1"],
            allowed_file_refs=["LF1"],
            visible_target_refs=[f"BE{index}" for index in range(1, 4)],
        ),
        local_files=[
            LocalFileCard(
                ref="LF1",
                path="Pack/[Group] FRANCHISE Companion Stars.mkv",
                is_main=True,
            )
        ],
        bangumi_subjects=[
            BangumiSubjectCard(
                ref="BS1",
                subject_id=401,
                title=target_title,
                name=target_title,
                name_cn=target_title,
                eps=3,
                total_episodes=3,
                search_query_ref="Companion Stars",
            )
        ],
        bangumi_items=[
            BangumiItemCard(ref=f"BE{index}", subject_ref="BS1", sort=index, ep=index, title=f"Episode {index}")
            for index in range(1, 4)
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    _register_existing_targets(workspace, registry)

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="companion-stars-unresolved",
                        local=[desk["local_locators"][0]["locator"]],
                        outcome="fail_closed",
                        reason="No matching target surfaced.",
                    )
                ]
            )
        ),
    )

    assert result.feedback["package"]["issue_counts"]["excluded_local_has_visible_title_pairing_target"] == 1
    repair = result.feedback["package"]["excluded_visible_title_pairing_repairs"][0]
    option = repair["local_target_title_pairing_options"][0]
    assert "401-" in option["target_subject"]
    assert option["candidate_target_episode_locators"]

    composite_result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="companion-stars-as-composite",
                        local=[desk["local_locators"][0]["locator"]],
                        target=f"{registry.subject_locator_by_id[401]}/episodes/1-3",
                        outcome="mapped_composite_feature",
                        reason="one singleton companion title is not automatically a complete composite feature",
                    )
                ]
            )
        ),
    )

    assert composite_result.feedback["package"]["issue_counts"]["composite_feature_shape_invalid"] == 1
