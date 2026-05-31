from __future__ import annotations

import json
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..case_agent.models import CaseVerifierResult, VerifierIssue
from ..decision_snapshot import write_decision_snapshot
from ..filename_builder import FilenameBuilder, MovieMetadata
from .models import (
    BgmAssignmentRef,
    BgmToTmdbInput,
    BridgeMappingDisposition,
    MOVIE_LEGAL_NODE_RE,
    TmdbCandidateCard,
    TmdbLegalGraph,
    TmdbLegalNode,
    TmdbMediaType,
    TV_LEGAL_NODE_RE,
    VerifiedBgmToTmdbPlan,
    normalize_source_path,
    tmdb_ref,
)


BGM_TO_TMDB_RENAME_PLAN_STAGE = 'rename_bgm_to_tmdb_final_plan_dry_run'
ExistingTargetPolicy = Literal['block', 'warn', 'ignore']
RenamePlanRootKey = Literal['tv_root', 'movie_root']


class TmdbRenamePlanRoots(BaseModel):
    tv_root: str = ''
    movie_root: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class TmdbRenameDestination(BaseModel):
    media_type: TmdbMediaType
    tmdb_ref: str = ''
    tmdb_id: int
    legal_node_ids: list[str] = Field(default_factory=list)
    title: str = ''
    year: int | None = None
    root_key: RenamePlanRootKey
    root_path: str = ''
    work_folder: str = ''
    season_folder: str = ''
    file_name: str = ''
    target_path: str = ''
    season_number: int | None = None
    episode_number: int | None = None
    episode_end_number: int | None = None
    episode_token: str = ''

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class TmdbRenamePlanItem(BaseModel):
    source_path: str = ''
    source_abs_path: str = ''
    disposition: BridgeMappingDisposition = 'map_to_tmdb'
    tmdb_legal_node_ids: list[str] = Field(default_factory=list)
    destination: TmdbRenameDestination | None = None
    bangumi_assignment: BgmAssignmentRef | None = None
    reason: str = ''

    @field_validator('source_path', mode='before')
    @classmethod
    def normalize_path(cls, value: object) -> str:
        return normalize_source_path(value)

    @property
    def target_path(self) -> str:
        if self.destination is None:
            return ''
        return self.destination.target_path

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


class TmdbRenamePlan(BaseModel):
    source_path: str = ''
    dry_run: bool = True
    file_mutation_allowed: bool = False
    roots: TmdbRenamePlanRoots = Field(default_factory=TmdbRenamePlanRoots)
    items: list[TmdbRenamePlanItem] = Field(default_factory=list)
    target_item_count: int = 0
    tmdb_target_count: int = 0
    tmdb_absent_count: int = 0
    supplemental_count: int = 0
    summary: str = ''

    @field_validator('source_path', mode='before')
    @classmethod
    def normalize_path(cls, value: object) -> str:
        return normalize_source_path(value)

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')


def compile_verified_bgm_to_tmdb_rename_plan(
    *,
    bridge_input: BgmToTmdbInput,
    legal_graph: TmdbLegalGraph,
    verified_plan: VerifiedBgmToTmdbPlan,
    roots: TmdbRenamePlanRoots | dict[str, Any],
    source_root: str | Path = '',
    existing_target_policy: ExistingTargetPolicy = 'block',
) -> tuple[TmdbRenamePlan, CaseVerifierResult]:
    root_model = roots if isinstance(roots, TmdbRenamePlanRoots) else TmdbRenamePlanRoots.model_validate(roots)
    assignment_by_path = {
        normalize_source_path(assignment.source_path): assignment
        for assignment in bridge_input.assignments
    }
    node_map = legal_graph.legal_node_map()
    candidate_map = legal_graph.candidate_map()
    items: list[TmdbRenamePlanItem] = []

    for mapping in verified_plan.mappings:
        source_path = normalize_source_path(mapping.source_path)
        assignment = assignment_by_path.get(source_path)
        destination = None
        if mapping.disposition == 'map_to_tmdb':
            destination = _compile_destination(
                node_ids=mapping.tmdb_legal_node_ids,
                node_map=node_map,
                candidate_map=candidate_map,
                roots=root_model,
                source_path=source_path,
            )
        items.append(
            TmdbRenamePlanItem(
                source_path=source_path,
                source_abs_path=_source_abs_path(source_root, source_path),
                disposition=mapping.disposition,
                tmdb_legal_node_ids=list(mapping.tmdb_legal_node_ids),
                destination=destination,
                bangumi_assignment=assignment,
                reason=mapping.reason,
            )
        )

    plan = TmdbRenamePlan(
        source_path=verified_plan.source_path or bridge_input.source_path,
        roots=root_model,
        items=items,
        target_item_count=sum(1 for item in items if item.destination is not None),
        tmdb_target_count=sum(len(item.tmdb_legal_node_ids) for item in items if item.destination is not None),
        tmdb_absent_count=sum(1 for item in items if item.disposition == 'tmdb_target_absent'),
        supplemental_count=sum(1 for item in items if item.disposition == 'unmapped_supplemental'),
        summary=verified_plan.summary or 'compiled BGM->TMDB final rename dry-run plan',
    )
    result = verify_bgm_to_tmdb_rename_plan(
        bridge_input=bridge_input,
        legal_graph=legal_graph,
        verified_plan=verified_plan,
        rename_plan=plan,
        existing_target_policy=existing_target_policy,
    )
    return plan, result


