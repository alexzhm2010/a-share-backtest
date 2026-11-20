#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补齐历史每日估值数据（东方财富数据中心 RPT_VALUEANALYSIS_DET）。

覆盖 2018-01-02 至今的每日：PE_TTM、PE_LAR、PB_MRQ、PS_TTM、PCF、PEG、
总市值、流通市值、总股本、流通股本、收盘价、涨跌幅。

接口：https://datacenter-web.eastmoney.com/api/data/v1/get
reportName=RPT_VALUEANALYSIS_DET，按 SECURITY_CODE 过滤，pageSize=500 分页。
东财按源IP限流，控制并发与重试。

建表 daily_valuation(symbol, code, trade_date, ...)，支持断点续跑。
用法：
    python3 add_valuation.py                 # 全量
    python3 add_valuation.py --limit 100     # 测试
    python3 add_valuation.py --workers 4
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

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
DEFAULT_DB = "/workspace/a_share_daily.db"
PAGE_SIZE = 500
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
            "Referer": "https://data.eastmoney.com/",
        })
        _tls.session = s
    return s


def fetch_one_stock(code: str, retries: int = 3) -> list[tuple]:
    """拉某只股票全部估值历史，返回入库元组列表。"""
    rows: list[tuple] = []
    page = 1
    while True:
        params = {
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": ("SECURITY_CODE,TRADE_DATE,CLOSE_PRICE,CHANGE_RATE,"
                        "TOTAL_MARKET_CAP,NOTLIMITED_MARKETCAP_A,TOTAL_SHARES,"
                        "FREE_SHARES_A,PE_TTM,PE_LAR,PB_MRQ,PS_TTM,"
                        "PCF_OCF_LAR,PCF_OCF_TTM,PEG_CAR"),
            "filter": f'(SECURITY_CODE="{code}")',
            "pageNumber": page,
            "pageSize": PAGE_SIZE,
            "sortColumns": "TRADE_DATE",
            "sortTypes": 1,
            "source": "WEB",
            "client": "WEB",
        }
        data = None
        last_err = None
        for attempt in range(retries):
            try:
                resp = get_session().get(API, params=params, timeout=25)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as exc:
                last_err = exc
                time.sleep(0.6 * (attempt + 1) + random.random() * 0.3)
        if data is None:
            raise RuntimeError(f"{code} page{page} 失败: {last_err}")

        result = data.get("result")
        if not result or not result.get("data"):
            break
        for d in result["data"]:
            def g(k):
                return d.get(k)
            rows.append((
                code, (g("TRADE_DATE") or "")[:10], g("CLOSE_PRICE"), g("CHANGE_RATE"),
                g("TOTAL_MARKET_CAP"), g("NOTLIMITED_MARKETCAP_A"),
                g("TOTAL_SHARES"), g("FREE_SHARES_A"),
                g("PE_TTM"), g("PE_LAR"), g("PB_MRQ"), g("PS_TTM"),
                g("PCF_OCF_LAR"), g("PCF_OCF_TTM"), g("PEG_CAR"),
            ))
        if page >= result.get("pages", 1):
            break
        page += 1
        time.sleep(0.05)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="补齐历史每日估值数据")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_valuation (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            close REAL, change_rate REAL,
            total_market_cap REAL, float_market_cap REAL,
            total_shares REAL, float_shares REAL,
            pe_ttm REAL, pe_lar REAL, pb_mrq REAL, ps_ttm REAL,
            pcf_ocf_lar REAL, pcf_ocf_ttm REAL, peg REAL,
            PRIMARY KEY (code, trade_date)
        )
    """)
    conn.commit()

    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM stocks ORDER BY code")]
    # 跳过已退市股票（东财估值库无其数据，2018年前已退市）
    try:
        delisted_syms = {r[0] for r in conn.execute(
            "SELECT symbol FROM scan_cache WHERE is_delisted=1")}
        delisted_codes = {s[2:] for s in delisted_syms}
        codes = [c for c in codes if c not in delisted_codes]
        log(f"跳过 {len(delisted_codes)} 只已退市股票")
    except Exception:
        pass
    if args.limit > 0:
        codes = codes[: args.limit]
    # 断点续跑
    done = {r[0] for r in conn.execute("SELECT DISTINCT code FROM daily_valuation")}
    todo = [c for c in codes if c not in done]
    log(f"待处理 {len(todo)} 只（总 {len(codes)}，已完成 {len(done)}）")
    if not todo:
        log("全部已处理，退出。")
        conn.close()
        return 0

    ok = 0
    failed: list[str] = []
    started = time.time()

    def worker(code: str) -> tuple[str, list[tuple], str | None]:
        try:
            return code, fetch_one_stock(code), None
        except Exception as exc:
            return code, [], str(exc)

    no_data = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(worker, c) for c in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            code, rows, err = fut.result()
            if err:
                failed.append(code)
            elif not rows:
                no_data += 1   # 该股无估值数据，正常跳过
            else:
                conn.executemany("""
                    INSERT OR REPLACE INTO daily_valuation
                    (code,trade_date,close,change_rate,total_market_cap,float_market_cap,
                     total_shares,float_shares,pe_ttm,pe_lar,pb_mrq,ps_ttm,
                     pcf_ocf_lar,pcf_ocf_ttm,peg)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, rows)
                conn.commit()
                ok += 1
            if i % 200 == 0 or i == len(todo):
                el = time.time() - started
                rate = i / el if el else 0
                eta = (len(todo) - i) / rate if rate else 0
                log(f"进度 {i}/{len(todo)} | 成功 {ok} 无数据 {no_data} 失败 {len(failed)} | "
                    f"{rate:.1f}只/秒 | 预计剩余 {eta/60:.1f}分钟")
                time.sleep(0.3)  # 轻微限速，避免IP被封

    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('valuation_done',?)",
                 (datetime.now().isoformat(timespec="seconds"),))
    conn.commit()
    cnt = conn.execute("SELECT COUNT(*) FROM daily_valuation").fetchone()[0]
    conn.close()
    log(f"估值补全完成：成功 {ok} 只，无数据 {no_data} 只，失败 {len(failed)} 只，共 {cnt:,} 行，"
        f"耗时 {(time.time()-started)/60:.1f} 分钟")
    if failed:
        log("失败代码（前30）：" + ", ".join(failed[:30]))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
