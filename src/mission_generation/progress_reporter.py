# 긴 미션 생성 run의 현재 단계를 콘솔에 사람이 읽을 수 있게 출력한다.

from __future__ import annotations

import threading

from .utils import seoul_now


class ConsoleProgressReporter:
    """긴 pilot run에서 현재 단계가 보이도록 사람이 읽는 콘솔 메시지만 출력한다."""

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = bool(enabled)
        self._lock = threading.Lock()

    def emit(self, message: str) -> None:
        """enabled일 때만 timestamp가 붙은 진행 메시지를 출력한다."""

        if not self.enabled:
            return
        timestamp = seoul_now().strftime("%H:%M:%S")
        with self._lock:
            print(f"[{timestamp}] {message}", flush=True)

    def run_started(self, *, run_id: str | None, total_targets: int, concurrency: int) -> None:
        """pilot run 시작 시 전체 target 수와 병렬도를 알린다."""

        self.emit(f"run started - run_id={run_id} targets={total_targets} concurrency={concurrency}")

    def run_finished(self, *, saved_count: int, failed_count: int, repair_used_count: int) -> None:
        """pilot run 종료 시 저장/실패/repair 사용 건수를 알린다."""

        self.emit(f"run finished - saved={saved_count} failed={failed_count} repair_used={repair_used_count}")

    def target(
        self,
        *,
        current: int | None,
        total: int | None,
        job_cd: str,
        difficulty_code: str,
        stage: str,
        detail: str | None = None,
    ) -> None:
        """직무/난이도 하나가 어느 단계까지 왔는지 출력한다."""

        if current is None or total is None:
            prefix = f"{job_cd} {difficulty_code}"
        else:
            prefix = f"[{current}/{total}] {job_cd} {difficulty_code}"
        suffix = f" - {stage}"
        if detail:
            suffix += f" ({detail})"
        self.emit(prefix + suffix)
