#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补全股票【当前快照】信息：PE(TTM)、PB、总市值、流通市值、换手率、ST标记。

数据源：新浪 Market_Center.getHQNodeData（hs_a 节点，分页全量）。
注意：这是当前时点快照，不是历史每日值。历史每日 PE/PB/市值在当前网络
环境下无可用数据源（baostock/东财/网易均不通），故以快照形式冗余存储，
供回测做"当前值近似"或基本面过滤参考。

建表 snapshots(symbol PRIMARY KEY, code, name, trade, pe, pb, mktcap, nmc, turnover, is_st, updated_at)。
其中 mktcap/nmc 单位为万元；is_st = 1 表示名称含 ST/*ST。
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import datetime

import requests

LIST_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
DEFAULT_DB = "/workspace/a_share_daily.db"


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="补全股票当前快照信息")
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            symbol TEXT PRIMARY KEY,
            code TEXT,
            name TEXT,
            trade REAL,
            pe REAL,
            pb REAL,
            mktcap REAL,
            nmc REAL,
            turnover REAL,
            is_st INTEGER,
            updated_at TEXT
        )
    """)
    conn.commit()

    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})

    rows = []
    for page in range(1, 200):
        for attempt in range(4):
            try:
                resp = s.get(LIST_URL, params={
                    "page": page, "num": 100, "sort": "symbol", "asc": 1,
                    "node": "hs_a", "symbol": "",
                }, timeout=15)
                data = resp.json()
                break
            except Exception:
                data = None
                time.sleep(0.5 * (attempt + 1))
        if not data:
            break

        def tofloat(v, default=None):
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        for it in data:
            name = it.get("name", "")
            rows.append((
                it.get("symbol", ""), it.get("code", ""), name,
                tofloat(it.get("trade")),
                tofloat(it.get("per")),
                tofloat(it.get("pb")),
                tofloat(it.get("mktcap")),
                tofloat(it.get("nmc")),
                tofloat(it.get("turnoverratio")),
                1 if "ST" in name.upper() else 0,
                datetime.now().isoformat(timespec="seconds"),
            ))
        if len(data) < 100:
            break
        time.sleep(0.03)

    conn.executemany("""
        INSERT OR REPLACE INTO snapshots
        (symbol,code,name,trade,pe,pb,mktcap,nmc,turnover,is_st,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()
    log(f"快照补全完成：{len(rows)} 只，ST 股 {sum(1 for r in rows if r[9])} 只")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
