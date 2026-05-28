"""
export_missions.py
==================
job_mission 파이프라인이 생성한 mission_output.json을 웹앱이 읽을 수 있는
missions/scenarios/*.json 형태로 변환하고 missions/index.json을 갱신한다.

주요 작업:
  1. outputs/ 폴더에서 mission_output.json 파일 수집
  2. evaluation.rubric.linked_evidence + points 기반으로 axis_signals_derived 계산
  3. missions/scenarios/{key}.json 으로 저장
  4. missions/index.json 갱신

실행:
  python scripts/export_missions.py
  python scripts/export_missions.py --outputs outputs/pilot/v1/runs/<run_id>
  python scripts/export_missions.py --dry-run   # 파일 변경 없이 결과만 출력

의존성: 표준 라이브러리만 사용 (추가 패키지 불필요)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
MISSIONS_DIR = BASE_DIR / "missions"
SCENARIOS_DIR = MISSIONS_DIR / "scenarios"
AXIS_MAPPING_PATH = BASE_DIR / "data" / "processed" / "axis_mapping.json"
DEFAULT_OUTPUTS_DIR = BASE_DIR / "outputs"

# axis_mapping.json에 없는 KNOW 항목에 대한 확장 매핑
# 활동중요도(dtlGb_7) 외에 능력(dtlGb_5) 항목도 포함
EXTENDED_AXIS_MAPPING: dict[str, str] = {
    # ── AX1: 정보분석·논리 ─────────────────────────────────
    "논리적 분석": "AX1",
    "수리력": "AX1",
    "추리력": "AX1",
    "읽고 이해하기": "AX1",
    "쓰기": "AX1",
    "기술 분석": "AX1",
    "산수와 수학": "AX1",
    "경제와 회계": "AX1",
    "과학": "AX1",
    "물리": "AX1",
    "화학": "AX1",
    "사무": "AX1",
    # ── AX2: 관찰·탐색 ────────────────────────────────────
    "사물, 서비스, 사람의 질 판단": "AX2",
    "공학과 기술": "AX2",
    "생산과 공정": "AX2",
    "품질 관리 분석": "AX2",
    # ── AX3: 전략·판단 ────────────────────────────────────
    "판단과 의사결정": "AX3",
    "복잡한 문제 해결": "AX3",
    "시스템 평가": "AX3",
    "시스템 분석": "AX3",
    # ── AX4: 리더십·조직 ──────────────────────────────────
    "경영 및 행정": "AX4",
    "재정 관리": "AX4",
    "인사 관리": "AX4",
    "자원 배분": "AX4",
    # ── AX5: 대인서비스 ───────────────────────────────────
    "설득": "AX5",
    "서비스 지향성": "AX5",
    "사회적 인지": "AX5",
    "타인 모니터링": "AX5",
    "사람들을 훈련, 교육": "AX5",
    "조직 외부인과 소통": "AX5",
}

JOB_ICONS: dict[str, str] = {
    "K000000997": "📦",
    "K000001080": "📊",
    "K000007519": "🛡️",
    "K000001179": "💹",
}


def load_axis_mapping(path: Path) -> dict[str, list[str]]:
    """axis_mapping.json을 읽어 {항목명: 축코드} 역방향 사전 반환."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    item_to_axis: dict[str, str] = {}
    for ax_code, ax_info in raw.items():
        for item in ax_info.get("items", []):
            item_to_axis[item] = ax_code
    item_to_axis.update(EXTENDED_AXIS_MAPPING)
    return item_to_axis


