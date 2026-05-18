from __future__ import annotations

from src.rename.case_agent.human_case_agent import (
    AttentionFocus,
    CaseCognitiveWorkspace,
    HumanCaseSession,
    InvestigationAgendaItem,
    NoteToolArgs,
    PackageResolution,
    RejectedCandidate,
    ResolutionReadiness,
    ResolutionWorkUnit,
    SubmitToolArgs,
    WorkUnitFocus,
    _apply_turn_health,
    _active_repair_agenda_for_prompt,
    _call_human_agent,
    _compact_cognitive_workspace,
    _initial_cognitive_workspace_from_desk,
    _layer_search_output_for_workspace,
    _near_cap_submit_finalization_guard_output,
    _note_tool,
    _repair_finalization_guard_for_prompt,
    _repair_agenda_from_submit_feedback,
    _record_tool_output,
    _submit_tool,
    _turn_tail,
    _workspace_counts,
    _workspace_with_submit_rejection,
    build_human_case_desk,
    HumanToolCall,
)
from src.rename.case_agent.models import (
    CaseBudget,
    CaseContract,
    CaseHeader,
    LocalFileCard,
)
from src.rename.case_agent.workspace import CaseEvidenceWorkspace


def _workspace() -> CaseEvidenceWorkspace:
    return CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-COG"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Show/Show - 01.mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Show/Show - 02.mkv", is_main=True),
        ],
    )


def test_note_updates_cognitive_workspace_and_rejects_hidden_locator():
    workspace = _workspace()
    desk, registry = build_human_case_desk(workspace)
    locator = desk["local_locators"][0]["locator"]
    session = HumanCaseSession(case_id="CASE-COG")

    update = CaseCognitiveWorkspace(
        primary_hypotheses=["Show"],
        attention_focus=AttentionFocus(
            summary="Check the main span",
            locators=[locator],
            next_action="search Show",
        ),
        active_work_units=[
            WorkUnitFocus(
                work_unit_id="WU1",
                label="main span",
                local=[locator],
                hypothesis="Show episodes 1-2",
            )
        ],
        investigation_agenda=[
            InvestigationAgendaItem(
                agenda_id="AG1",
                question="Find the target subject",
                status="open",
                locators=[locator],
            )
        ],
        resolution_readiness=ResolutionReadiness(
            status="not_ready",
            blocking_work_units=["WU1"],
            evidence_gaps=["target not inspected"],
        ),
    )

    result = _note_tool(
        registry,
        session,
        NoteToolArgs(
            claims=["main span hypothesis"],
            locators=[locator],
            reason="record desk",
            cognitive_workspace=update,
        ),
    )

    assert result["accepted"] is True
    compact = _compact_cognitive_workspace(session.cognitive_workspace)
    assert compact["primary_hypotheses"] == ["Show"]
    assert compact["attention_focus"]["locators"] == [locator]

    rejected = _note_tool(
        registry,
        session,
        NoteToolArgs(
            reason="bad hidden ref",
            cognitive_workspace=CaseCognitiveWorkspace(
                attention_focus=AttentionFocus(locators=["LF1"]),
            ),
        ),
    )

    assert rejected["accepted"] is False
    assert rejected["issues"][0]["issue"] in {"locator_not_found", "unknown_locator"}


def test_human_agent_call_records_cache_probe_without_previous_response_id():
    workspace = _workspace()
    desk, _registry = build_human_case_desk(workspace)
    session = HumanCaseSession(
        case_id="CASE-CACHE",
        cognitive_workspace=_initial_cognitive_workspace_from_desk(desk),
        max_turns=4,
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def call_responses_tool_agent(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "id": f"resp_{len(self.calls)}",
                "tool_calls": [
                    {
                        "call_id": f"call_{len(self.calls)}",
                        "name": "search",
                        "arguments": '{"queries":["Show"],"reason":"find subject"}',
                    }
                ],
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 10,
                    "input_tokens_details": {"cached_tokens": 256},
                },
            }

    client = FakeClient()
    _tool_call, session, first_audit, error = _call_human_agent(client, desk, session, max_turns=4)
    assert error == ""
    _tool_call, session, second_audit, error = _call_human_agent(client, desk, session, max_turns=4)
    assert error == ""

    assert client.calls[0]["conversation_id"] == ""
    assert client.calls[1]["conversation_id"] == ""
    assert "previous_response_id" not in client.calls[0]
    assert "previous_response_id" not in client.calls[1]
    assert "previous_response_id_sent" not in first_audit
    assert first_audit["provider_cached_input_tokens"] == 256
    assert first_audit["provider_cached_input_ratio"] == 0.256
    for key in ("instructions_sha256", "tools_sha256", "case_desk_sha256", "tail_sha256"):
        assert len(str(first_audit[key])) == 64
    assert second_audit["tail_lcp_with_previous_bytes"] > 0
    assert second_audit["tail_lcp_with_previous_estimated_tokens"] == second_audit["tail_lcp_with_previous_bytes"] // 4


