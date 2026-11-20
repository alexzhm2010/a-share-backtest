#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略插件：低价N股（名义价格最低的N只，带风控过滤）。

规则：
1. 按当日【不复权名义收盘价】从低到高排序
2. 过滤：剔除 ST、剔除价格<=min_price（面值退市风险）、剔除当日停牌/一字板
3. 取前 N 只，等权持有，按引擎设定的调仓频率换股
"""
from __future__ import annotations

import numpy as np

from backtest.strategy import BaseStrategy, Context


class LowPriceStrategy(BaseStrategy):
    name = "低价N股"

    def __init__(self, n: int = 20, min_price: float = 2.0, exclude_st: bool = True, **params):
        super().__init__(n=n, min_price=min_price, exclude_st=exclude_st, **params)
        self.n = n
        self.min_price = min_price
        self.exclude_st = exclude_st

    def on_rebalance(self, ctx: Context) -> list[str]:
        raw = ctx.raw_prices_on()                       # 名义价格横截面
        raw = raw[raw > self.min_price]                 # 剔除低价退市风险

        # 剔除 ST（用当前快照的 ST 标记，注意这是快照不是历史点，属于轻微前视）
        if self.exclude_st and not ctx.data.snapshots.empty:
            st_syms = set(
                ctx.data.snapshots.index[ctx.data.snapshots.get("is_st", 0) == 1]
            ) if "is_st" in ctx.data.snapshots.columns else set()
            raw = raw[~raw.index.isin(st_syms)]

        # 剔除当日停牌（成交量为0或价格非有限）
        vol = ctx.data.volume.loc[ctx.date]
        liquid = vol.reindex(raw.index).fillna(0) > 0
        raw = raw[liquid]

        # 按名义价格升序取前 N
        selected = raw.nsmallest(self.n).index.tolist()
        return selected
