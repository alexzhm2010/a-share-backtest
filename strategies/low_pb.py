#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略插件示例3：低估值选股（历史每日 PB/PE 数据驱动，演示估值接口）。

规则：在当日 PB_MRQ 最低的前 n 只股票中等权持有，剔除 ST、剔除 PB<=0。
数据来自东财历史每日估值（2018 年至今），严格按调仓日取值，无未来函数。
"""
from __future__ import annotations

import pandas as pd

from backtest.strategy import BaseStrategy, Context


class LowPBStrategy(BaseStrategy):
    name = "低PB价值策略"

    def __init__(self, n: int = 20, exclude_st: bool = True, **params):
        super().__init__(n=n, exclude_st=exclude_st, **params)
        self.n = n
        self.exclude_st = exclude_st

    def on_rebalance(self, ctx: Context) -> list[str]:
        pb = ctx.valuation_on("pb_mrq")          # 带 symbol 索引的估值横截面
        pb = pb[pb > 0]                          # 剔除负PB
        if pb.empty:
            return []                            # 2018年前无估值数据

        if self.exclude_st and not ctx.data.snapshots.empty and "is_st" in ctx.data.snapshots.columns:
            st_syms = set(ctx.data.snapshots.index[ctx.data.snapshots["is_st"] == 1])
            pb = pb[~pb.index.isin(st_syms)]

        # 只保留当日有行情的股票（能交易）
        prices = ctx.prices_on()
        pb = pb[pb.index.isin(prices.index)]

        return pb.nsmallest(self.n).index.tolist()
