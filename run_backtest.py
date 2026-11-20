#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测运行入口：通过命令行动态装载任意策略插件。

用法示例：
    # 低价20股，月度调仓，2010-2024
    python3 run_backtest.py --strategy strategies.low_price:LowPriceStrategy \
        --start 2010-01-01 --end 2024-12-31 --freq monthly --n 20

    # 换成另一个策略（只要继承 BaseStrategy 并实现 on_rebalance）
    python3 run_backtest.py --strategy strategies.my_strategy:MyStrategy --freq weekly
"""
from __future__ import annotations

import argparse
import importlib

import pandas as pd

from backtest.data import load_market_data
from backtest.engine import Engine
from backtest.metrics import compute_metrics, regime_metrics, print_report


BULL_BEAR = {
    "2007牛市": ("2006-01-01", "2007-10-31"),
    "2008熊市": ("2008-01-01", "2008-10-31"),
    "2009反弹": ("2009-01-01", "2009-12-31"),
    "2015牛市": ("2014-07-01", "2015-06-12"),
    "2015熊市": ("2015-06-13", "2016-01-28"),
    "2017-2018": ("2017-01-01", "2018-12-31"),
    "2019-2021": ("2019-01-01", "2021-12-31"),
    "2022-2024": ("2022-01-01", "2024-12-31"),
}


def load_strategy(spec: str, params: dict):
    """spec 格式：模块路径:类名，如 strategies.low_price:LowPriceStrategy"""
    if ":" not in spec:
        raise ValueError("策略格式应为 '模块路径:类名'，如 strategies.low_price:LowPriceStrategy")
    mod_path, cls_name = spec.split(":", 1)
    mod = importlib.import_module(mod_path)
    cls = getattr(mod, cls_name)
    return cls(**params)


def main() -> int:
    ap = argparse.ArgumentParser(description="通用A股回测框架")
    ap.add_argument("--strategy", required=True, help="策略：模块路径:类名")
    ap.add_argument("--db", default="/workspace/a_share_daily.db")
    ap.add_argument("--start", default=None, help="回测起始日 YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="回测结束日 YYYY-MM-DD")
    ap.add_argument("--freq", default="monthly", choices=["daily", "weekly", "monthly"])
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--commission", type=float, default=0.0003)
    ap.add_argument("--stamp", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--benchmark", default="沪深300")
    ap.add_argument("--max-holdings", type=int, default=None)
    ap.add_argument("--n", type=int, default=None, help="策略参数示例：持仓只数")
    ap.add_argument("--min-price", type=float, default=None, help="策略参数示例：最低价过滤")
    ap.add_argument("--save-equity", default=None, help="净值曲线保存路径 CSV")
    ap.add_argument("--save-trades", default=None, help="成交记录保存路径 CSV")
    args = ap.parse_args()

    # 组装策略参数（通用常用参数）
    strategy_params = {}
    if args.n is not None:
        strategy_params["n"] = args.n
    if args.min_price is not None:
        strategy_params["min_price"] = args.min_price

    print(f"[1/4] 加载数据 {args.start or '最早'} ~ {args.end or '最新'} ...")
    data = load_market_data(args.db, start=args.start, end=args.end)
    print(f"      交易日 {len(data.trade_dates)} 天，股票 {data.close.shape[1]} 只")

    print(f"[2/4] 装载策略 {args.strategy} ...")
    strategy = load_strategy(args.strategy, strategy_params)
    print(f"      策略：{strategy}")

    print(f"[3/4] 运行回测（调仓频率 {args.freq}）...")
    engine = Engine(
        data=data, initial_capital=args.capital,
        commission=args.commission, stamp_duty=args.stamp,
        slippage=args.slippage, freq=args.freq,
        benchmark_name=args.benchmark, max_holdings=args.max_holdings,
    )
    result = engine.run(strategy)

    print("[4/4] 计算绩效指标 ...")
    metrics = compute_metrics(result)
    regimes = regime_metrics(result, BULL_BEAR)
    print_report(metrics, regimes)

    if args.save_equity:
        df = pd.DataFrame({"equity": result.equity, "benchmark": result.benchmark})
        df.to_csv(args.save_equity)
        print(f"净值曲线已保存: {args.save_equity}")
    if args.save_trades:
        result.trades.to_csv(args.save_trades, index=False)
        print(f"成交记录已保存: {args.save_trades}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
