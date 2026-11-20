#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补全指数基准日线数据，用于回测超额收益与基准对比。

指数列表：上证指数、深证成指、创业板指、沪深300、中证500、中证1000、上证50、科创50。
数据源：新浪 KLineData 接口（已验证可用）。

建表 benchmarks(symbol, name, trade_date, open, high, low, close, volume)。
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import datetime

import requests

KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)
DEFAULT_DB = "/workspace/a_share_daily.db"

INDICES = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000300", "沪深300"),
    ("sh000905", "中证500"),
    ("sh000852", "中证1000"),
    ("sh000016", "上证50"),
    ("sh000688", "科创50"),
]


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="补全指数基准日线")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--datalen", type=int, default=5000)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS benchmarks (
            symbol TEXT NOT NULL,
            name TEXT,
            trade_date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY (symbol, trade_date)
        )
    """)
    conn.commit()

    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})

    for sym, name in INDICES:
        try:
            resp = s.get(KLINE_URL, params={
                "symbol": sym, "scale": 240, "ma": "no", "datalen": args.datalen
            }, timeout=25)
            data = resp.json()
            if not isinstance(data, list) or not data:
                log(f"{name}({sym}) 无数据，跳过")
                continue
            rows = [(
                sym, name, d["day"], float(d["open"]), float(d["high"]),
                float(d["low"]), float(d["close"]), float(d["volume"]),
            ) for d in data]
            conn.executemany(
                "INSERT OR REPLACE INTO benchmarks"
                "(symbol,name,trade_date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)",
                rows,
            )
            conn.commit()
            log(f"{name}({sym}): {len(rows)} 条，{rows[0][2]} ~ {rows[-1][2]}")
        except Exception as exc:
            log(f"{name}({sym}) 失败: {exc}")
        time.sleep(0.2)

    conn.close()
    log("指数基准补全完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
