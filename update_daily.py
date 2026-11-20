#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增量更新 A 股日线数据（基于 fetch_all.py 生成的 SQLite 数据库）。

逻辑：
1. 重新拉取当前沪深京 A 股列表（自动纳入新上市股票）
2. 每只股票只拉最近 N 个交易日（默认 30，足够覆盖日常更新）
3. 用 INSERT OR REPLACE 幂等写入：新日期追加，已有日期若被数据源修正也会同步
4. 更新 stocks 基本信息与 meta 中的 last_update

用法：
    python3 update_daily.py                    # 更新最近 30 个交易日
    python3 update_daily.py --days 60          # 回溯 60 个交易日（停更较久时用）
    python3 update_daily.py --workers 8        # 并发数
    python3 update_daily.py --db /path/to.db   # 指定数据库

建议用 crontab 每日收盘后运行，例如：
    30 16 * * 1-5 /usr/bin/python3 /workspace/update_daily.py >> /workspace/update.log 2>&1
"""
from __future__ import annotations

import argparse
import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

LIST_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)
DEFAULT_DB = "/workspace/a_share_daily.db"
INSERT_SQL = (
    "INSERT OR REPLACE INTO daily_prices"
    "(symbol,code,trade_date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)"
)

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
            "Accept": "application/json, text/plain, */*",
        })
        _tls.session = s
    return s


def fetch_stock_list() -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for page in range(1, 200):
        params = {
            "page": page, "num": 100, "sort": "symbol",
            "asc": 1, "node": "hs_a", "symbol": "",
        }
        data = None
        last_err = None
        for attempt in range(4):
            try:
                resp = get_session().get(LIST_URL, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as exc:
                last_err = exc
                time.sleep(0.6 * (attempt + 1))
        if data is None:
            raise RuntimeError(f"股票列表第 {page} 页获取失败: {last_err}")
        if not data:
            break
        for item in data:
            sym = item.get("symbol", "")
            if sym and sym not in seen:
                seen.add(sym)
                result.append({
                    "symbol": sym,
                    "code": item.get("code") or sym[2:],
                    "name": item.get("name", ""),
                })
        if len(data) < 100:
            break
        time.sleep(0.03)
    return result


def fetch_kline(symbol: str, datalen: int, retries: int = 3) -> list[tuple]:
    params = {"symbol": symbol, "scale": 240, "ma": "no", "datalen": datalen}
    last_err = None
    for attempt in range(retries):
        try:
            resp = get_session().get(KLINE_URL, params=params, timeout=25)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError(f"响应不是列表: {str(data)[:120]}")
            rows: list[tuple] = []
            for d in data:
                try:
                    rows.append((
                        symbol, symbol[2:], d["day"],
                        float(d["open"]), float(d["high"]),
                        float(d["low"]), float(d["close"]),
                        int(float(d["volume"])),
                    ))
                except (KeyError, ValueError, TypeError):
                    continue
            return rows
        except Exception as exc:
            last_err = exc
            time.sleep(0.5 * (attempt + 1) + random.random() * 0.3)
    raise RuntimeError(f"{symbol} 抓取失败: {last_err}")


def main() -> int:
    parser = argparse.ArgumentParser(description="增量更新 A 股日线数据")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite 数据库路径")
    parser.add_argument("--days", type=int, default=30, help="回溯的交易日数（默认 30）")
    parser.add_argument("--workers", type=int, default=8, help="并发线程数（默认 8）")
    args = parser.parse_args()

    log("正在拉取最新股票列表 ...")
    stocks = fetch_stock_list()
    log(f"股票列表就绪：{len(stocks)} 只")

    conn = sqlite3.connect(args.db, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    stock_map = {s["symbol"]: s for s in stocks}
    total = len(stocks)
    ok = 0
    failed: list[str] = []
    new_rows = 0
    pending: list[tuple] = []
    started = time.time()
    processed = 0

    def worker(item: dict) -> tuple[str, list[tuple], str | None]:
        try:
            return item["symbol"], fetch_kline(item["symbol"], args.days), None
        except Exception as exc:
            return item["symbol"], [], str(exc)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for offset in range(0, total, 300):
            batch = stocks[offset: offset + 300]
            futures = [pool.submit(worker, s) for s in batch]
            for fut in as_completed(futures):
                sym, rows, err = fut.result()
                processed += 1
                if err or not rows:
                    failed.append(sym)
                else:
                    ok += 1
                    item = stock_map[sym]
                    conn.execute(
                        "INSERT OR REPLACE INTO stocks(symbol,code,name,exchange,updated_at) "
                        "VALUES(?,?,?,?,?)",
                        (sym, item["code"], item["name"], sym[:2],
                         datetime.now().isoformat(timespec="seconds")),
                    )
                    pending.extend(rows)
                    new_rows += len(rows)
                    if len(pending) >= 20000:
                        conn.executemany(INSERT_SQL, pending)
                        conn.commit()
                        pending.clear()
                if processed % 500 == 0 or processed == total:
                    log(f"进度 {processed}/{total} | 成功 {ok} 失败 {len(failed)}")

    if pending:
        conn.executemany(INSERT_SQL, pending)
        conn.commit()

    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_update',?)", (now,))
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_update_rows',?)", (str(new_rows),))
    conn.commit()

    db_count = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
    conn.close()
    log(
        f"增量更新完成：成功 {ok} 只，失败 {len(failed)} 只，"
        f"处理 {new_rows} 行，耗时 {(time.time() - started) / 60:.1f} 分钟，"
        f"库内累计 {db_count:,} 行"
    )
    if failed:
        log("失败代码（前 30 个）：" + ", ".join(failed[:30]))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
