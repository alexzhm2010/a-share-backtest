#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描补齐已退市股票的历史K线，消除幸存者偏差。

原理：
- 新浪 KLineData 对已退市股票代码仍返回历史K线（最后日期=退市日）
- 对完全不存在的代码返回 null
- 因此扫描 A 股代码空间，凡"返回非null数据但最后交易日远早于当前"者，即为退市股

代码段（A股历史上用过的段）：
- 沪市：600000-600999, 601000-601999, 603000-603999, 605000-605999
- 深市：000001-000999, 001000-001999, 002000-002999, 003000-003999
- 创业板：300000-301999
- 老B股等：200000-200999(深B), 900000-900999(沪B) —— 也纳入

为控制请求量，用 datalen=2 探测（只需最后日期），命中退市股后再拉全历史。
支持断点续跑、进度保存。

用法：
    python3 add_delisted.py                      # 全扫描
    python3 add_delisted.py --limit-codes 500    # 只扫前500个代码（测试）
    python3 add_delisted.py --workers 20
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)
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


def build_codes() -> list[str]:
    """生成待扫描的代码列表（带交易所前缀）。"""
    codes = []
    ranges = [
        ("sh", 600000, 601999),   # 沪市主板
        ("sh", 603000, 603999),
        ("sh", 605000, 605999),
        ("sh", 688000, 688999),   # 科创板
        ("sh", 900000, 900999),   # 沪B
        ("sz", 1, 3999),          # 深市主板+中小板 (000001起)
        ("sz", 300000, 302999),   # 创业板
        ("sz", 200000, 200999),   # 深B
    ]
    for prefix, lo, hi in ranges:
        for n in range(lo, hi + 1):
            codes.append(f"{prefix}{n:06d}")
    return codes


def probe(symbol: str, retries: int = 2) -> str | None:
    """探测代码最后交易日。返回 None 表示代码不存在。"""
    for attempt in range(retries):
        try:
            resp = get_session().get(KLINE_URL, params={
                "symbol": symbol, "scale": 240, "ma": "no", "datalen": 2
            }, timeout=12)
            data = resp.json()
            if not isinstance(data, list) or not data:
                return None
            return data[-1]["day"]
        except Exception:
            if attempt < retries - 1:
                time.sleep(0.3 + random.random() * 0.2)
    return None


def fetch_full_history(symbol: str, datalen: int = 5000, retries: int = 3) -> list[tuple]:
    """拉退市股全历史K线，返回入库元组（含复权因子占位，后续补复权）。"""
    for attempt in range(retries):
        try:
            resp = get_session().get(KLINE_URL, params={
                "symbol": symbol, "scale": 240, "ma": "no", "datalen": datalen
            }, timeout=25)
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError("非列表")
            rows = []
            for d in data:
                try:
                    rows.append((
                        symbol, symbol[2:], d["day"],
                        float(d["open"]), float(d["high"]), float(d["low"]),
                        float(d["close"]), int(float(d["volume"])),
                    ))
                except (KeyError, ValueError, TypeError):
                    continue
            return rows
        except Exception:
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描补齐退市股票数据")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--datalen", type=int, default=5000)
    ap.add_argument("--limit-codes", type=int, default=0, help="只扫前N个代码（测试）")
    ap.add_argument("--stale-before", default="2026-10-01",
                    help="最后交易日早于此日期判定为退市（默认2026-10-01）")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    all_codes = build_codes()
    if args.limit_codes > 0:
        all_codes = all_codes[: args.limit_codes]

    # 已入库股票（在市+已补退市）
    existing = {r[0] for r in conn.execute("SELECT symbol FROM stocks")}
    # 已确认不存在的代码（断点续跑缓存）
    conn.execute("""CREATE TABLE IF NOT EXISTS scan_cache (
        symbol TEXT PRIMARY KEY, last_date TEXT, is_delisted INTEGER)""")
    conn.commit()
    scanned = {r[0] for r in conn.execute("SELECT symbol FROM scan_cache")}

    todo = [c for c in all_codes if c not in existing and c not in scanned]
    log(f"待扫描代码 {len(todo)} 个（总 {len(all_codes)}，已入库 {len(existing)}，已扫描 {len(scanned)}）")
    if not todo:
        log("无待扫描代码。")
        conn.close()
        return 0

    delisted: list[str] = []
    started = time.time()
    checked = 0

    # 阶段1：探测哪些代码是退市股
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(probe, c): c for c in todo}
        for fut in as_completed(futs):
            sym = futs[fut]
            last = fut.result()
            checked += 1
            if last is None:
                conn.execute("INSERT OR IGNORE INTO scan_cache(symbol,last_date,is_delisted) VALUES(?,?,0)",
                             (sym, ""))
            elif last < args.stale_before:
                # 退市股
                conn.execute("INSERT OR REPLACE INTO scan_cache(symbol,last_date,is_delisted) VALUES(?,?,1)",
                             (sym, last))
                delisted.append(sym)
            else:
                # 在市但不在stocks表（可能有新上市未抓到的）
                conn.execute("INSERT OR IGNORE INTO scan_cache(symbol,last_date,is_delisted) VALUES(?,?,0)",
                             (sym, last))
            if checked % 500 == 0:
                conn.commit()
                el = time.time() - started
                log(f"探测进度 {checked}/{len(todo)} | 发现退市股 {len(delisted)} | "
                    f"{checked/el:.0f}个/秒 | 预计剩余 {(len(todo)-checked)/(checked/el)/60:.1f}分钟")
    conn.commit()
    log(f"探测完成：发现退市股 {len(delisted)} 只，耗时 {(time.time()-started)/60:.1f} 分钟")

    # 阶段2：拉退市股全历史并入库
    ok = 0
    fail = []
    for i, sym in enumerate(delisted, 1):
        rows = fetch_full_history(sym, args.datalen)
        if not rows:
            fail.append(sym)
            continue
        # 写 stocks 表（标记 delisted）
        name = ""
        conn.execute(
            "INSERT OR REPLACE INTO stocks(symbol,code,name,exchange,updated_at) VALUES(?,?,?,?,?)",
            (sym, sym[2:], name, sym[:2], datetime.now().isoformat(timespec="seconds")),
        )
        conn.executemany("""
            INSERT OR REPLACE INTO daily_prices
            (symbol,code,trade_date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)
        """, rows)
        conn.commit()
        ok += 1
        if i % 50 == 0 or i == len(delisted):
            log(f"退市股入库 {i}/{len(delisted)} | 成功 {ok} 失败 {len(fail)}")

    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('delisted_scan_done',?)",
                 (datetime.now().isoformat(timespec="seconds"),))
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('delisted_count',?)", (str(ok),))
    conn.commit()
    conn.close()
    log(f"退市股补齐完成：入库 {ok} 只，失败 {len(fail)} 只，"
        f"总耗时 {(time.time()-started)/60:.1f} 分钟")
    if fail:
        log("失败代码（前30）：" + ", ".join(fail[:30]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
