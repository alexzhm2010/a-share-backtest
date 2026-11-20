#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测引擎：事件驱动，T日收盘选股、T+1日开盘成交，严格避免未来函数。"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .data import MarketData, align_benchmark
from .strategy import BaseStrategy, Context


@dataclass
class BacktestResult:
    equity: pd.Series            # 组合净值（每日）
    returns: pd.Series           # 每日收益率
    benchmark: pd.Series         # 基准净值（每日）
    trades: pd.DataFrame         # 成交记录
    rebalance_dates: list[str]
    final_value: float
    params: dict = field(default_factory=dict)


class Engine:
    """
    朴实严谨的日频回测引擎。

    规则：
    - 调仓日由 freq 决定（daily/weekly/monthly），在调仓日【收盘后】调用策略选股
    - 次日【开盘】以开盘价成交，避免用到当日收盘信息
    - 等权分配资金；涨停（开盘=最高=涨停价近似）买不进时跳过
    - 成本：佣金 commission（双边）、印花税 stamp（卖出）、滑点 slippage（双边）
    """

    def __init__(
        self,
        data: MarketData,
        initial_capital: float = 1_000_000.0,
        commission: float = 0.0003,
        stamp_duty: float = 0.001,
        slippage: float = 0.0005,
        freq: str = "monthly",
        benchmark_name: str = "沪深300",
        max_holdings: int | None = None,
    ):
        self.data = data
        self.initial_capital = initial_capital
        self.commission = commission
        self.stamp_duty = stamp_duty
        self.slippage = slippage
        self.freq = freq
        self.benchmark_name = benchmark_name
        self.max_holdings = max_holdings

    def _rebalance_days(self) -> list[int]:
        """返回调仓日在 trade_dates 中的索引（信号日，实际次日成交）。"""
        dates = pd.to_datetime(self.data.trade_dates)
        s = pd.Series(range(len(dates)), index=dates)
        if self.freq == "daily":
            idxs = list(range(len(dates)))
        elif self.freq == "weekly":
            groups = s.groupby([dates.isocalendar().year, dates.isocalendar().week])
            idxs = [g.iloc[-1] for g in groups.groups.values()]
            idxs = sorted(int(s.iloc[g]) for g in idxs) if False else sorted(
                s.groupby([dates.isocalendar().year, dates.isocalendar().week]).max().astype(int).tolist()
            )
        elif self.freq == "monthly":
            idxs = s.groupby([dates.year, dates.month]).max().astype(int).tolist()
        else:
            raise ValueError(f"未知调仓频率: {self.freq}")
        # 最后一个交易日不能作为信号日（无次日成交），去掉
        return [i for i in sorted(idxs) if i < len(dates) - 1]

    def run(self, strategy: BaseStrategy) -> BacktestResult:
        dates = self.data.trade_dates
        close = self.data.close
        opens = self.data.open
        volumes = self.data.volume

        cash = self.initial_capital
        holdings: dict[str, int] = {}       # symbol -> 股数
        pending_target: list[str] | None = None

        equity_rows: list[float] = []
        trade_rows: list[dict] = []
        rebalance_dates: list[str] = []

        reb_days = set(self._rebalance_days())

        for i, date in enumerate(dates):
            # ---- 1. 执行昨日收盘产生的目标（今日开盘成交）----
            if pending_target is not None:
                target = set(pending_target)
                if self.max_holdings:
                    target = set(list(target)[: self.max_holdings])

                # 卖出：持有但不在目标
                for sym in list(holdings.keys()):
                    if sym in target:
                        continue
                    px = opens.at[date, sym] if sym in opens.columns else np.nan
                    if not np.isfinite(px) or px <= 0:
                        px = close.at[date, sym]
                    if not np.isfinite(px) or px <= 0:
                        continue  # 停牌无法成交，继续持有
                    # 一字跌停卖不出：开=高=低则顺延持有
                    hi = self.data.high.at[date, sym] if sym in self.data.high.columns else np.nan
                    lo = self.data.low.at[date, sym] if sym in self.data.low.columns else np.nan
                    if np.isfinite(hi) and np.isfinite(lo) and abs(hi - lo) < 1e-9:
                        continue
                    shares = holdings.pop(sym)
                    proceeds = shares * px * (1 - self.slippage) * (1 - self.commission - self.stamp_duty)
                    cash += proceeds
                    trade_rows.append({"date": date, "symbol": sym, "side": "SELL",
                                       "shares": shares, "price": px, "value": proceeds})

                # 买入：在目标但未持有
                to_buy = [s for s in target if s not in holdings]
                if to_buy:
                    # 用今日收盘净值近似确定每只预算（此处用开盘前总资产）
                    total = cash + sum(
                        sh * (opens.at[date, s] if s in opens.columns and np.isfinite(opens.at[date, s]) else close.at[date, s])
                        for s, sh in holdings.items()
                        if s in close.columns and np.isfinite(close.at[date, s])
                    )
                    budget = total / len(target)
                    for sym in to_buy:
                        px = opens.at[date, sym] if sym in opens.columns else np.nan
                        if not np.isfinite(px) or px <= 0:
                            px = close.at[date, sym]
                        if not np.isfinite(px) or px <= 0:
                            continue  # 停牌
                        # 一字涨停买不进：开=高=低则跳过
                        hi = self.data.high.at[date, sym] if sym in self.data.high.columns else np.nan
                        lo = self.data.low.at[date, sym] if sym in self.data.low.columns else np.nan
                        if np.isfinite(hi) and np.isfinite(lo) and abs(hi - lo) < 1e-9:
                            continue
                        cost_per = px * (1 + self.slippage) * (1 + self.commission)
                        shares = int(budget / cost_per / 100) * 100
                        if shares <= 0 or shares * cost_per > cash:
                            shares = int(cash / cost_per / 100) * 100
                        if shares <= 0:
                            continue
                        spend = shares * cost_per
                        cash -= spend
                        holdings[sym] = holdings.get(sym, 0) + shares
                        trade_rows.append({"date": date, "symbol": sym, "side": "BUY",
                                           "shares": shares, "price": px, "value": spend})
                pending_target = None

            # ---- 2. 收盘后计算净值 ----
            mv = 0.0
            for sym, sh in holdings.items():
                px = close.at[date, sym] if sym in close.columns else np.nan
                if np.isfinite(px):
                    mv += sh * px
            equity_rows.append(cash + mv)

            # ---- 3. 调仓日收盘后调用策略产生明日目标 ----
            if i in reb_days:
                ctx = Context(
                    date=date, data=self.data, trade_dates=dates,
                    current_idx=i, holdings=list(holdings.keys()),
                    cash=cash, portfolio_value=cash + mv,
                )
                target = strategy.on_rebalance(ctx)
                pending_target = list(dict.fromkeys(target))  # 去重保序
                rebalance_dates.append(date)

        equity = pd.Series(equity_rows, index=pd.Index(dates, name="trade_date"), name="equity")
        returns = equity.pct_change().fillna(0.0)

        bench_bt = align_benchmark(self.data.benchmarks, dates, self.benchmark_name)
        if bench_bt.notna().any():
            bench_norm = bench_bt / bench_bt.dropna().iloc[0]
        else:
            bench_norm = pd.Series(1.0, index=dates)

        return BacktestResult(
            equity=equity, returns=returns, benchmark=bench_norm,
            trades=pd.DataFrame(trade_rows), rebalance_dates=rebalance_dates,
            final_value=equity.iloc[-1],
            params={"strategy": repr(strategy), "freq": self.freq,
                    "commission": self.commission, "stamp_duty": self.stamp_duty,
                    "slippage": self.slippage, "initial_capital": self.initial_capital},
        )
