"""Run the SSI FastConnect collector independently from the FastAPI web process."""
from __future__ import annotations

import signal
import threading
import time
import uuid
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sli import config, database
from src.sli.collector import RuntimeState
from src.sli.ssi_collector import SsiCollector
from src.sli.worker_lock import CollectorWorkerLock, WorkerAlreadyRunning


def main() -> int:
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())

    lock = CollectorWorkerLock(config.DB_PATH)
    try:
        lock.acquire()
    except WorkerAlreadyRunning:
        print("SSI collector worker đã chạy; bỏ qua lần khởi động trùng.", flush=True)
        return 0

    worker_id = str(uuid.uuid4())
    runtime = RuntimeState()
    collector = SsiCollector(runtime, worker_id=worker_id)
    try:
        database.initialize()
        print("Đã khởi tạo cơ sở dữ liệu worker.", flush=True)
        abandoned = database.close_abandoned_collector_runs()
        if abandoned:
            print(f"Đã đóng {abandoned} lượt thu thập cũ bị bỏ quên.", flush=True)
        if not collector.start():
            print("Không thể khởi chạy SSI collector.", flush=True)
            return 1
        print(f"SSI collector worker đã chạy riêng; PID {__import__('os').getpid()}.", flush=True)
        last_heartbeat = 0.0
        while not stop.wait(1):
            thread = collector._thread
            if thread is None or not thread.is_alive():
                break
            try:
                if database.collector_stop_requested(worker_id):
                    print("Đã nhận yêu cầu ngắt kết nối.", flush=True)
                    break
            except Exception as exc:
                print(f"Không đọc được yêu cầu dừng: {type(exc).__name__}: {exc}", flush=True)
            if time.monotonic() - last_heartbeat >= 5:
                run_id = runtime.snapshot().get("collector_run_id")
                if run_id:
                    try:
                        database.update_collector_heartbeat(run_id, runtime.snapshot())
                    except Exception as exc:
                        print(f"Không ghi được heartbeat: {type(exc).__name__}: {exc}", flush=True)
                last_heartbeat = time.monotonic()
        final_run = database.latest_collector_run()
        return 1 if final_run and final_run.get("status") == "failed" else 0
    finally:
        collector.stop()
        thread = collector._thread
        if thread and thread.is_alive():
            thread.join()
        lock.release()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        raise
