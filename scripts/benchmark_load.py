from __future__ import annotations

import argparse
import gzip
import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def month_range(offset: int) -> tuple[str, str]:
    base_year = 2026
    base_month = 6 + offset
    start_year = base_year + (base_month - 1) // 12
    start_month = ((base_month - 1) % 12) + 1
    end_month_raw = base_month + 1
    end_year = base_year + (end_month_raw - 1) // 12
    end_month = ((end_month_raw - 1) % 12) + 1
    return f"{start_year:04d}-{start_month:02d}-01", f"{end_year:04d}-{end_month:02d}-01"


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


class Client:
    def __init__(self, base_url: str, gzip_enabled: bool) -> None:
        self.base_url = base_url.rstrip("/")
        self.gzip_enabled = gzip_enabled
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[bytes, int]:
        headers: dict[str, str] = {}
        data = None
        if self.gzip_enabled:
            headers["Accept-Encoding"] = "gzip"
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        with self.opener.open(req, timeout=45) as response:
            raw = response.read()
            transfer_size = len(raw)
            if response.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw, transfer_size

    def get(self, path: str) -> tuple[bytes, int]:
        return self.request("GET", path)

    def post(self, path: str, body: dict | None = None) -> tuple[bytes, int]:
        return self.request("POST", path, body or {})

    def delete(self, path: str) -> tuple[bytes, int]:
        return self.request("DELETE", path)


