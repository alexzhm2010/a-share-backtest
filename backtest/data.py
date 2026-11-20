#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据加载与预处理：从 SQLite 分批读取后复权行情，构建回测面板。

一次性 read_sql 1500万行在本环境不稳定，改为按年分块读取再 concat/pivot。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

DEFAULT_DB = "/workspace/a_share_daily.db"


@dataclass
class MarketData:
    close: pd.DataFrame       # 后复权收盘价
    open: pd.DataFrame        # 后复权开盘价
    high: pd.DataFrame        # 后复权最高价
    low: pd.DataFrame         # 后复权最低价
    volume: pd.DataFrame      # 原始成交量
    raw_close: pd.DataFrame   # 不复权收盘价（名义价格）
    trade_dates: list[str]
    snapshots: pd.DataFrame
    benchmarks: pd.DataFrame
    valuation: dict[str, pd.DataFrame] | None = None  # 历史每日估值（按字段分表，index=交易日,columns=code）

    def symbols_on(self, date: str) -> list[str]:
        row = self.close.loc[date]
        return list(row[row.notna()].index)


def load_market_data(db_path: str = DEFAULT_DB,
                     start: str | None = None,
                     end: str | None = None) -> MarketData:
    conn = sqlite3.connect(db_path)

    y0 = int((start or "1996-01-01")[:4])
    y1 = int((end or "2026-12-31")[:4])

    chunks = []
    for year in range(y0, y1 + 1):
        lo = max(f"{year}-01-01", start or "0000-00-00")
        hi = min(f"{year}-12-31", end or "9999-99-99")
        if lo > hi:
            continue
        q = """
            SELECT trade_date, symbol, adj_close, close, open, high, low, volume, adj_factor
            FROM daily_prices
            WHERE adj_close IS NOT NULL AND adj_factor IS NOT NULL
              AND trade_date >= ? AND trade_date <= ?
        """
        c = pd.read_sql(q, conn, params=[lo, hi])
        if not c.empty:
            chunks.append(c)
    df = pd.concat(chunks, ignore_index=True)

    f = df["adj_factor"].to_numpy()
    df["adj_open"] = df["open"].to_numpy() * f
    df["adj_high"] = df["high"].to_numpy() * f
    df["adj_low"] = df["low"].to_numpy() * f

    def pivot(col: str) -> pd.DataFrame:
        return df.pivot(index="trade_date", columns="symbol", values=col).sort_index()

    close = pivot("adj_close")
    opens = pivot("adj_open")
    highs = pivot("adj_high")
    lows = pivot("adj_low")
    raw_close = pivot("close")
    volume = pivot("volume")

    # 清洗：单日涨跌超 ±60% 的复权价脏点（多为北交所早期脏数据）用前值填充
    pct = close.pct_change()
    bad = ((pct > 0.6) | (pct < -0.5)).fillna(False)
    if bad.any().any():
        prev = close.shift(1)
        close = close.where(~bad, prev)
        opens = opens.where(~bad, prev)
        highs = highs.where(~bad, prev)
        lows = lows.where(~bad, prev)

    try:
        snapshots = pd.read_sql("SELECT * FROM snapshots", conn).set_index("symbol")
    except Exception:
        snapshots = pd.DataFrame()
    try:
        bench = pd.read_sql(
            "SELECT trade_date, name, close FROM benchmarks", conn
        ).pivot(index="trade_date", columns="name", values="close").sort_index()
    except Exception:
        bench = pd.DataFrame()

    # 历史每日估值（2018至今，东财）：按字段分表加载为面板
    valuation: dict[str, pd.DataFrame] = {}
    try:
        val_cols = {
            "pe_ttm": "PE_TTM", "pe_lar": "PE_LAR", "pb_mrq": "PB_MRQ",
            "ps_ttm": "PS_TTM", "total_market_cap": "TOTAL_MARKET_CAP",
            "float_market_cap": "FLOAT_MARKET_CAP", "total_shares": "TOTAL_SHARES",
            "float_shares": "FLOAT_SHARES",
        }
        vdf = pd.read_sql(
            "SELECT code, trade_date, pe_ttm, pe_lar, pb_mrq, ps_ttm, "
            "total_market_cap, float_market_cap, total_shares, float_shares "
            "FROM daily_valuation", conn,
        )
        if not vdf.empty:
            for out_name in val_cols:
                valuation[out_name] = vdf.pivot(
                    index="trade_date", columns="code", values=out_name
                ).sort_index()
    except Exception:
        pass

    conn.close()
    return MarketData(
        close=close, open=opens, high=highs, low=lows,
        volume=volume, raw_close=raw_close,
        trade_dates=list(close.index), snapshots=snapshots, benchmarks=bench,
        valuation=valuation,
    )


def align_benchmark(bench: pd.DataFrame, trade_dates: list[str], name: str) -> pd.Series:
    if name not in bench.columns:
        return pd.Series(index=trade_dates, dtype=float)
    return bench[name].reindex(trade_dates).ffill()