def test_search_output_layers_rejected_candidates_out_of_primary_results():
    cognitive = CaseCognitiveWorkspace(
        rejected_or_noisy_candidates=[
            RejectedCandidate(locator="target://bangumi/2-noise", reason="wrong title family")
        ]
    )
    output = {
        "accepted": True,
        "queries": [
            {
                "query": "Show",
                "results": [
                    {"target": "target://bangumi/1-show", "title": "Show", "eps": 2},
                    {"target": "target://bangumi/2-noise", "title": "Noise", "eps": 12},
                ],
            }
        ],
    }

    layered = _layer_search_output_for_workspace(output, cognitive)
    query = layered["queries"][0]

    assert [row["target"] for row in query["results"]] == ["target://bangumi/1-show"]
    rejected_tier = next(tier for tier in query["result_tiers"] if tier["layer"] == "rejected_or_noisy")
    assert rejected_tier["results"][0]["target"] == "target://bangumi/2-noise"
    assert layered["noise_candidate_count"] == 1


def test_submit_rejection_updates_readiness_and_repeated_rejection_stalls():
    workspace = _workspace()
    desk, registry = build_human_case_desk(workspace)
    session = HumanCaseSession(case_id="CASE-COG")
    session.cognitive_workspace = CaseCognitiveWorkspace(
        active_work_units=[
            WorkUnitFocus(
                work_unit_id="WU1",
                label="main span",
                local=[desk["local_locators"][0]["locator"]],
            )
        ]
    )
    before_cognitive = session.cognitive_workspace.model_copy(deep=True)

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(resolution=PackageResolution(work_units=[])),
    )
    assert result.accepted is False
    agenda = _repair_agenda_from_submit_feedback(result.feedback, repeated=False)
    session.cognitive_workspace = _workspace_with_submit_rejection(
        session.cognitive_workspace,
        agenda,
        repeated=False,
    )

    assert session.cognitive_workspace.resolution_readiness.status == "blocked"
    assert session.cognitive_workspace.resolution_readiness.mechanical_gaps

    session, output = _apply_turn_health(
        session,
        {"accepted": False},
        before_workspace_counts=_workspace_counts(workspace),
        after_workspace=workspace,
        before_cognitive=before_cognitive,
        repeated_submit_rejection=False,
    )

    assert output["turn_health"]["active_focus_changed"] is True
    assert output["turn_health"]["resolution_readiness_changed"] is True
    assert "stall_warning" not in output["turn_health"]

    before_cognitive = session.cognitive_workspace.model_copy(deep=True)
    session, output = _apply_turn_health(
        session,
        {"accepted": False},
        before_workspace_counts=_workspace_counts(workspace),
        after_workspace=workspace,
        before_cognitive=before_cognitive,
        repeated_submit_rejection=True,
    )

    assert output["turn_health"]["stall_warning"]["issue"] == "cognitive_workspace_stalled"
    assert session.stall_warning_count == 1


def test_special_target_absent_semantics_are_diagnostics_not_hard_rejects():
    workspace = CaseEvidenceWorkspace.from_cards(
        header=CaseHeader(case_id="CASE-SP-DIAG"),
        budget=CaseBudget(max_judge_rounds=4),
        contract=CaseContract(
            main_file_refs=["LF1", "LF2"],
            allowed_file_refs=["LF1", "LF2"],
        ),
        local_files=[
            LocalFileCard(ref="LF1", path="Show/SPs/Show [SP01].mkv", is_main=True),
            LocalFileCard(ref="LF2", path="Show/SPs/Show [SP02].mkv", is_main=True),
        ],
    )
    desk, registry = build_human_case_desk(workspace)
    local = next(row["locator"] for row in desk["local_locators"] if row["locator"].endswith("/special-marker"))

    result = _submit_tool(
        workspace,
        registry,
        SubmitToolArgs(
            resolution=PackageResolution(
                work_units=[
                    ResolutionWorkUnit(
                        unit_label="SP extras",
                        local=[local],
                        outcome="bangumi_target_absent",
                        reason="Agent judges these numbered SP files as package extras with no target owner.",
                    )
                ]
            )
        ),
    )

    assert result.accepted is True
    assert result.feedback["semantic_diagnostics"]
    assert result.feedback["semantic_diagnostics"][0]["issue_code"] == "numbered_special_exclusion_needs_target_evidence"