def verify_bgm_to_tmdb_rename_plan(
    *,
    bridge_input: BgmToTmdbInput,
    legal_graph: TmdbLegalGraph,
    verified_plan: VerifiedBgmToTmdbPlan,
    rename_plan: TmdbRenamePlan,
    existing_target_policy: ExistingTargetPolicy = 'block',
) -> CaseVerifierResult:
    issues: list[VerifierIssue] = []
    mapping_by_path = {
        normalize_source_path(mapping.source_path): mapping
        for mapping in verified_plan.mappings
    }
    item_by_path: dict[str, list[TmdbRenamePlanItem]] = {}
    for item in rename_plan.items:
        item_by_path.setdefault(normalize_source_path(item.source_path), []).append(item)

    for source_path, mapping in mapping_by_path.items():
        items = item_by_path.get(source_path, [])
        if not items:
            issues.append(_issue(
                source_path,
                'missing_rename_plan_item',
                'every verified BGM->TMDB bridge mapping must appear in the rename dry-run plan',
                related_refs=[source_path],
            ))
            continue
        if len(items) > 1:
            issues.append(_issue(
                source_path,
                'duplicate_rename_plan_item',
                'a source_path may appear only once in the final rename dry-run plan',
                related_refs=[source_path],
            ))
        for item in items:
            _verify_item_shape(issues, mapping, item)

    for source_path in item_by_path:
        if source_path not in mapping_by_path:
            issues.append(_issue(
                source_path,
                'unknown_rename_plan_source',
                'rename dry-run plan source_path is not present in the verified bridge plan',
                related_refs=[source_path],
            ))

    node_map = legal_graph.legal_node_map()
    for item in rename_plan.items:
        _verify_destination_shape(issues, item, node_map)
        _verify_target_path_safety(issues, item, existing_target_policy)

    target_counts = Counter(
        str(item.target_path)
        for item in rename_plan.items
        if item.destination is not None and str(item.target_path)
    )
    for target_path, count in target_counts.items():
        if count > 1:
            related_sources = sorted(
                item.source_path
                for item in rename_plan.items
                if item.destination is not None and item.target_path == target_path
            )
            issues.append(_issue(
                target_path,
                'duplicate_target_path',
                'two or more dry-run items resolve to the same final target path',
                related_refs=related_sources,
            ))

    if rename_plan.file_mutation_allowed:
        issues.append(_issue(
            'rename_plan',
            'file_mutation_not_allowed',
            'this BGM->TMDB final plan layer is dry-run only and must not allow file mutation',
        ))
    if not rename_plan.dry_run:
        issues.append(_issue(
            'rename_plan',
            'dry_run_flag_required',
            'this BGM->TMDB final plan layer must be marked dry_run=true',
        ))

    source_paths = {normalize_source_path(assignment.source_path) for assignment in bridge_input.assignments}
    unknown_bridge_sources = sorted(source_path for source_path in mapping_by_path if source_path not in source_paths)
    for source_path in unknown_bridge_sources:
        issues.append(_issue(
            source_path,
            'verified_plan_unknown_source',
            'verified bridge plan source_path is not present in the original BGM->TMDB input',
            related_refs=[source_path],
        ))

    blocking = [issue for issue in issues if issue.severity == 'blocked']
    return CaseVerifierResult(
        passed=not blocking,
        issues=issues,
        summary='accepted' if not blocking else f'{len(blocking)} blocking BGM->TMDB rename plan issue(s)',
    )


