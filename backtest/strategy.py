#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略基类：所有回测模型继承此类，实现 on_rebalance 即可被引擎装载。"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Context:
    """调仓时传给策略的上下文，提供截至当前交易日的数据视图（含当日，严格无未来函数）。"""
    date: str
    data: object                      # MarketData
    trade_dates: list[str]
    current_idx: int
    holdings: list[str]
    cash: float
    portfolio_value: float

    def history_close(self, symbol: str, days: int) -> pd.Series:
        """某股票截至当前日的最近 days 天后复权收盘价。"""
        s = self.data.close[symbol]
        return s.iloc[max(0, self.current_idx - days + 1): self.current_idx + 1].dropna()

    def prices_on(self, date: str | None = None) -> pd.Series:
        """指定日（默认当前日）的后复权收盘价横截面。"""
        d = date or self.date
        return self.data.close.loc[d].dropna()

    def raw_prices_on(self, date: str | None = None) -> pd.Series:
        """指定日的不复权收盘价横截面（名义价格，用于低价选股）。"""
        d = date or self.date
        return self.data.raw_close.loc[d].dropna()

    def valuation_on(self, field: str, date: str | None = None) -> pd.Series:
        """
        指定日的估值横截面（历史每日，索引为带前缀的 symbol 如 sh600000）。
        field 支持: pe_ttm, pe_lar, pb_mrq, ps_ttm, total_market_cap,
                    float_market_cap, total_shares, float_shares
        数据覆盖 2018 年至今；更早日期返回空 Series。
        """
        d = date or self.date
        val = getattr(self.data, "valuation", None) or {}
        if field not in val:
            return pd.Series(dtype=float)
        panel = val[field]
        if d not in panel.index:
            # 找不晚于 d 的最近交易日
            prior = panel.index[panel.index <= d]
            if len(prior) == 0:
                return pd.Series(dtype=float)
            d = prior[-1]
        s = panel.loc[d].dropna()
        s.index = [self._to_symbol(c) for c in s.index]
        return s

    @staticmethod
    def _to_symbol(code: str) -> str:
        """纯数字代码转带前缀 symbol。"""
        code = str(code).zfill(6)
        if code.startswith(("60", "68", "90")):
            return "sh" + code
        return "sz" + code


class BaseStrategy:
    """策略插件基类。子类需设置 name，并实现 on_rebalance。"""

    name = "BaseStrategy"

    def __init__(self, **params):
        self.params = params

    def on_rebalance(self, ctx: Context) -> list[str]:
        """
        在每个调仓日被调用，返回目标持仓股票代码列表（等权买入）。
        只能使用 ctx 提供的截至当日的数据，不得访问未来数据。
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{self.name}({self.params})"
