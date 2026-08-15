"""Offline regression checks for deterministic Alpha candidate selection."""
from __future__ import annotations

from app.services.alpha_discovery import build_quantitative_candidates


def make_history(base: float, *, rising: bool = False) -> list[dict]:
    records = []
    for index in range(65):
        close = base + (index * 0.015 if rising else (index % 5 - 2) * 0.02)
        records.append({
            "date": f"2026-06-{index + 1:02d}",
            "open": close - 0.03,
            "close": close,
            "high": close + 0.08,
            "low": close - 0.08,
            "volume_lots": 1000 if index < 55 else 650,
        })
    return records


def main() -> None:
    histories = {
        "sz000001": {"data": make_history(10.0)},
        "sz000002": {"data": make_history(12.0)},
        "sz000003": {"data": make_history(8.0)},
    }
    stocks = {
        "sz000001": {"name": "甲", "price": 10.0, "change_pct": 0.2, "volume": 800000, "industry": "甲行业"},
        "sz000002": {"name": "乙", "price": 12.0, "change_pct": 0.1, "volume": 900000, "industry": "乙行业"},
        # Strong intraday gain must not enter merely because volume is high.
        "sz000003": {"name": "追高", "price": 8.0, "change_pct": 6.0, "volume": 9999999, "industry": "丙行业"},
    }
    candidates, rejected = build_quantitative_candidates(stocks, histories, target_count=5)
    assert all(candidate["code"] != "sz000003" for candidate in candidates)
    assert all(candidate["technical"]["data_points"] >= 60 for candidate in candidates)
    assert all(candidate["current_price"] == stocks[candidate["code"]]["price"] for candidate in candidates)
    assert all(candidate["score"] == sum(candidate["score_breakdown"].values()) for candidate in candidates)
    print(f"alpha discovery checks passed: candidates={len(candidates)} rejected={rejected}")


if __name__ == "__main__":
    main()