def test_repair_agenda_keeps_semantic_diagnostics_out_of_blocking_units():
    feedback = {
        "package": {"issue_counts": {"target_episode_surface_missing": 1}},
        "units": [
            {
                "unit": "SP extras",
                "local": ["local://show-sps/special-marker"],
                "issue": "numbered_special_exclusion_needs_target_evidence",
            },
            {
                "unit": "movie span",
                "local": ["local://movie/main-episodes"],
                "target": "target://bangumi/1-movie/episodes/1-2",
                "issues": [
                    {
                        "issue": "target_episode_surface_missing",
                        "target": "target://bangumi/1-movie/episodes/1-2",
                        "available_target_episode_numbers": [1],
                        "target_surface_visible": True,
                        "local_slice_mapping_options": [
                            {
                                "local": "local://movie/main-episodes/episode/2",
                                "target": "target://bangumi/2-movie-part-two/episode/1",
                                "outcome": "mapped_explicit_item",
                            }
                        ],
                    }
                ],
            },
        ],
    }

    agenda = _repair_agenda_from_submit_feedback(feedback, repeated=False)

    assert [unit["unit"] for unit in agenda["blocking_units"]] == ["movie span"]
    assert agenda["diagnostic_units"][0]["unit"] == "SP extras"
    assert agenda["visible_target_surface_missing_units"][0]["local_slice_mapping_options"][0]["local"] == (
        "local://movie/main-episodes/episode/2"
    )

    workspace = CaseCognitiveWorkspace(
        active_work_units=[
            WorkUnitFocus(work_unit_id="SP extras", label="SP extras", local=["local://show-sps/special-marker"]),
            WorkUnitFocus(work_unit_id="movie span", label="movie span", local=["local://movie/main-episodes"]),
        ]
    )
    updated = _workspace_with_submit_rejection(workspace, agenda, repeated=False)
    gap_codes = {gap.issue_code for gap in updated.resolution_readiness.mechanical_gaps}

    assert "target_episode_surface_missing" in gap_codes
    assert "numbered_special_exclusion_needs_target_evidence" not in gap_codes


def test_submit_rejection_creates_durable_repair_agenda_and_focus():
    feedback = {
        "package": {"issue_counts": {"count_mismatch": 1}},
        "units": [
            {
                "unit": "movie parent",
                "local": ["local://movie/main-episodes"],
                "target": "target://bangumi/1-movie/episode/1",
                "issue": "count_mismatch",
                "split_first_repair": {
                    "legal_local_split_locators": [
                        {"locator": "local://movie/main-episodes/episode/1"},
                        {"locator": "local://movie/main-episodes/episode/2"},
                    ]
                },
            }
        ],
        "visible_target_surface_missing_units": [
            {
                "unit": "movie parent",
                "local": ["local://movie/main-episodes"],
                "target": "target://bangumi/1-movie/episodes/1-2",
                "local_slice_mapping_options": [
                    {
                        "local": "local://movie/main-episodes/episode/2",
                        "target": "target://bangumi/2-movie-part-two/episode/1",
                    }
                ],
            }
        ],
    }
    workspace = CaseCognitiveWorkspace(
        active_work_units=[
            WorkUnitFocus(
                work_unit_id="WU1",
                label="movie parent",
                local=["local://movie/main-episodes"],
            )
        ]
    )

    agenda = _repair_agenda_from_submit_feedback(feedback, repeated=False)
    agenda["visible_target_surface_missing_units"] = feedback["visible_target_surface_missing_units"]
    updated = _workspace_with_submit_rejection(workspace, agenda, repeated=False)

    assert updated.attention_focus.locators == [
        "local://movie/main-episodes",
        "target://bangumi/1-movie/episode/1",
    ]
    assert updated.attention_focus.next_action
    repair_items = [item for item in updated.investigation_agenda if item.agenda_id.startswith("REPAIR-")]
    assert len(repair_items) == 1
    assert repair_items[0].blocking_issue == "count_mismatch"
    assert repair_items[0].next_action
    assert repair_items[0].closure_condition
    session = HumanCaseSession(case_id="CASE-REPAIR")
    session.cognitive_workspace = updated
    session.observations.append({"tool": "submit", "output": agenda})
    active_agenda = _active_repair_agenda_for_prompt(session)
    assert active_agenda[0]["visible_options"]["local_slice_mapping_options"][0]["local"] == (
        "local://movie/main-episodes/episode/2"
    )
    assert updated.active_work_units[0].blocking_issue == "count_mismatch"
    assert updated.active_work_units[0].required_next_action
    assert updated.active_work_units[0].closure_condition


