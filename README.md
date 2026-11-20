# A股通用回测框架

一套**引擎与策略完全解耦**的 A 股量化回测框架：换策略只需一行 `--strategy` 参数。基于近 20 年全 A 股日线数据，内置低价股等多个示例策略。

📊 **[在线演示页（GitHub Pages）](https://alexzhm2010.github.io/a-share-backtest/)** — 净值曲线、绩效指标、策略说明（把 `alexzhm2010` 替换为你的 GitHub 用户名）

---

## 特性

- **插件化架构**：只实现 `on_rebalance` 一个方法，任何选股模型都能装载
- **严谨无未来函数**：T 日收盘后选股，T+1 开盘价成交
- **真实交易成本**：佣金万3（双边）+ 印花税千1（卖出）+ 滑点万5（双边）
- **一字板过滤**：一字涨停买不进、一字跌停卖不出
- **后复权计价**：分红送转已还原，避免除权日假暴跌
- **绩效归因**：年化、夏普、最大回撤、牛熊分段收益

## 示例策略回测结果（低价20股，2010–2024）

| 指标 | 数值 |
|---|---|
| 年化收益 | **14.2%** |
| 超额年化（vs 沪深300） | **+13.5%** |
| 最大回撤 | -53.4% |
| 夏普比率 | 0.52 |
| 期末净值 | 7.31 倍 |

> 结论：低价股效应在 A 股有效，但**高波动、强牛市依赖**（2015 牛市 +224%，2017-2018 熊市 -10%）。详见[在线演示页](https://alexzhm2010.github.io/a-share-backtest/)。**仅供研究，不构成投资建议。**

## 目录结构

```
a-share-backtest/
├── backtest/               # 引擎核心
│   ├── data.py             # 数据加载 + 脏数据清洗
│   ├── strategy.py         # 策略基类 BaseStrategy / 上下文 Context
│   ├── engine.py           # 调仓 · 成交 · 成本 · 净值
│   └── metrics.py          # 绩效指标 · 牛熊分段
├── strategies/             # 策略插件目录（可自由扩展）
│   ├── low_price.py        # 低价N股
│   ├── low_price_low_vol.py# 低价+低波双因子
│   └── low_pb.py           # 低PB价值
├── docs/index.html         # GitHub Pages 静态首页
├── examples/               # 示例回测输出（净值/成交CSV）
├── run_backtest.py         # 命令行入口
├── fetch_all.py            # 全量抓取全A股日线
├── update_daily.py         # 增量更新
├── add_*.py                # 补数脚本（复权/估值/退市/基准/快照）
└── make_site.py            # 生成在线演示页
```

## 快速开始

### 1. 安装依赖

```bash
pip install pandas numpy requests beautifulsoup4
```

### 2. 准备数据

> ⚠️ 数据库 `a_share_daily.db`（约 2.7GB）**不在仓库中**（超出 GitHub 单文件 100MB 限制）。请用随附脚本自行生成：

```bash
python3 fetch_all.py              # 抓取全A股近20年日线 → a_share_daily.db
python3 add_adjusted_prices.py    # 后复权因子 + 后复权价（回测必需）
python3 add_benchmarks.py         # 基准指数（沪深300等）
# 可选增强：
python3 add_snapshot.py           # 当前PE/PB/市值/ST快照
python3 add_valuation.py          # 历史每日估值（2018至今，东财源）
python3 add_delisted.py           # 扫描补齐退市股，消除幸存者偏差
python3 update_daily.py           # 日常增量更新（可配crontab）
```

数据源为新浪财经与东方财富公开接口；受网络环境影响，部分接口在境外服务器可能不通。

### 3. 运行回测

```bash
# 低价20股，月度调仓
python3 run_backtest.py \
  --strategy strategies.low_price:LowPriceStrategy \
  --start 2010-01-01 --end 2024-12-31 \
  --freq monthly --n 20 \
  --save-equity equity.csv --save-trades trades.csv

# 换策略：只改 --strategy
python3 run_backtest.py --strategy strategies.low_pb:LowPbStrategy --freq monthly
```

常用参数：`--freq`(daily/weekly/monthly)、`--capital`(初始资金)、`--commission`、`--stamp`、`--slippage`、`--benchmark`、`--n`。

### 4. 编写自己的策略

在 `strategies/` 新建 `.py`，继承 `BaseStrategy` 并实现 `on_rebalance`（返回目标持仓代码列表）：

```python
from backtest.strategy import BaseStrategy, Context

class MyStrategy(BaseStrategy):
    name = "我的策略"

    def __init__(self, n=20, **params):
        super().__init__(n=n, **params)
        self.n = n

    def on_rebalance(self, ctx: Context) -> list[str]:
        prices = ctx.raw_prices_on()          # 当日不复权名义价横截面
        return prices.nsmallest(self.n).index.tolist()   # 选最低价N股
```

Context 还提供：`ctx.prices_on()`(后复权价)、`ctx.history_close(sym, days)`(历史价)、`ctx.valuation_on("pb_mrq")`(当日估值横截面)、`ctx.data.snapshots`(PE/PB/市值/ST)。

引擎与策略完全解耦：`on_rebalance` 只负责"选哪些股"，买卖时点、成本、净值、风控由引擎处理。

## 数据库结构（脚本生成后）

| 表 | 内容 |
|---|---|
| `stocks` | 股票列表（代码、名称、交易所） |
| `daily_prices` | 日线 OHLCV + 后复权因子/后复权价（约 1525 万行） |
| `daily_valuation` | 历史每日 PE/PB/PS/市值/股本（2018 至今） |
| `benchmarks` | 8 个基准指数日线 |
| `snapshots` | 当前 PE/PB/市值/换手率/ST 快照 |

## 更新在线演示页

```bash
python3 make_site.py    # 读取 examples/equity_lowprice20.csv 重新生成 docs/index.html
```

## 已知局限

1. 估值数据起点 2018 年（东财历史估值最早到 2018-01-02）
2. 历史每日换手率无直接源（可用 成交量/流通股本 近似）
3. ST 标记为当前快照，用作历史过滤有轻微前视偏差
4. 退市股扫描基于代码段探测，极少数非常规代码可能遗漏
5. 一字板判断简化（仅用开=高=低识别）

## License

MIT — 仅供量化研究学习，不构成任何投资建议。数据来自公开财经接口，使用前请核实数据准确性。