def run_bgm_to_tmdb_rename_plan_dry_run(
    *,
    bridge_input: BgmToTmdbInput | dict[str, Any],
    legal_graph: TmdbLegalGraph | dict[str, Any],
    verified_plan: VerifiedBgmToTmdbPlan | dict[str, Any],
    roots: TmdbRenamePlanRoots | dict[str, Any],
    source_root: str | Path = '',
    existing_target_policy: ExistingTargetPolicy = 'block',
    source_path: str | Path = '',
    write_snapshot: bool = True,
) -> dict[str, Any]:
    input_model = bridge_input if isinstance(bridge_input, BgmToTmdbInput) else BgmToTmdbInput.model_validate(bridge_input)
    graph_model = legal_graph if isinstance(legal_graph, TmdbLegalGraph) else TmdbLegalGraph.model_validate(legal_graph)
    verified_model = verified_plan if isinstance(verified_plan, VerifiedBgmToTmdbPlan) else VerifiedBgmToTmdbPlan.model_validate(verified_plan)
    plan, verifier_result = compile_verified_bgm_to_tmdb_rename_plan(
        bridge_input=input_model,
        legal_graph=graph_model,
        verified_plan=verified_model,
        roots=roots,
        source_root=source_root,
        existing_target_policy=existing_target_policy,
    )
    payload = {
        'ok': bool(verifier_result.passed),
        'status': 'accepted' if verifier_result.passed else 'invalid',
        'dry_run': True,
        'file_mutation_allowed': False,
        'mode': 'bgm_to_tmdb_final_rename_plan_dry_run',
        'bridge_input': input_model.model_dump(mode='json'),
        'tmdb_legal_graph': graph_model.model_dump(mode='json'),
        'verified_plan': verified_model.model_dump(mode='json'),
        'rename_plan': plan.model_dump(mode='json'),
        'verifier_result': verifier_result.model_dump(mode='json'),
    }
    if write_snapshot:
        write_decision_snapshot(BGM_TO_TMDB_RENAME_PLAN_STAGE, payload, source_path=source_path or plan.source_path)
    return payload


