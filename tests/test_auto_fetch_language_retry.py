from pathlib import Path
from types import SimpleNamespace

from src.subtitle.auto_fetch import SubtitleAutoFetcher
from src.subtitle.providers import SubtitleCandidate
from tests.test_auto_fetch_mapping_mode import (
    _patch_pi_runner_with_invoker,
    build_mapping_fetcher,
    make_package,
)


def _write_traditional_ass(path: Path, marker: str = "") -> Path:
    path.write_text(
        "[Script Info]\n[Events]\n"
        + "".join(
            "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,"
            f"後臺發展軟體裡面這邊還沒發現問題{marker}\n"
            for _ in range(10)
        ),
        encoding="utf-8",
    )
    return path


def test_exhausted_search_converts_unique_traditional_fallback(
    monkeypatch,
    tmp_path,
):
    fetcher, task_uuid, target = build_mapping_fetcher(monkeypatch, tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"video")
    traditional = _write_traditional_ass(
        target.with_name(f"{target.stem}.zh-TW.ass")
    )
    original = traditional.read_bytes()

    monkeypatch.setattr(
        "src.subtitle.auto_fetch.cm_get",
        lambda key, default=None: {
            "subtitle_auto_fetch_preferred_language": "zh-CN",
            "subtitle_auto_fetch_convert_traditional_fallback": True,
        }.get(key, default),
    )
    monkeypatch.setattr(fetcher, "_persist_status", lambda _uuid, result: result)

    def failed_round(**kwargs):
        retry_round = kwargs["retry_round"]
        return {
            "status": "success",
            "matched_count": 1,
            "mappings": [
                {
                    "video": target.name,
                    "language": "zh-TW",
                }
            ],
            "selections": [
                {
                    "status": "success",
                    "selection_key": f"selection-{retry_round}",
                    "selected_candidate": {
                        "source": "test",
                        "title": f"candidate-{retry_round}",
                    },
                    "selected_package": {},
                    "processor_result": {
                        "mappings": [
                            {
                                "video": target.name,
                                "language": "zh-TW",
                                "content_chinese_script": "traditional",
                            }
                        ]
                    },
                }
            ],
        }

    monkeypatch.setattr(fetcher, "_execute_fetch_round", failed_round)

    result = fetcher._execute_fetch(
        task_uuid=task_uuid,
        task_data={},
        record_data={},
        scan_scope={"type": "task", "root": None},
        missing_videos=[target],
    )
    converted = target.with_name(
        f"{target.stem}.converted.zh-CN.ass"
    )

    assert result["status"] == "success"
    assert result["preferred_language_status"] == "converted_fallback"
    assert result["conversion_fallback"]["converted_count"] == 1
    assert result["conversion_fallback"]["mappings"][0][
        "write_status"
    ] == "converted_fallback_created"
    assert converted.exists()
    assert traditional.read_bytes() == original
    assert not fetcher._has_sidecar_subtitle(target, "zh-CN")

    repeated = fetcher._convert_traditional_fallbacks([target])
    assert repeated["status"] == "success"
    assert repeated["mappings"][0]["write_status"] == (
        "converted_fallback_existing"
    )
    assert traditional.read_bytes() == original


def test_no_candidate_converts_existing_traditional_when_enabled(
    monkeypatch,
    tmp_path,
):
    fetcher, task_uuid, target = build_mapping_fetcher(monkeypatch, tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"video")
    _write_traditional_ass(target.with_name(f"{target.stem}.zh-TW.ass"))
    monkeypatch.setattr(
        "src.subtitle.auto_fetch.cm_get",
        lambda key, default=None: {
            "subtitle_auto_fetch_preferred_language": "zh-CN",
            "subtitle_auto_fetch_convert_traditional_fallback": True,
        }.get(key, default),
    )
    monkeypatch.setattr(fetcher, "_persist_status", lambda _uuid, result: result)
    monkeypatch.setattr(
        fetcher,
        "_execute_fetch_round",
        lambda **_kwargs: {
            "status": "skipped",
            "reason": "pi_fail_closed",
            "case_agent_status": "fail_closed",
            "mappings": [],
            "selections": [],
        },
    )

    result = fetcher._execute_fetch(
        task_uuid=task_uuid,
        task_data={},
        record_data={},
        scan_scope={"type": "task", "root": None},
        missing_videos=[target],
    )

    assert result["status"] == "success"
    assert result["preferred_language_status"] == "converted_fallback"
    assert result["conversion_fallback"]["converted_count"] == 1


def test_conversion_fallback_is_all_or_none_for_ambiguous_sources(
    monkeypatch,
    tmp_path,
):
    fetcher = SubtitleAutoFetcher()
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mkv"
    first.write_bytes(b"video")
    second.write_bytes(b"video")
    _write_traditional_ass(first.with_name("first.zh-TW.ass"))
    _write_traditional_ass(second.with_name("second.zh-TW.ass"), "甲")
    _write_traditional_ass(second.with_name("second.zh.ass"), "乙")

    result = fetcher._convert_traditional_fallbacks([first, second])

    assert result["status"] == "failed"
    assert result["converted_count"] == 0
    assert result["issues"] == [
        {
            "video": second.name,
            "reason": "multiple_traditional_sources",
            "distinct_source_count": 2,
        }
    ]
    assert not first.with_name("first.converted.zh-CN.ass").exists()
    assert not second.with_name("second.converted.zh-CN.ass").exists()