def prepare_database(db_path: Path, reset: bool) -> int:
    if reset and db_path.exists():
        db_path.unlink()
    os.environ["RENTAL_MANAGER_DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"

    from sqlalchemy import select

    from rental_manager.database import SessionLocal, engine, init_db
    from rental_manager.main import import_release_baseline
    from rental_manager.models import Tenant, UtilityService

    init_db()
    with SessionLocal() as session:
        if reset or not session.scalar(select(Tenant.id).limit(1)):
            import_release_baseline(session)
            session.commit()
        service = session.scalar(select(UtilityService).order_by(UtilityService.id))
        if service is None:
            raise RuntimeError("No utility service found in benchmark database")
        service_id = int(service.id)
    engine.dispose()
    return service_id


def start_server(db_path: Path, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["RENTAL_MANAGER_DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "rental_manager.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_server(client: Client) -> None:
    for _ in range(120):
        try:
            client.get("/healthz")
            return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("Benchmark server did not start")


def hit_paths(client: Client, paths: list[str]) -> int:
    transfer_size = 0
    for path in paths:
        _body, size = client.get(path)
        transfer_size += size
    return transfer_size


def measure(name: str, func: Callable[[], int], repeats: int, cleanup: Callable[[], None] | None = None) -> dict[str, float | int | str]:
    values: list[float] = []
    sizes: list[int] = []
    for _ in range(repeats):
        started = time.perf_counter()
        size = func()
        values.append((time.perf_counter() - started) * 1000)
        sizes.append(size)
        if cleanup:
            cleanup()
    warm_values = values[1:] if len(values) > 2 else values
    warm_sizes = sizes[1:] if len(sizes) > 2 else sizes
    return {
        "scenario": name,
        "median_ms": round(statistics.median(warm_values), 1),
        "best_ms": round(min(warm_values), 1),
        "worst_ms": round(max(warm_values), 1),
        "transfer_bytes": int(statistics.median(warm_sizes)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Rental Manager page/API loading through local uvicorn.")
    parser.add_argument("--database", default="data/perf_benchmark.db", help="SQLite database path relative to the repo root.")
    parser.add_argument("--keep-db", action="store_true", help="Reuse the benchmark database instead of rebuilding it.")
    parser.add_argument("--repeats", type=int, default=6, help="Repeats per scenario; the first run is treated as warmup.")
    parser.add_argument("--port", type=int, default=0, help="Port for the temporary uvicorn server; 0 picks a free port.")
    parser.add_argument("--no-gzip", action="store_true", help="Do not send Accept-Encoding: gzip.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    db_path = (ROOT_DIR / args.database).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    service_id = prepare_database(db_path, reset=not args.keep_db)
    port = args.port or free_port()
    client = Client(f"http://127.0.0.1:{port}", gzip_enabled=not args.no_gzip)
    process = start_server(db_path, port)
    created_bill_ids: list[int] = []
    draft_offset = 0

    try:
        wait_for_server(client)
        client.post("/api/auth/pin", {"pin_code": "1298", "remember_device": True})

        legacy_web_paths = [
            "/api/bootstrap",
            "/api/rent-charges",
            "/api/utility-bills",
            "/api/utilities/timeline",
            "/api/expenses",
            "/api/tariffs",
            "/api/messages/targets",
            "/api/payment-receipts/suspicious",
        ]
        legacy_mobile_paths = [
            "/api/bootstrap",
            "/api/bootstrap",
            "/api/rent-charges",
            "/api/payment-receipts/suspicious",
            "/api/bootstrap",
            "/api/utility-bills",
            "/api/utilities/timeline",
            "/api/expenses",
            "/api/tariffs",
            "/api/bootstrap",
            "/api/messages/targets",
        ]
        sectioned_app_state_paths = [
            "/api/app-state?sections=bootstrap",
            "/api/app-state?sections=registry",
            "/api/app-state?sections=rent_charges,utility_bills,expenses,tariffs",
            "/api/app-state?sections=utility_timeline,message_targets,suspicious_receipts",
        ]
        first_screen_paths = ["/", "/static/app.js", "/api/auth/status", "/api/app-state?sections=bootstrap"]

        def create_draft(refresh_mode: str) -> int:
            nonlocal draft_offset
            last_error = ""
            for _ in range(36):
                start, end = month_range(draft_offset)
                draft_offset += 1
                try:
                    body, size = client.post(
                        "/api/utility-bills/calculate",
                        {
                            "service_id": service_id,
                            "period_start": start,
                            "period_end": end,
                            "allow_estimate": True,
                        },
                    )
                    break
                except urllib.error.HTTPError as exc:
                    last_error = exc.read().decode("utf-8", errors="replace")
                    if exc.code == 400 and "Черновик за этот период" in last_error:
                        continue
                    raise RuntimeError(f"Draft benchmark failed for {start} -> {end}: {last_error}") from exc
            else:
                raise RuntimeError(f"Draft benchmark did not find a free period: {last_error}")
            created_bill_ids.append(int(json.loads(body.decode("utf-8"))["id"]))
            if refresh_mode == "app_state":
                return size + hit_paths(client, ["/api/app-state"])
            if refresh_mode == "sectioned":
                return size + hit_paths(client, sectioned_app_state_paths)
            return size + hit_paths(client, legacy_web_paths)

        def cleanup_drafts() -> None:
            while created_bill_ids:
                bill_id = created_bill_ids.pop()
                try:
                    client.delete(f"/api/utility-bills/{bill_id}")
                except Exception:
                    pass

        results = [
            measure("first_screen_bootstrap", lambda: hit_paths(client, first_screen_paths), args.repeats),
            measure("full_app_state", lambda: hit_paths(client, ["/api/app-state"]), args.repeats),
            measure("first_screen_sectioned", lambda: hit_paths(client, ["/", "/static/app.js", "/api/auth/status", *sectioned_app_state_paths]), args.repeats),
            measure("web_app_state", lambda: hit_paths(client, ["/api/app-state"]), args.repeats),
            measure("web_sectioned_app_state", lambda: hit_paths(client, sectioned_app_state_paths), args.repeats),
            measure("web_legacy_load_all", lambda: hit_paths(client, legacy_web_paths), args.repeats),
            measure("mobile_app_state", lambda: hit_paths(client, ["/api/app-state"]), args.repeats),
            measure("mobile_sectioned_app_state", lambda: hit_paths(client, sectioned_app_state_paths), args.repeats),
            measure("mobile_legacy_tab_switches", lambda: hit_paths(client, legacy_mobile_paths), args.repeats),
            measure("create_draft_app_state_flow", lambda: create_draft("app_state"), max(3, min(args.repeats, 6)), cleanup_drafts),
            measure("create_draft_sectioned_flow", lambda: create_draft("sectioned"), max(3, min(args.repeats, 6)), cleanup_drafts),
            measure("create_draft_legacy_flow", lambda: create_draft("legacy"), max(3, min(args.repeats, 6)), cleanup_drafts),
        ]
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return
        print("scenario                 median_ms  best_ms  worst_ms  transfer_bytes")
        for item in results:
            print(
                f"{item['scenario']:<24}"
                f"{item['median_ms']:>9}"
                f"{item['best_ms']:>9}"
                f"{item['worst_ms']:>10}"
                f"{item['transfer_bytes']:>16}"
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()
