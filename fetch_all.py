#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全量抓取 A 股近 20 年日线数据到 SQLite。

数据源：新浪财经（实测在国内节点稳定可用，单次可返回 5000 个交易日）。

用法：
    python3 fetch_all.py                      # 全量抓取（约 5579 只）
    python3 fetch_all.py --limit 100           # 只抓前 100 只（先测试）
    python3 fetch_all.py --workers 12          # 调整并发数
    python3 fetch_all.py --resume              # 跳过已成功抓取的股票

数据库表：
    stocks       股票基本信息
    daily_prices 日线行情（symbol + trade_date 为主键）
    meta         抓取元信息
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
    """分页拉取沪深京全部 A 股列表。"""
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
    """拉取单只股票日线，返回可直接入库的元组列表。"""
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
                        symbol,
                        symbol[2:],
                        d["day"],
                        float(d["open"]),
                        float(d["high"]),
                        float(d["low"]),
                        float(d["close"]),
                        int(float(d["volume"])),
                    ))
                except (KeyError, ValueError, TypeError):
                    continue
            return rows
        except Exception as exc:
            last_err = exc
            time.sleep(0.5 * (attempt + 1) + random.random() * 0.3)
    raise RuntimeError(f"{symbol} 抓取失败: {last_err}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            symbol TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            name TEXT,
            exchange TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS daily_prices (
            symbol TEXT NOT NULL,
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (symbol, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_code_date ON daily_prices(code, trade_date);
        CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_prices(trade_date);
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="全量抓取 A 股近 20 年日线到 SQLite")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite 数据库路径")
    parser.add_argument("--workers", type=int, default=8, help="并发线程数（默认 8）")
    parser.add_argument("--datalen", type=int, default=5000, help="每只股票抓取的交易日数（默认 5000）")
    parser.add_argument("--limit", type=int, default=0, help="只抓前 N 只用于测试（0=全部）")
    parser.add_argument("--resume", action="store_true", help="跳过已成功抓取的股票")
    args = parser.parse_args()

    log("正在拉取沪深京 A 股股票列表 ...")
    stocks = fetch_stock_list()
    log(f"股票列表就绪：{len(stocks)} 只")
    if args.limit > 0:
        stocks = stocks[: args.limit]
        log(f"测试模式：仅处理前 {len(stocks)} 只")

    conn = sqlite3.connect(args.db, check_same_thread=False)
    init_db(conn)

    if args.resume:
        done = {row[0] for row in conn.execute(
            "SELECT symbol FROM stocks WHERE updated_at IS NOT NULL"
        )}
        before = len(stocks)
        stocks = [s for s in stocks if s["symbol"] not in done]
        log(f"断点续抓：跳过 {before - len(stocks)} 只已完成，剩余 {len(stocks)} 只")

    stock_map = {s["symbol"]: s for s in stocks}
    total = len(stocks)
    if total == 0:
        log("没有需要抓取的股票，退出。")
        conn.close()
        return 0

    ok = 0
    failed: list[str] = []
    pending_rows: list[tuple] = []
    BATCH_ROWS = 20000
    BATCH_STOCKS = 300
    started = time.time()
    processed = 0

    def worker(item: dict) -> tuple[str, list[tuple], str | None]:
        try:
            return item["symbol"], fetch_kline(item["symbol"], args.datalen), None
        except Exception as exc:
            return item["symbol"], [], str(exc)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for offset in range(0, total, BATCH_STOCKS):
            batch = stocks[offset: offset + BATCH_STOCKS]
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
                    pending_rows.extend(rows)
                    if len(pending_rows) >= BATCH_ROWS:
                        conn.executemany(INSERT_SQL, pending_rows)
                        conn.commit()
                        pending_rows.clear()
                if processed % 100 == 0 or processed == total:
                    elapsed = time.time() - started
                    rate = processed / elapsed if elapsed else 0
                    eta = (total - processed) / rate if rate else 0
                    log(
                        f"进度 {processed}/{total} ({processed / total:.1%}) | 成功 {ok} "
                        f"失败 {len(failed)} | {rate:.1f} 只/秒 | 预计剩余 {eta / 60:.1f} 分钟"
                    )

    if pending_rows:
        conn.executemany(INSERT_SQL, pending_rows)
        conn.commit()

    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_full_fetch',?)", (now,))
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('full_fetch_success',?)", (str(ok),))
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('full_fetch_failed',?)", (str(len(failed)),))
    conn.commit()
    conn.close()

    elapsed = (time.time() - started) / 60
    log(f"抓取完成：成功 {ok} 只，失败 {len(failed)} 只，耗时 {elapsed:.1f} 分钟")
    if failed:
        log("失败代码（前 30 个）：" + ", ".join(failed[:30]))
        with open(args.db + ".failed.txt", "w", encoding="utf-8") as fh:
            fh.write("\n".join(failed))
        log(f"完整失败列表已写入 {args.db}.failed.txt")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