def _candidate(title: str) -> SubtitleCandidate:
    candidate = SubtitleCandidate(
        title=title,
        detail_url=f"https://example.com/{title}",
        source="acgrip",
    )
    candidate.thread_packages = [make_package(title, ["batch"])]
    candidate.pages_scanned = 1
    return candidate


def test_content_language_mismatch_selects_different_package(
    monkeypatch, tmp_path
):
    fetcher, task_uuid, target = build_mapping_fetcher(monkeypatch, tmp_path)
    target2 = target.with_name("Omoide no Mani (2014) - Part 2.mkv")
    candidates = [
        _candidate("wrong-script"),
        _candidate("right-script-partial"),
        _candidate("unconfirmed-script-rest"),
        _candidate("right-script-rest"),
    ]
    monkeypatch.setattr(
        fetcher.provider, "search", lambda keyword, limit=10: candidates
    )
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda item: item)
    monkeypatch.setattr(
        fetcher.provider, "load_thread_packages", lambda item: item
    )

    rounds = []

    def invoker(state):
        context = state.handle_tool("get_auto_fetch_context", {})["data"]
        retry_round = int(
            state.task_data.get("subtitle_auto_fetch_language_retry_round") or 0
        )
        rounds.append(context.get("prior_download_feedback") or [])
        state.handle_tool("search_candidates", {"keyword": "Omoide no Mani"})
        state.handle_tool(
            "load_candidate_packages", {"candidate_ref": "CD1"}
        )
        if retry_round:
            for candidate_index in range(1, retry_round + 1):
                candidate_ref = f"CD{candidate_index}"
                package_ref = f"PK{candidate_index}"
                state.handle_tool(
                    "load_candidate_packages",
                    {"candidate_ref": candidate_ref},
                )
                state.handle_tool(
                    "submit_candidate",
                    {"candidate_ref": candidate_ref, "language": "chs"},
                )
                rejected = state.handle_tool(
                    "submit_package", {"package_ref": package_ref}
                )
                assert rejected["accepted"] is False
                assert (
                    rejected["verifier_result"]["issues"][0]["issue_code"]
                    == "prior_download_language_mismatch"
                )
            selected_index = retry_round + 1
            state.handle_tool(
                "load_candidate_packages",
                {"candidate_ref": f"CD{selected_index}"},
            )
            state.handle_tool(
                "submit_candidate",
                {
                    "candidate_ref": f"CD{selected_index}",
                    "language": "chs",
                },
            )
            accepted = state.handle_tool(
                "submit_package", {"package_ref": f"PK{selected_index}"}
            )
        else:
            state.handle_tool(
                "submit_candidate", {"candidate_ref": "CD1", "language": "chs"}
            )
            accepted = state.handle_tool(
                "submit_package", {"package_ref": "PK1"}
            )
        assert accepted["accepted"] is True
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)

    downloads = []

    def fake_download(candidate, destination_dir, package=None, download_url=None):
        downloads.append((candidate.title, Path(destination_dir)))
        path = tmp_path / f"{candidate.title}.zip"
        path.write_text("subtitle", encoding="utf-8")
        return SimpleNamespace(
            status="success",
            downloaded_path=path,
            download_url=download_url,
            selected_package=package,
            download_attempts=1,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)

    def fake_process_mapping(
        archive_path,
        target_task_uuid=None,
        *,
        allowed_emby_languages=None,
    ):
        archive_stem = Path(archive_path).stem
        language_mismatches = []
        if archive_stem == "wrong-script":
            assert allowed_emby_languages is None
            pairs = [(target, "zh-TW"), (target2, "zh-TW")]
        elif archive_stem == "right-script-partial":
            assert allowed_emby_languages == {"zh-CN"}
            pairs = [(target, "zh-CN")]
            language_mismatches = [
                {
                    "subtitle": "episode2.ass",
                    "video": target2.name,
                    "target": "episode2.zh-CN.default.ass",
                    "task_uuid": target_task_uuid,
                    "language": "zh-CN",
                    "content_chinese_script": "unknown",
                    "write_status": (
                        "filtered_unconfirmed_preferred_language"
                    ),
                }
            ]
        elif archive_stem == "unconfirmed-script-rest":
            assert allowed_emby_languages == {"zh-CN"}
            pairs = []
            language_mismatches = [
                {
                    "subtitle": "episode2.ass",
                    "video": target2.name,
                    "target": "episode2.zh-CN.default.ass",
                    "task_uuid": target_task_uuid,
                    "language": "zh-CN",
                    "content_chinese_script": "unknown",
                    "write_status": (
                        "filtered_unconfirmed_preferred_language"
                    ),
                }
            ]
        else:
            assert allowed_emby_languages == {"zh-CN"}
            pairs = [(target2, "zh-CN")]
        return {
            "status": "success",
            "mapping_only": True,
            "matched_count": len(pairs),
            "mappings": [
                {
                    "subtitle": "episode.ass",
                    "video": video.name,
                    "target": f"episode.{language}.ass",
                    "task_uuid": target_task_uuid,
                    "language": language,
                    "content_chinese_script": (
                        "simplified" if language == "zh-CN" else "traditional"
                    ),
                }
                for video, language in pairs
            ],
            "language_mismatches": language_mismatches,
            "unmatched": [],
            "no_target_videos": [],
        }

    monkeypatch.setattr(
        fetcher.processor, "process_mapping", fake_process_mapping
    )

    result = fetcher.process_task_mapping(
        task_uuid, missing_videos_override=[target, target2]
    )

    assert result["status"] == "success"
    assert result["preferred_language_status"] == "satisfied_after_retry"
    assert result["language_retry_count"] == 3
    assert [item[0] for item in downloads] == [
        "wrong-script",
        "right-script-partial",
        "unconfirmed-script-rest",
        "right-script-rest",
    ]
    assert "language_retry_1" in str(downloads[1][1])
    assert "language_retry_2" in str(downloads[2][1])
    assert "language_retry_3" in str(downloads[3][1])
    assert rounds[0] == []
    assert rounds[1][0]["outcome"] == "content_language_mismatch"
    assert rounds[1][0]["attachment_label"] == "wrong-script.zip"
    assert rounds[2][-1]["outcome"] == "preferred_language_partial"
    assert rounds[3][-1]["outcome"] == "preferred_language_unconfirmed"
    assert all(
        "url" not in feedback and "download_url" not in feedback
        for round_feedback in rounds
        for feedback in round_feedback
    )
    assert {
        mapping["video"]
        for mapping in result["mappings"]
        if mapping["language"] == "zh-CN"
    } == {target.name, target2.name}
    assert {mapping["language"] for mapping in result["mappings"]} == {
        "zh-CN",
        "zh-TW",
    }


