#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""绩效指标计算：收益、风险、回撤、超额、牛熊分段。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import BacktestResult

TRADING_DAYS = 244


def compute_metrics(result: BacktestResult, rf: float = 0.02) -> dict:
    eq = result.equity
    ret = result.returns
    n = len(ret)
    years = n / TRADING_DAYS

    total_return = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 else 0

    vol = ret.std() * np.sqrt(TRADING_DAYS)
    sharpe = (ret.mean() * TRADING_DAYS - rf) / vol if vol > 0 else 0

    # 最大回撤
    cummax = eq.cummax()
    drawdown = eq / cummax - 1
    max_dd = drawdown.min()
    max_dd_end = drawdown.idxmin()
    max_dd_start = eq.loc[:max_dd_end].idxmax()

    downside = ret[ret < 0].std() * np.sqrt(TRADING_DAYS)
    sortino = (ret.mean() * TRADING_DAYS - rf) / downside if downside > 0 else 0

    # 基准与超额
    bench_ret = result.benchmark.pct_change().fillna(0.0)
    bench_cagr = (result.benchmark.iloc[-1] / result.benchmark.iloc[0]) ** (1 / years) - 1 if years > 0 else 0
    excess = ret - bench_ret
    info_ratio = excess.mean() * TRADING_DAYS / (excess.std() * np.sqrt(TRADING_DAYS)) if excess.std() > 0 else 0

    # 月度胜率
    eq_dt = eq.copy()
    eq_dt.index = pd.to_datetime(eq_dt.index)
    monthly = eq_dt.resample("ME").last().pct_change().dropna()
    win_rate = (monthly > 0).mean()

    return {
        "回测区间": f"{eq.index[0]} ~ {eq.index[-1]}",
        "交易日数": n,
        "期初资金": f"{eq.iloc[0]:,.0f}",
        "期末市值": f"{eq.iloc[-1]:,.0f}",
        "累计收益": f"{total_return:.2%}",
        "年化收益": f"{cagr:.2%}",
        "年化波动": f"{vol:.2%}",
        "夏普比率": f"{sharpe:.2f}",
        "索提诺比率": f"{sortino:.2f}",
        "最大回撤": f"{max_dd:.2%}",
        "最大回撤区间": f"{max_dd_start} ~ {max_dd_end}",
        "基准年化": f"{bench_cagr:.2%}",
        "超额年化": f"{cagr - bench_cagr:.2%}",
        "信息比率": f"{info_ratio:.2f}",
        "月度胜率": f"{win_rate:.2%}",
        "调仓次数": len(result.rebalance_dates),
        "成交笔数": len(result.trades),
    }


def regime_metrics(result: BacktestResult, regimes: dict[str, tuple[str, str]]) -> dict[str, dict]:
    """按牛熊分段统计。regimes: {名称: (起始日, 结束日)}"""
    out = {}
    eq = result.equity
    for name, (start, end) in regimes.items():
        seg = eq.loc[(eq.index >= start) & (eq.index <= end)]
        if len(seg) < 2:
            continue
        years = len(seg) / TRADING_DAYS
        cagr = (seg.iloc[-1] / seg.iloc[0]) ** (1 / years) - 1 if years > 0 else 0
        dd = (seg / seg.cummax() - 1).min()
        out[name] = {
            "区间": f"{seg.index[0]} ~ {seg.index[-1]}",
            "区间收益": f"{seg.iloc[-1]/seg.iloc[0]-1:.2%}",
            "年化": f"{cagr:.2%}",
            "最大回撤": f"{dd:.2%}",
        }
    return out


def print_report(metrics: dict, regimes: dict | None = None) -> None:
    print("\n" + "=" * 50)
    print("            回 测 绩 效 报 告")
    print("=" * 50)
    for k, v in metrics.items():
        print(f"  {k:<12} {v}")
    if regimes:
        print("-" * 50)
        print("  牛熊分段：")
        for name, m in regimes.items():
            print(f"  [{name}] {m['区间']} 收益{m['区间收益']} 年化{m['年化']} 回撤{m['最大回撤']}")
    print("=" * 50)