def test_turn_tail_prioritizes_active_repair_agenda():
    session = HumanCaseSession(case_id="CASE-TAIL")
    session.cognitive_workspace = CaseCognitiveWorkspace(
        investigation_agenda=[
            InvestigationAgendaItem(
                agenda_id="REPAIR-1-count-mismatch",
                status="open",
                locators=["local://movie/main-episodes"],
                next_action="split the visible local slices",
                blocking_issue="count_mismatch",
                closure_condition="each slice is covered exactly once",
            )
        ]
    )

    tail = _turn_tail({"resolution_contract": {"must_account_locator_count": 1}}, session, max_turns=4)

    active = tail["case_memory"]["active_repair_agenda"]
    assert active[0]["blocking_issue"] == "count_mismatch"
    assert active[0]["required_next_action"] == "split the visible local slices"


def test_near_cap_repair_finalization_guard_requires_evidence_or_exact_fail_closed():
    repair = {
        "accepted": False,
        "status": "repair_required",
        "issue_counts": {"count_mismatch": 1},
        "blocking_units": [
            {
                "unit": "movie parent",
                "local": ["local://movie/main-episodes"],
                "target": "target://bangumi/1-movie/episode/1",
                "issue": "count_mismatch",
            }
        ],
    }
    session = HumanCaseSession(
        case_id="CASE-NEAR-CAP",
        turn_count=10,
        max_turns=12,
        draft_work_units=[
            {
                "unit_label": "tv span",
                "local": ["local://show/main-episodes"],
                "outcome": "mapped_regular_span",
                "target": "target://bangumi/2-show/episodes/1-12",
            }
        ],
    )
    session.cognitive_workspace = _workspace_with_submit_rejection(
        CaseCognitiveWorkspace(),
        repair,
        repeated=False,
    )
    session.observations.append({"tool": "submit", "output": repair})

    tail = _turn_tail({"resolution_contract": {"must_account_locator_count": 2}}, session, max_turns=12)
    guard = tail["case_memory"]["near_cap_repair_finalization_guard"]

    assert guard["issue"] == "near_cap_repair_finalization_guard"
    assert guard["finalization_target_locators"] == ["local://movie/main-episodes"]
    assert "fail_closed" in guard["required_next_action"]
    assert "fixed layer does not choose target" in guard["forbidden_fixed_layer_choices"]

    broad_submit = SubmitToolArgs(
        resolution=PackageResolution(
            work_units=[
                ResolutionWorkUnit(
                    unit_label="movie parent still broad",
                    local=["local://movie/main-episodes"],
                    outcome="supplemental",
                    reason="Agent semantic judgment, but not an exact final blocker.",
                )
            ]
        )
    )
    rejected = _near_cap_submit_finalization_guard_output(session, broad_submit, max_turns=12)
    assert rejected["issue"] == "near_cap_repair_finalization_requires_exact_work_unit_closure"

    exact_fail_closed = SubmitToolArgs(
        resolution=PackageResolution(
            work_units=[
                ResolutionWorkUnit(
                    unit_label="movie parent unresolved",
                    local=["local://movie/main-episodes"],
                    outcome="fail_closed",
                    reason="Visible evidence still leaves this exact movie parent unsafe.",
                )
            ]
        )
    )
    assert _near_cap_submit_finalization_guard_output(session, exact_fail_closed, max_turns=12) == {}