def write_bgm_to_tmdb_rename_plan_artifacts(
    *,
    output_dir: str | Path,
    rename_plan: TmdbRenamePlan,
    verifier_result: CaseVerifierResult,
) -> None:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / 'bgm_to_tmdb_rename_plan.json').write_text(
        json.dumps(rename_plan.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    (path / 'bgm_to_tmdb_rename_plan_verifier_result.json').write_text(
        json.dumps(verifier_result.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True),
        encoding='utf-8',
    )


def _compile_destination(
    *,
    node_ids: list[str],
    node_map: dict[str, TmdbLegalNode],
    candidate_map: dict[str, TmdbCandidateCard],
    roots: TmdbRenamePlanRoots,
    source_path: str,
) -> TmdbRenameDestination | None:
    nodes = [node_map[node_id] for node_id in node_ids if node_id in node_map]
    if not nodes:
        return None
    first = nodes[0]
    ref = tmdb_ref(first.media_type, first.tmdb_id)
    candidate = candidate_map.get(ref)
    title = _candidate_title(candidate, first)
    year = candidate.year if candidate is not None else None
    extension = PurePosixPath(source_path).suffix or '.mkv'

    if first.media_type == 'movie':
        root_path = str(roots.movie_root)
        work_folder = FilenameBuilder.build_movie_folder(title, _year_text(year))
        file_name = FilenameBuilder.build_movie_filename(MovieMetadata(title=title, year=_year_text(year), file_ext=extension))
        target_path = str(Path(root_path) / work_folder / file_name)
        return TmdbRenameDestination(
            media_type='movie',
            tmdb_ref=ref,
            tmdb_id=first.tmdb_id,
            legal_node_ids=list(node_ids),
            title=title,
            year=year,
            root_key='movie_root',
            root_path=root_path,
            work_folder=work_folder,
            file_name=file_name,
            target_path=target_path,
        )

    season_number = first.season_number if first.season_number is not None else 1
    episode_number = first.episode_number if first.episode_number is not None else 1
    episode_numbers = [node.episode_number for node in nodes if node.episode_number is not None]
    episode_end = episode_numbers[-1] if episode_numbers else episode_number
    root_path = str(roots.tv_root)
    work_folder = FilenameBuilder.build_movie_folder(title, _year_text(year))
    season_folder = FilenameBuilder.build_season_folder(season_number)
    episode_token = _episode_token(nodes)
    file_name = _build_episode_filename(
        title=title,
        episode_token=episode_token,
        file_ext=extension,
    )
    target_path = str(Path(root_path) / work_folder / season_folder / file_name)
    return TmdbRenameDestination(
        media_type='tv',
        tmdb_ref=ref,
        tmdb_id=first.tmdb_id,
        legal_node_ids=list(node_ids),
        title=title,
        year=year,
        root_key='tv_root',
        root_path=root_path,
        work_folder=work_folder,
        season_folder=season_folder,
        file_name=file_name,
        target_path=target_path,
        season_number=season_number,
        episode_number=episode_number,
        episode_end_number=episode_end,
        episode_token=episode_token,
    )


def _verify_item_shape(
    issues: list[VerifierIssue],
    mapping: Any,
    item: TmdbRenamePlanItem,
) -> None:
    source_path = normalize_source_path(mapping.source_path)
    if item.disposition != mapping.disposition:
        issues.append(_issue(
            source_path,
            'rename_disposition_mismatch',
            'rename plan item disposition must match the verified bridge mapping disposition',
            related_refs=[source_path],
        ))
    if list(item.tmdb_legal_node_ids) != list(mapping.tmdb_legal_node_ids):
        issues.append(_issue(
            source_path,
            'rename_target_nodes_mismatch',
            'rename plan item legal nodes must match the verified bridge mapping legal nodes',
            related_refs=[source_path, *item.tmdb_legal_node_ids],
        ))
    if mapping.disposition == 'map_to_tmdb':
        if item.destination is None:
            issues.append(_issue(
                source_path,
                'mapped_item_missing_destination',
                'map_to_tmdb bridge mappings must compile to a dry-run target destination',
                related_refs=[source_path],
            ))
    elif item.destination is not None or item.target_path:
        issues.append(_issue(
            source_path,
            'unmapped_item_has_destination',
            'tmdb_target_absent and supplemental items must not get a final media target path',
            related_refs=[source_path, item.target_path],
        ))


def _verify_destination_shape(
    issues: list[VerifierIssue],
    item: TmdbRenamePlanItem,
    node_map: dict[str, TmdbLegalNode],
) -> None:
    destination = item.destination
    if destination is None:
        return
    nodes = [node_map.get(node_id) for node_id in destination.legal_node_ids]
    if any(node is None for node in nodes):
        issues.append(_issue(
            item.source_path,
            'rename_destination_unknown_node',
            'rename destination must reference legal nodes from the TMDB legal graph',
            related_refs=destination.legal_node_ids,
        ))
        return
    typed_nodes = [node for node in nodes if node is not None]
    media_types = {node.media_type for node in typed_nodes}
    tmdb_ids = {node.tmdb_id for node in typed_nodes}
    if len(media_types) != 1 or len(tmdb_ids) != 1:
        issues.append(_issue(
            item.source_path,
            'rename_destination_mixed_tmdb_nodes',
            'a single source file can only compile to one TMDB title identity in this dry-run layer',
            related_refs=destination.legal_node_ids,
        ))
        return
    media_type = next(iter(media_types))
    if media_type != destination.media_type:
        issues.append(_issue(
            item.source_path,
            'rename_destination_media_type_mismatch',
            'destination media_type must match its TMDB legal nodes',
            related_refs=destination.legal_node_ids,
        ))
    if media_type == 'movie':
        if len(typed_nodes) != 1 or MOVIE_LEGAL_NODE_RE.fullmatch(destination.legal_node_ids[0]) is None:
            issues.append(_issue(
                item.source_path,
                'rename_movie_destination_shape',
                'movie rename destinations must contain exactly one movie:<tmdb_id> legal node',
                related_refs=destination.legal_node_ids,
            ))
        return

    seasons = [node.season_number for node in typed_nodes]
    episodes = [node.episode_number for node in typed_nodes]
    if any(season is None for season in seasons) or any(episode is None for episode in episodes):
        issues.append(_issue(
            item.source_path,
            'rename_tv_destination_missing_episode_number',
            'TV rename destinations need season and episode numbers from legal nodes',
            related_refs=destination.legal_node_ids,
        ))
        return
    if len(set(seasons)) > 1:
        issues.append(_issue(
            item.source_path,
            'rename_tv_span_crosses_seasons',
            'a single source spanning multiple TMDB seasons has ambiguous destination folder in this dry-run layer',
            related_refs=destination.legal_node_ids,
        ))
    if sorted(episodes) != list(episodes):
        issues.append(_issue(
            item.source_path,
            'rename_tv_span_order_not_monotonic',
            'TV legal nodes in a multi-episode destination must be in ascending episode order',
            related_refs=destination.legal_node_ids,
        ))
    if episodes:
        expected = list(range(int(episodes[0]), int(episodes[-1]) + 1))
        if list(episodes) != expected:
            issues.append(_issue(
                item.source_path,
                'rename_tv_span_not_contiguous',
                'TV multi-episode file naming can only represent contiguous episode ranges',
                related_refs=destination.legal_node_ids,
            ))
    for node_id in destination.legal_node_ids:
        if TV_LEGAL_NODE_RE.fullmatch(node_id) is None:
            issues.append(_issue(
                item.source_path,
                'rename_tv_destination_shape',
                'TV rename destinations must use tv:<tmdb_id>:SxxEyy legal nodes',
                related_refs=[node_id],
            ))


def _verify_target_path_safety(
    issues: list[VerifierIssue],
    item: TmdbRenamePlanItem,
    existing_target_policy: ExistingTargetPolicy,
) -> None:
    destination = item.destination
    if destination is None:
        return
    if not str(destination.root_path).strip():
        issues.append(_issue(
            item.source_path,
            'target_root_required',
            'mapped rename plan items require an explicit target root',
            related_refs=[item.source_path],
        ))
        return
    if not str(destination.target_path).strip():
        issues.append(_issue(
            item.source_path,
            'target_path_required',
            'mapped rename plan items require a target_path',
            related_refs=[item.source_path],
        ))
        return
    root = Path(destination.root_path).resolve(strict=False)
    target = Path(destination.target_path).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        issues.append(_issue(
            item.source_path,
            'target_path_outside_root',
            'target_path must stay under its configured target root',
            related_refs=[str(target), str(root)],
        ))
    if existing_target_policy != 'ignore' and Path(destination.target_path).exists():
        severity = 'warning' if existing_target_policy == 'warn' else 'blocked'
        issues.append(_issue(
            item.source_path,
            'target_path_exists',
            'target_path already exists; Trans.trans_file would refuse to overwrite by default',
            related_refs=[destination.target_path],
            severity=severity,
        ))


def _candidate_title(candidate: TmdbCandidateCard | None, node: TmdbLegalNode) -> str:
    if candidate is not None:
        for value in (candidate.display_title, candidate.original_name, candidate.original_title):
            text = str(value or '').strip()
            if text:
                return text
    return str(node.title or tmdb_ref(node.media_type, node.tmdb_id)).strip()


def _year_text(year: int | None) -> str | None:
    return str(year) if isinstance(year, int) and year > 0 else None


def _episode_token(nodes: list[TmdbLegalNode]) -> str:
    if not nodes:
        return 'S01E01'
    first = nodes[0]
    first_season = int(first.season_number if first.season_number is not None else 1)
    first_episode = int(first.episode_number if first.episode_number is not None else 1)
    if len(nodes) == 1:
        return f'S{first_season:02d}E{first_episode:02d}'
    last = nodes[-1]
    last_season = int(last.season_number if last.season_number is not None else first_season)
    last_episode = int(last.episode_number if last.episode_number is not None else first_episode)
    if first_season == last_season:
        return f'S{first_season:02d}E{first_episode:02d}-E{last_episode:02d}'
    return f'S{first_season:02d}E{first_episode:02d}-S{last_season:02d}E{last_episode:02d}'


def _build_episode_filename(*, title: str, episode_token: str, file_ext: str) -> str:
    parts = [FilenameBuilder.sanitize_path_component(title), episode_token]
    return FilenameBuilder.sanitize_path_component(' - '.join(parts)) + file_ext


def _source_abs_path(source_root: str | Path, source_path: str) -> str:
    raw_root = str(source_root or '').strip()
    if not raw_root:
        return ''
    return str((Path(raw_root) / Path(*PurePosixPath(source_path).parts)).resolve(strict=False))


def _issue(
    ref: str,
    issue_code: str,
    message: str,
    *,
    related_refs: list[str] | None = None,
    severity: Literal['info', 'warning', 'blocked', 'unknown'] = 'blocked',
) -> VerifierIssue:
    return VerifierIssue(
        ref=str(ref or ''),
        issue_code=issue_code,
        severity=severity,
        message=message,
        related_refs=[str(ref) for ref in (related_refs or []) if str(ref)],
    )
