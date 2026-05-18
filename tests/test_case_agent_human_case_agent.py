from __future__ import annotations

from src.rename.case_agent.human_case_agent import (
    AgentLocator,
    HUMAN_CASE_AGENT_INSTRUCTIONS,
    LocatorRegistry,
    PackageResolution,
    ResolutionWorkUnit,
    SearchToolArgs,
    SubmitToolArgs,
    _register_existing_targets,
    _local_target_title_pairing_options,
    _excluded_title_tail_unresolved_after_search_repairs,
    _query_hints_for_locator,
    _repair_agenda_from_submit_feedback,
    _search_query_variants,
    _search_tool,
    _subject_card_from_api,
    _submit_tool,
    _visible_source_query_bridge_targets,
    build_human_case_desk,
    human_case_tool_definitions,
)
from src.bangumi.models import BangumiSubject
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


def test_human_agent_prompt_keeps_tools_simple_and_agent_semantic():
    assert "HumanCaseAgent" in HUMAN_CASE_AGENT_INSTRUCTIONS
    assert "submit" in {tool["function"]["name"] for tool in human_case_tool_definitions()}
    assert "Use fail_closed only when the local locator remains semantically unresolved" in HUMAN_CASE_AGENT_INSTRUCTIONS
    assert "The fixed layer checks only counts" in HUMAN_CASE_AGENT_INSTRUCTIONS


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
    assert "target://bangumi/1-franchise" in [row["target"] for row in rows]


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