def compute_axis_signals(
    rubric: list[dict[str, Any]],
    item_to_axis: dict[str, str],
) -> dict[str, float]:
    """
    rubric의 linked_evidence 항목들을 5축으로 매핑하고
    각 기준의 points를 linked_evidence 항목 수로 균등 배분하여
    축별 누적 점수를 정규화한다.

    반환: {"AX1": 0.xx, "AX2": 0.xx, ..., "AX5": 0.xx} (합계 ≈ 1.0)
    """
    axes = ["AX1", "AX2", "AX3", "AX4", "AX5"]
    ax_score: dict[str, float] = {ax: 0.0 for ax in axes}

    for criterion in rubric:
        points = float(criterion.get("points", 0))
        evidence_items = criterion.get("linked_evidence", [])
        if not evidence_items or points <= 0:
            continue

        per_item = points / len(evidence_items)
        for item in evidence_items:
            ax = item_to_axis.get(item)
            if ax in ax_score:
                ax_score[ax] += per_item
            else:
                # 미매핑 항목: 5축 균등 배분
                split = per_item / len(axes)
                for a in axes:
                    ax_score[a] += split

    total = sum(ax_score.values())
    if total <= 0:
        return {ax: round(1.0 / len(axes), 4) for ax in axes}

    return {ax: round(ax_score[ax] / total, 4) for ax in axes}


def mission_key_from_path(mission_output_path: Path) -> str:
    """
    outputs/.../runs/{run_id}/jobs/{job_cd}/{difficulty}/mission_output.json
    → "{run_id}__{mission_id}" 형태의 고유 키 생성.
    """
    parts = mission_output_path.parts
    run_id = ""
    for i, p in enumerate(parts):
        if p == "runs" and i + 1 < len(parts):
            run_id = parts[i + 1]
            break

    with open(mission_output_path, encoding="utf-8") as f:
        data = json.load(f)
    mission_id = data.get("mission_id", "unknown")
    if run_id:
        return f"{run_id}__{mission_id}"
    return mission_id


def collect_mission_files(outputs_root: Path) -> list[Path]:
    """outputs_root 하위의 모든 mission_output.json을 재귀 탐색."""
    found = []
    for dirpath, _, filenames in os.walk(outputs_root):
        for fname in filenames:
            if fname == "mission_output.json":
                found.append(Path(dirpath) / fname)
    return sorted(found)


def build_scenario_filename(job_cd: str, existing_files: set[str]) -> str:
    """
    missions/scenarios/ 안에서 겹치지 않는 파일명 반환.
    예: K000000997_01.json, K000000997_02.json, …
    """
    idx = 1
    while True:
        name = f"{job_cd}_{idx:02d}.json"
        if name not in existing_files:
            return name
        idx += 1


def load_index(index_path: Path) -> dict[str, Any]:
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            return json.load(f)
    return {
        "schema_version": "missions-index.v1",
        "generated_at": str(date.today()),
        "jobs": [],
        "missions": [],
    }