def test_content_language_mismatch_fails_when_alternatives_exhausted(
    monkeypatch, tmp_path
):
    fetcher, task_uuid, target = build_mapping_fetcher(monkeypatch, tmp_path)
    candidate = _candidate("wrong-script")
    monkeypatch.setattr(
        fetcher.provider, "search", lambda keyword, limit=10: [candidate]
    )
    monkeypatch.setattr(fetcher.provider, "prepare_candidate", lambda item: item)
    monkeypatch.setattr(
        fetcher.provider, "load_thread_packages", lambda item: item
    )

    def invoker(state):
        state.handle_tool("search_candidates", {"keyword": "Omoide no Mani"})
        state.handle_tool(
            "load_candidate_packages", {"candidate_ref": "CD1"}
        )
        state.handle_tool(
            "submit_candidate", {"candidate_ref": "CD1", "language": "chs"}
        )
        selected = state.handle_tool(
            "submit_package", {"package_ref": "PK1"}
        )
        if selected["accepted"] is False:
            state.handle_tool(
                "fail_closed",
                {
                    "reason": "no distinct candidate remains",
                    "reason_kind": "no_candidates",
                },
            )
        return {"ok": True, "returncode": 0, "argv": ["fake"]}

    _patch_pi_runner_with_invoker(monkeypatch, tmp_path, invoker)
    download_count = 0

    def fake_download(candidate, destination_dir, package=None, download_url=None):
        nonlocal download_count
        download_count += 1
        path = tmp_path / "wrong-script.zip"
        path.write_text("subtitle", encoding="utf-8")
        return SimpleNamespace(
            status="success",
            downloaded_path=path,
            download_url=download_url,
            selected_package=package,
            download_attempts=1,
        )

    monkeypatch.setattr(fetcher.provider, "download", fake_download)
    monkeypatch.setattr(
        fetcher.processor,
        "process_mapping",
        lambda archive_path, target_task_uuid=None: {
            "status": "success",
            "mapping_only": True,
            "matched_count": 1,
            "mappings": [
                {
                    "subtitle": "episode.ass",
                    "video": target.name,
                    "target": "episode.zh-TW.ass",
                    "task_uuid": target_task_uuid,
                    "language": "zh-TW",
                }
            ],
            "unmatched": [],
            "no_target_videos": [],
        },
    )

    result = fetcher.process_task_mapping(
        task_uuid, missing_videos_override=[target]
    )

    assert result["status"] == "failed"
    assert result["reason"] == "preferred_language_not_found"
    assert result["preferred_language_status"] == "not_found"
    assert result["preferred_language_missing_videos"] == [target.name]
    assert download_count == 1
    assert result["mappings"][0]["language"] == "zh-TW"
