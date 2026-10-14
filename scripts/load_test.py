"""Simple load test: how many orders per second can the API accept?

Start the API first (``python -m app.cli run``), then:

    python scripts/load_test.py --orders 2000 --workers 16

Each worker thread sends ``POST /orders`` requests with synthetic data and
records latency. The script prints throughput and latency percentiles. Numbers
depend heavily on the machine, so report them together with the hardware and
settings used.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.domain.synthetic import SyntheticDataGenerator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--orders", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    with httpx2.Client(base_url=args.base_url, timeout=30) as client:
        inventory = client.get("/inventory").raise_for_status().json()
    ndcs = [item["drug"]["ndc"] for item in inventory]
    if not ndcs:
        raise SystemExit("No drugs found. Seed the database first: python -m app.cli seed")

    gen = SyntheticDataGenerator(args.seed)
    payloads = [
        {
            "patient_ref": gen.patient_ref(),
            "drug_ndc": gen.rng.choice(ndcs),
            "quantity": 30,
            "priority": gen.priority().value,
        }
        for _ in range(args.orders)
    ]

    latencies: list[float] = []
    errors = 0
    limits = httpx2.Limits(max_connections=args.workers, max_keepalive_connections=args.workers)

    with httpx2.Client(base_url=args.base_url, timeout=30, limits=limits) as client:

        def send(payload: dict[str, object]) -> float | None:
            start = time.perf_counter()
            response = client.post("/orders", json=payload)
            elapsed = time.perf_counter() - start
            return elapsed if response.status_code == 201 else None

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for result in pool.map(send, payloads):
                if result is None:
                    errors += 1
                else:
                    latencies.append(result)
        wall = time.perf_counter() - started

    latencies.sort()

    def pct(p: float) -> float:
        return latencies[min(int(len(latencies) * p), len(latencies) - 1)] * 1000

    print(f"orders sent:      {args.orders}  (workers: {args.workers})")
    print(f"succeeded:        {len(latencies)}   failed: {errors}")
    print(f"wall time:        {wall:.2f} s")
    print(f"throughput:       {len(latencies) / wall:.1f} orders/s")
    if latencies:
        print(f"latency mean:     {statistics.mean(latencies) * 1000:.1f} ms")
        print(f"latency p50/p95/p99: {pct(0.50):.1f} / {pct(0.95):.1f} / {pct(0.99):.1f} ms")


if __name__ == "__main__":
    main()