def save_index(index_path: Path, index: dict[str, Any]) -> None:
    index["generated_at"] = str(date.today())
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def export_mission(
    mission_path: Path,
    item_to_axis: dict[str, str],
    scenarios_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    """
    mission_output.json 하나를 읽어 axis_signals_derived를 추가하고
    scenarios_dir에 저장한다.

    반환: missions/index.json에 등록할 엔트리 dict (실패 시 None).
    """
    try:
        with open(mission_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"  [skip] {mission_path}: read error — {exc}", file=sys.stderr)
        return None

    schema_ver = data.get("schema_version", "")
    if not schema_ver.startswith("mission_output"):
        print(f"  [skip] {mission_path}: unknown schema_version '{schema_ver}'")
        return None

    mission_id = data.get("mission_id", "")
    if not mission_id:
        print(f"  [skip] {mission_path}: no mission_id")
        return None

    job_identity = data.get("job_identity", {})
    job_cd = job_identity.get("job_cd", "")
    mission_block = data.get("mission", {})
    difficulty_block = mission_block.get("difficulty", {})
    difficulty = difficulty_block.get("level", "")
    rubric = data.get("evaluation", {}).get("rubric", [])

    # axis_signals_derived 계산 (이미 있으면 재사용)
    if "axis_signals_derived" not in data:
        data["axis_signals_derived"] = compute_axis_signals(rubric, item_to_axis)

    # 저장 파일명 결정
    existing = {f.name for f in scenarios_dir.glob("*.json")} if scenarios_dir.exists() else set()

    # 이미 동일 mission_id가 존재하면 덮어쓰기
    target_path: Path | None = None
    for f in scenarios_dir.glob("*.json"):
        try:
            with open(f, encoding="utf-8") as fh:
                existing_data = json.load(fh)
            if existing_data.get("mission_id") == mission_id:
                target_path = f
                break
        except Exception:
            continue

    if target_path is None:
        filename = build_scenario_filename(job_cd, existing)
        target_path = scenarios_dir / filename

    rel_path = f"scenarios/{target_path.name}"

    if not dry_run:
        scenarios_dir.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # index 엔트리 구성
    title = mission_block.get("title", "")
    task_type = mission_block.get("task_type", "")
    est_minutes = difficulty_block.get("estimated_time_minutes", 15)
    axis_signals = data["axis_signals_derived"]

    key = mission_key_from_path(mission_path)

    return {
        "key": key,
        "path": rel_path,
        "mission_id": mission_id,
        "job_cd": job_cd,
        "job_name": job_identity.get("job_smcl_nm", ""),
        "difficulty": difficulty,
        "title": title,
        "task_type": task_type,
        "estimated_time_minutes": est_minutes,
        "axis_signals": axis_signals,
    }


def update_index(
    index: dict[str, Any],
    new_entry: dict[str, Any],
    all_mission_entries: list[dict[str, Any]],
) -> None:
    """index에 새 미션 엔트리를 추가/갱신하고 jobs 배열도 동기화한다."""
    missions: list[dict[str, Any]] = index.setdefault("missions", [])
    jobs: list[dict[str, Any]] = index.setdefault("jobs", [])

    # 기존 같은 key 교체 또는 신규 추가
    for i, entry in enumerate(missions):
        if entry.get("key") == new_entry["key"] or entry.get("mission_id") == new_entry["mission_id"]:
            missions[i] = new_entry
            return
    missions.append(new_entry)

    # jobs 배열 동기화 (전체 엔트리 기준)
    job_cd = new_entry["job_cd"]
    job_name = new_entry["job_name"]
    job_keys = [e["key"] for e in all_mission_entries if e["job_cd"] == job_cd]

    existing_job = next((j for j in jobs if j.get("job_cd") == job_cd), None)
    if existing_job is None:
        jobs.append({
            "job_cd": job_cd,
            "job_name": job_name,
            "icon": JOB_ICONS.get(job_cd, "💼"),
            "mission_keys": job_keys,
        })
    else:
        existing_job["mission_keys"] = job_keys


def rebuild_index_from_scenarios(
    scenarios_dir: Path,
    item_to_axis: dict[str, str],
) -> dict[str, Any]:
    """scenarios/ 폴더 전체를 스캔해 index를 처음부터 재구성한다."""
    index: dict[str, Any] = {
        "schema_version": "missions-index.v1",
        "generated_at": str(date.today()),
        "jobs": [],
        "missions": [],
    }
    jobs_map: dict[str, dict[str, Any]] = {}

    for f in sorted(scenarios_dir.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue

        if not data.get("mission_id"):
            continue

        job_identity = data.get("job_identity", {})
        job_cd = job_identity.get("job_cd", "")
        mission_block = data.get("mission", {})
        difficulty_block = mission_block.get("difficulty", {})
        rubric = data.get("evaluation", {}).get("rubric", [])

        if "axis_signals_derived" not in data:
            data["axis_signals_derived"] = compute_axis_signals(rubric, item_to_axis)
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)

        # Derive key from filename (keep simple)
        key = f.stem  # e.g., K000000997_01

        entry = {
            "key": key,
            "path": f"scenarios/{f.name}",
            "mission_id": data["mission_id"],
            "job_cd": job_cd,
            "job_name": job_identity.get("job_smcl_nm", ""),
            "difficulty": difficulty_block.get("level", ""),
            "title": mission_block.get("title", ""),
            "task_type": mission_block.get("task_type", ""),
            "estimated_time_minutes": difficulty_block.get("estimated_time_minutes", 15),
            "axis_signals": data["axis_signals_derived"],
        }
        index["missions"].append(entry)

        if job_cd not in jobs_map:
            jobs_map[job_cd] = {
                "job_cd": job_cd,
                "job_name": job_identity.get("job_smcl_nm", ""),
                "job_lrcl_nm": job_identity.get("job_lrcl_nm", ""),
                "job_mdcl_nm": job_identity.get("job_mdcl_nm", ""),
                "icon": JOB_ICONS.get(job_cd, "💼"),
                "mission_keys": [],
            }
        jobs_map[job_cd]["mission_keys"].append(key)

    index["jobs"] = list(jobs_map.values())
    return index


def main() -> None:
    parser = argparse.ArgumentParser(
        description="job_mission outputs → missions/scenarios/ 변환 및 index.json 갱신"
    )
    parser.add_argument(
        "--outputs",
        default=str(DEFAULT_OUTPUTS_DIR),
        help="job_mission 출력 루트 경로 (기본: outputs/)",
    )
    parser.add_argument(
        "--missions-dir",
        default=str(MISSIONS_DIR),
        help="미션 저장 루트 경로 (기본: missions/)",
    )
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="scenarios/ 폴더 전체를 스캔해 index.json을 재구성한다",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="파일을 실제로 쓰지 않고 결과만 출력한다",
    )
    args = parser.parse_args()

    outputs_root = Path(args.outputs)
    missions_dir = Path(args.missions_dir)
    scenarios_dir = missions_dir / "scenarios"
    index_path = missions_dir / "index.json"

    if not AXIS_MAPPING_PATH.exists():
        print(f"[error] axis_mapping.json not found: {AXIS_MAPPING_PATH}", file=sys.stderr)
        sys.exit(1)

    item_to_axis = load_axis_mapping(AXIS_MAPPING_PATH)
    print(f"axis_mapping 로드: {len(item_to_axis)}개 항목")

    # --rebuild-index: scenarios/ 전체 재스캔
    if args.rebuild_index:
        print(f"\nscenarios/ 전체 재스캔 → index.json 재구성 중…")
        index = rebuild_index_from_scenarios(scenarios_dir, item_to_axis)
        if not args.dry_run:
            save_index(index_path, index)
            print(f"index.json 저장: {index_path}")
        else:
            print("[dry-run] 저장 생략")
        print(f"  직업: {len(index['jobs'])}개, 미션: {len(index['missions'])}개")
        return

    # outputs/ 에서 mission_output.json 수집
    if not outputs_root.exists():
        print(f"[info] outputs 폴더 없음: {outputs_root}. --rebuild-index 옵션으로 기존 scenarios만 재인덱싱합니다.")
        index = rebuild_index_from_scenarios(scenarios_dir, item_to_axis)
        if not args.dry_run:
            save_index(index_path, index)
        print(f"  직업: {len(index['jobs'])}개, 미션: {len(index['missions'])}개")
        return

    mission_files = collect_mission_files(outputs_root)
    print(f"\noutputs/ 탐색 결과: {len(mission_files)}개 mission_output.json 발견")

    if not mission_files:
        print("변환할 파일이 없습니다.")
        return

    index = load_index(index_path)
    exported_entries: list[dict[str, Any]] = []

    for mf in mission_files:
        print(f"  처리: {mf.relative_to(BASE_DIR) if mf.is_relative_to(BASE_DIR) else mf}")
        entry = export_mission(mf, item_to_axis, scenarios_dir, dry_run=args.dry_run)
        if entry:
            exported_entries.append(entry)
            update_index(index, entry, index.get("missions", []) + exported_entries)
            status = "[dry-run]" if args.dry_run else "→"
            print(f"    {status} missions/scenarios/{entry['path'].split('/')[-1]}  "
                  f"axis_signals={entry['axis_signals']}")

    if not args.dry_run and exported_entries:
        save_index(index_path, index)
        print(f"\nindex.json 갱신: {index_path}")

    print(f"\n완료: {len(exported_entries)}개 미션 내보냄")
    print(f"  직업: {len(index.get('jobs', []))}개, 미션: {len(index.get('missions', []))}개")


if __name__ == "__main__":
    main()
