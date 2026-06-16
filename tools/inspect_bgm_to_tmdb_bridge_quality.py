"""Deep quality audit for bgm-to-tmdb bridge results."""
import json
import re
import sys
from pathlib import Path
from collections import Counter, defaultdict


def load_result(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_legal_nodes(graph: dict) -> set[str]:
    nodes: set[str] = set()
    for cand in graph.get("candidates", []):
        for season in cand.get("season_cards", []):
            nodes.update(season.get("legal_node_ids", []))
        for movie in cand.get("movie_cards", []):
            nodes.add(movie.get("tmdb_ref", ""))
        for node in cand.get("legal_nodes", []):
            nodes.add(node.get("legal_node_id", ""))
    nodes.discard("")
    return nodes


def extract_legal_node_bounds(graph: dict) -> dict[str, int]:
    """Return {node_id: max_episode_number} for TV episodes."""
    bounds: dict[str, int] = {}
    for cand in graph.get("candidates", []):
        for season in cand.get("season_cards", []):
            for node_id in season.get("legal_node_ids", []):
                m = re.fullmatch(r"tv:(\d+):S(\d+)E(\d+)", node_id)
                if m:
                    bounds[node_id] = int(m.group(3))
    return bounds


def extract_case_input_assignments(case_input_path: Path) -> list[dict]:
    data = load_result(case_input_path)
    return data.get("context", {}).get("bridge_input", {}).get("assignments", [])


def find_case_input_path(data: dict) -> Path | None:
    cmd = data.get("sample_runner", {}).get("runtime_command", [])
    for i, arg in enumerate(cmd):
        if arg == "--input" and i + 1 < len(cmd):
            return Path(cmd[i + 1])
    return None


def main() -> int:
    output_dir = Path(
        "C:/Users/kafuuchino/CodeProjects/Bangumi_Auto_Rename"
        "/tests/sample_pool/generated/bgm_to_tmdb_bridge_gate_20260615_123631_710"
    )
    result_files = sorted(
        p for p in output_dir.glob("sample_*.json")
        if not p.name.endswith(".progress.json")
    )

    stats = Counter()
    disposition_stats = Counter()
    node_format_errors = 0
    empty_map_to_tmdb = 0
    missing_disposition = 0
    missing_in_legal_graph = 0
    assignments_without_mapping = 0
    extra_bridge_mappings = 0
    type_mismatch = 0
    per_sample_dispositions = {}
    summaries = []
    absent_details: list[tuple[str, str, str]] = []

    for path in result_files:
        data = load_result(path)
        status = data.get("status", "unknown")
        stats[status] += 1

        sample_name = path.stem
        summary = data.get("summary", "")
        summaries.append((sample_name, summary))

        brr = data.get("bridge_run_result", {})
        bridge = brr.get("bridge_draft", {})
        mappings = bridge.get("mappings", [])
        legal_graph = brr.get("tmdb_legal_graph", {})
        legal_node_ids = extract_legal_nodes(legal_graph)
        bounds = extract_legal_node_bounds(legal_graph)

        case_input_path = find_case_input_path(data)
        bg_assignments = (
            extract_case_input_assignments(case_input_path)
            if case_input_path and case_input_path.exists()
            else []
        )
        bg_assignment_paths = {a.get("source_path") for a in bg_assignments}

        mapping_paths = {m.get("source_path") for m in mappings}

        sample_dispositions = Counter()
        local_nodes: set[str] = set()
        for m in mappings:
            disp = m.get("disposition", "missing")
            sample_dispositions[disp] += 1
            disposition_stats[disp] += 1
            if disp == "missing":
                missing_disposition += 1
            nodes = m.get("tmdb_legal_node_ids", [])
            if disp == "map_to_tmdb":
                local_nodes.update(nodes)
                if not nodes:
                    empty_map_to_tmdb += 1
            for node in nodes:
                if not re.fullmatch(r"(tv|movie):\d+(:S\d+E\d+)?", node):
                    node_format_errors += 1
                    print(f"    format error: {node} (sample: {sample_name})")
            if disp == "tmdb_target_absent":
                absent_details.append(
                    (sample_name, m.get("source_path", ""), m.get("reason", ""))
                )

        for node in local_nodes:
            if node not in legal_node_ids:
                missing_in_legal_graph += 1
                print(f"    node not in legal graph: {node} in {sample_name}")

        # Compare with local->bgm assignments
        for sp in mapping_paths:
            if sp and sp not in bg_assignment_paths:
                extra_bridge_mappings += 1
                print(f"    extra bridge mapping source_path: {sp} in {sample_name}")

        per_sample_dispositions[sample_name] = dict(sample_dispositions)

    print("=== Status summary ===")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")

    print("\n=== Disposition summary ===")
    for k, v in sorted(disposition_stats.items()):
        print(f"  {k}: {v}")

    print("\n=== Quality issues ===")
    print(f"  missing_disposition: {missing_disposition}")
    print(f"  empty_map_to_tmdb: {empty_map_to_tmdb}")
    print(f"  tmdb_legal_node_ids format errors: {node_format_errors}")
    print(f"  nodes_missing_in_legal_graph: {missing_in_legal_graph}")
    print(f"  assignments_without_bridge_mapping: {assignments_without_mapping}")
    print(f"  extra_bridge_mappings: {extra_bridge_mappings}")
    print(f"  type_mismatch: {type_mismatch}")

    print("\n=== Per-sample disposition breakdown (first 10) ===")
    for name in sorted(per_sample_dispositions)[:10]:
        print(f"  {name}: {per_sample_dispositions[name]}")

    print("\n=== Random-ish spot-check summaries ===")
    for name, summary in summaries[::20][:10]:
        print(f"  {name}: {summary[:120]}")

    print("\n=== Duplicate TMDB target nodes within the same sample ===")
    intra_sample_duplicates = 0
    for path in result_files:
        data = load_result(path)
        sample_name = path.stem
        brr = data.get("bridge_run_result", {})
        bridge = brr.get("bridge_draft", {})
        nodes: list[str] = []
        for m in bridge.get("mappings", []):
            if m.get("disposition") == "map_to_tmdb":
                nodes.extend(m.get("tmdb_legal_node_ids", []))
        seen: set[str] = set()
        for node in nodes:
            if node in seen:
                intra_sample_duplicates += 1
                print(f"    {sample_name}: duplicate node {node}")
            seen.add(node)
    print(f"  Intra-sample duplicate node mappings: {intra_sample_duplicates}")

    print("\n=== Duplicate TMDB target nodes across all samples ===")
    node_to_sources: dict[str, list[str]] = defaultdict(list)
    for path in result_files:
        data = load_result(path)
        sample_name = path.stem
        brr = data.get("bridge_run_result", {})
        bridge = brr.get("bridge_draft", {})
        for m in bridge.get("mappings", []):
            if m.get("disposition") == "map_to_tmdb":
                for node in m.get("tmdb_legal_node_ids", []):
                    node_to_sources[node].append(
                        f"{sample_name}::{m.get('source_path', '')}"
                    )
    duplicates = {k: v for k, v in node_to_sources.items() if len(v) > 1}
    print(f"  Total distinct nodes: {len(node_to_sources)}")
    print(f"  Nodes mapped by more than one source: {len(duplicates)}")
    for node, sources in sorted(duplicates.items())[:10]:
        print(f"    {node}: {len(sources)} sources")
        for src in sources[:3]:
            print(f"      - {src}")
        if len(sources) > 3:
            print(f"      ... and {len(sources) - 3} more")

    print("\n=== tmdb_target_absent details ===")
    for sample_name, sp, reason in absent_details:
        print(f"  {sample_name}: {sp}")
        print(f"    reason: {reason[:160]}")

    print(f"\nTotal result files inspected: {len(result_files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