def test_near_cap_repair_finalization_guard_requires_all_open_locators_closed():
    repair = {
        "accepted": False,
        "status": "repair_required",
        "issue_counts": {"coverage_missing": 2},
        "required_missing_work_units": [
            {"local": ["local://movie-sp/main"]},
            {"local": ["local://ova-sp/main"]},
        ],
    }
    session = HumanCaseSession(
        case_id="CASE-NEAR-CAP-MULTI",
        turn_count=10,
        max_turns=12,
        draft_work_units=[{"unit_label": "saved", "local": ["local://show/main"], "outcome": "mapped_regular_span"}],
    )
    session.cognitive_workspace = _workspace_with_submit_rejection(
        CaseCognitiveWorkspace(),
        repair,
        repeated=False,
    )
    session.observations.append({"tool": "submit", "output": repair})

    one_closed = SubmitToolArgs(
        resolution=PackageResolution(
            work_units=[
                ResolutionWorkUnit(
                    unit_label="movie sp unresolved",
                    local=["local://movie-sp/main"],
                    outcome="fail_closed",
                    reason="visible evidence still does not identify this exact unit",
                )
            ]
        )
    )

    rejected = _near_cap_submit_finalization_guard_output(session, one_closed, max_turns=12)
    assert rejected["missing_exact_fail_closed_locators"] == ["local://ova-sp/main"]
    guard_text = " ".join(
        [
            str(rejected["required_next_action"]),
            str(rejected["near_cap_repair_finalization_guard"]["required_next_action"]),
            " ".join(rejected["near_cap_repair_finalization_guard"]["allowed_actions"]),
        ]
    )
    assert "every listed finalization_target_locator" in guard_text
    assert "one of finalization_target_locators" not in guard_text

    all_closed = SubmitToolArgs(
        resolution=PackageResolution(
            work_units=[
                ResolutionWorkUnit(
                    unit_label="movie sp unresolved",
                    local=["local://movie-sp/main"],
                    outcome="fail_closed",
                    reason="visible evidence still does not identify this exact unit",
                ),
                ResolutionWorkUnit(
                    unit_label="ova sp unresolved",
                    local=["local://ova-sp/main"],
                    outcome="fail_closed",
                    reason="visible evidence still does not identify this exact unit",
                ),
            ]
        )
    )
    assert _near_cap_submit_finalization_guard_output(session, all_closed, max_turns=12) == {}


def test_stall_repair_finalization_guard_uses_same_active_agenda_contract():
    repair = {
        "accepted": False,
        "status": "repair_required",
        "issue_counts": {"count_mismatch": 1},
        "blocking_units": [
            {
                "unit": "movie parent",
                "local": ["local://movie/main-episodes"],
                "issue": "count_mismatch",
            }
        ],
    }
    session = HumanCaseSession(
        case_id="CASE-STALL-GUARD",
        turn_count=4,
        max_turns=12,
        no_progress_turn_count=2,
    )
    session.cognitive_workspace = _workspace_with_submit_rejection(
        CaseCognitiveWorkspace(),
        repair,
        repeated=False,
    )
    session.observations.append({"tool": "submit", "output": repair})

    guard = _repair_finalization_guard_for_prompt(session, max_turns=12)

    assert guard["issue"] == "stall_repair_finalization_guard"
    assert guard["active_repair_agenda"][0]["blocking_issue"] == "count_mismatch"
    assert "local://movie/main-episodes" in guard["finalization_target_locators"]


def test_required_missing_list_locators_are_not_stringified():
    agenda = {
        "required_missing_work_units": [
            {"local": ["local://movie/main", "local://short/main"]}
        ]
    }

    updated = _workspace_with_submit_rejection(
        CaseCognitiveWorkspace(),
        agenda,
        repeated=False,
    )

    session = HumanCaseSession(case_id="CASE")
    session.cognitive_workspace = updated
    active = _active_repair_agenda_for_prompt(session)
    assert len(active) == 2
    locators = {
        locator
        for item in updated.investigation_agenda
        for locator in item.locators
        if item.agenda_id.startswith("REPAIR-")
    }
    assert "local://movie/main" in locators
    assert "['local://movie/main', 'local://short/main']" not in locators


def test_submit_repair_loop_warning_triggers_for_consecutive_submit():
    session = HumanCaseSession(
        case_id="CASE-LOOP",
        current_consecutive_tool_count=4,
    )
    output = {
        "accepted": False,
        "status": "repair_required",
        "issue_counts": {"count_mismatch": 1},
        "blocking_units": [{"unit": "movie parent", "issue": "count_mismatch"}],
    }

    updated = _record_tool_output(
        session,
        HumanToolCall(
            tool_name="submit",
            arguments=SubmitToolArgs(),
            raw_arguments={},
        ),
        output,
    )

    assert updated.single_tool_loop_suspected_count == 1
    assert updated.observations[-1]["output"]["loop_health_warning"]["issue"] == "same_tool_repeated"
