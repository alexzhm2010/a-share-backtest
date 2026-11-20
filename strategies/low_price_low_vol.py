#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略插件示例2：低价+低波动双因子（演示框架可装载多个不同模型）。

规则：在名义价格最低的前 pool 只股票中，再按过去20日收益率波动率
从低到高排序，取前 n 只等权持有。相比纯低价，降低高波动"妖股"权重。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.strategy import BaseStrategy, Context


class LowPriceLowVolStrategy(BaseStrategy):
    name = "低价低波双因子"

    def __init__(self, n: int = 20, pool: int = 60, min_price: float = 2.0,
                 lookback: int = 20, exclude_st: bool = True, **params):
        super().__init__(n=n, pool=pool, min_price=min_price, lookback=lookback,
                         exclude_st=exclude_st, **params)
        self.n = n
        self.pool = pool
        self.min_price = min_price
        self.lookback = lookback
        self.exclude_st = exclude_st

    def on_rebalance(self, ctx: Context) -> list[str]:
        raw = ctx.raw_prices_on()
        raw = raw[raw > self.min_price]

        if self.exclude_st and not ctx.data.snapshots.empty and "is_st" in ctx.data.snapshots.columns:
            st_syms = set(ctx.data.snapshots.index[ctx.data.snapshots["is_st"] == 1])
            raw = raw[~raw.index.isin(st_syms)]

        vol = ctx.data.volume.loc[ctx.date]
        raw = raw[vol.reindex(raw.index).fillna(0) > 0]

        # 第一层：名义价格最低的 pool 只
        pool_syms = raw.nsmallest(self.pool).index.tolist()

        # 第二层：按过去 lookback 日波动率从低到高
        vol_rank = {}
        for sym in pool_syms:
            h = ctx.history_close(sym, self.lookback + 1)
            if len(h) < self.lookback // 2:
                continue
            ret = h.pct_change().dropna()
            if ret.empty:
                continue
            vol_rank[sym] = ret.std()
        if not vol_rank:
            return pool_syms[:self.n]
        s = pd.Series(vol_rank).sort_values()
        return s.index[:self.n].tolist()
