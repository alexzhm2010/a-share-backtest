#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补全后复权价格数据（基于新浪 hfq.js 复权因子）。

原理：新浪 getKLineData 返回的是【不复权】原始价；hfq.js 给出每个除权日的
后复权因子，对交易日 t 取因子表中 d <= t 的最大日期对应的因子 f，
则 后复权价 = 原始价 × f。已用除权日前后价格连续性验证算法正确。

在 daily_prices 表新增列：adj_factor（后复权因子）、adj_close（后复权收盘价）。
支持断点续跑（已处理的股票跳过）。

用法：
    python3 add_adjusted_prices.py                 # 全量
    python3 add_adjusted_prices.py --limit 100     # 测试
    python3 add_adjusted_prices.py --workers 8
"""
from __future__ import annotations

import argparse
import bisect
import json
import random
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

FACTOR_URL = "https://finance.sina.com.cn/realstock/company/{sym}/hfq.js"
DEFAULT_DB = "/workspace/a_share_daily.db"

_tls = threading.local()


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def get_session() -> requests.Session:
    s = getattr(_tls, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://finance.sina.com.cn/",
        })
        _tls.session = s
    return s


def fetch_factors(symbol: str, retries: int = 3) -> list[tuple[str, float]]:
    """返回按日期升序的 [(除权日, 后复权因子), ...]，含 1900-01-01 兜底。"""
    url = FACTOR_URL.format(sym=symbol)
    last_err = None
    for attempt in range(retries):
        try:
            resp = get_session().get(url, timeout=20)
            resp.raise_for_status()
            m = re.search(r"\{.*\}", resp.text, re.S)
            if not m:
                raise ValueError("未找到因子JSON")
            data = json.loads(m.group(0)).get("data", [])
            if not data:
                raise ValueError("因子表为空")
            pairs = [(d["d"], float(d["f"])) for d in data]
            pairs.sort(key=lambda x: x[0])
            if pairs[0][0] != "1900-01-01":
                pairs.insert(0, ("1900-01-01", 1.0))
            return pairs
        except Exception as exc:
            last_err = exc
            time.sleep(0.5 * (attempt + 1) + random.random() * 0.3)
    raise RuntimeError(f"{symbol} 因子获取失败: {last_err}")


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_prices)")}
    if "adj_factor" not in cols:
        conn.execute("ALTER TABLE daily_prices ADD COLUMN adj_factor REAL")
    if "adj_close" not in cols:
        conn.execute("ALTER TABLE daily_prices ADD COLUMN adj_close REAL")
    conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description="补全后复权价格")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    ensure_columns(conn)

    symbols = [r[0] for r in conn.execute("SELECT symbol FROM stocks ORDER BY symbol")]
    if args.limit > 0:
        symbols = symbols[: args.limit]
    # 断点续跑：跳过已处理（adj_factor 非空占比高的股票）
    todo = []
    for sym in symbols:
        n = conn.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE symbol=? AND adj_factor IS NOT NULL", (sym,)
        ).fetchone()[0]
        if n == 0:
            todo.append(sym)
    log(f"待处理股票 {len(todo)} 只（总 {len(symbols)} 只）")
    if not todo:
        log("全部已处理，退出。")
        conn.close()
        return 0

    ok = 0
    failed: list[str] = []
    started = time.time()

    def worker(sym: str) -> tuple[str, list[tuple[str, float]] | None, str | None]:
        try:
            return sym, fetch_factors(sym), None
        except Exception as exc:
            return sym, None, str(exc)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(worker, s) for s in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            sym, factors, err = fut.result()
            if err or not factors:
                failed.append(sym)
            else:
                dates = [d for d, _ in factors]
                vals = [f for _, f in factors]
                rows = conn.execute(
                    "SELECT trade_date, close FROM daily_prices WHERE symbol=? ORDER BY trade_date", (sym,)
                ).fetchall()
                updates = []
                for td, close in rows:
                    idx = bisect.bisect_right(dates, td) - 1
                    f = vals[max(idx, 0)]
                    updates.append((f, close * f, sym, td))
                conn.executemany(
                    "UPDATE daily_prices SET adj_factor=?, adj_close=? WHERE symbol=? AND trade_date=?",
                    updates,
                )
                conn.commit()
                ok += 1
            if i % 200 == 0 or i == len(todo):
                el = time.time() - started
                log(f"进度 {i}/{len(todo)} | 成功 {ok} 失败 {len(failed)} | 耗时 {el/60:.1f} 分钟")

    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('adj_prices_done',?)",
                 (datetime.now().isoformat(timespec="seconds"),))
    conn.commit()
    conn.close()
    log(f"复权补全完成：成功 {ok} 只，失败 {len(failed)} 只，耗时 {(time.time()-started)/60:.1f} 分钟")
    if failed:
        log("失败代码（前30）：" + ", ".join(failed[:30]))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
