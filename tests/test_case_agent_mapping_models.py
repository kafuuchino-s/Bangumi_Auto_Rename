from src.rename.case_agent.models import (
    MappingDraftAccounting,
    MappingDraft,
    MappingDraftPatch,
    MappingDraftRow,
)


def test_mapping_models_default_values_can_be_constructed():
    row = MappingDraftRow()
    draft = MappingDraft()
    patch = MappingDraftPatch()

    assert row.local_ref_kind == 'file'
    assert row.selected_target_kind == 'none'
    assert row.mapping_mode == 'unresolved'
    assert row.status == 'open'
    assert draft.draft_ref == 'MD1'
    assert draft.rows == []
    assert draft.version == 0
    assert patch.op == 'mark_unresolved'


def test_invalid_enum_values_are_rejected():
    for model, payload in [
        (MappingDraftRow, {'local_ref_kind': 'bad'}),
        (MappingDraftRow, {'selected_target_kind': 'bad'}),
        (MappingDraftRow, {'mapping_mode': 'bad'}),
        (MappingDraftRow, {'status': 'bad'}),
        (MappingDraftRow, {'disposition': 'bad'}),
        (MappingDraftPatch, {'op': 'bad'}),
        (MappingDraftPatch, {'mapping_mode': 'bad'}),
        (MappingDraftAccounting, {'extra': 1}),
    ]:
        try:
            model.model_validate(payload)
            assert False, f'expected validation error for {model.__name__}'
        except Exception as exc:
            assert 'literal_error' in str(exc) or 'extra_forbidden' in str(exc)


def test_mapping_draft_patch_supports_current_ops_and_aliases():
    for op in [
        'map_to_bangumi',
        'mark_non_bangumi_or_supplemental',
        'needs_more_evidence',
        'mark_unaligned_fail_closed',
        'propose_span_mapping',
    ]:
        patch = MappingDraftPatch.model_validate({'op': op})
        assert patch.op == op


def test_mapping_draft_patch_rejects_unsupported_op():
    try:
        MappingDraftPatch.model_validate({'op': 'unsupported'})
        assert False, 'expected validation error for unsupported op'
    except Exception as exc:
        assert 'literal_error' in str(exc)


def test_mapping_draft_accounting_defaults_and_strict_schema():
    accounting = MappingDraftAccounting()

    assert accounting.main_file_count == 0
    assert accounting.accounted_for_count == 0
    assert accounting.accepted_accounting_ready is False

    schema = accounting.model_json_schema()
    assert schema.get('additionalProperties') is False
    assert getattr(MappingDraftAccounting, 'model_config').get('extra') == 'forbid'


def test_mapping_models_use_strict_schema_without_open_dicts():
    for model in [MappingDraftRow, MappingDraft, MappingDraftPatch]:
        schema = model.model_json_schema()
        assert schema.get('additionalProperties') is False
        assert getattr(model, 'model_config').get('extra') == 'forbid'
