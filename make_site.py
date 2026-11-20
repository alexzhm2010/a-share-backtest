#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 GitHub Pages 静态首页 docs/index.html。

数据来自 examples/equity_lowprice20.csv（低价20股策略回测结果），
页面完全自包含：净值曲线用原生 SVG + JS 绘制，无任何外部 CDN 依赖。

用法：
    python3 make_site.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
CSV = ROOT / "examples" / "equity_lowprice20.csv"
OUT = ROOT / "docs" / "index.html"


def build_data() -> tuple[dict, dict]:
    df = pd.read_csv(CSV, index_col=0)
    df.index = pd.to_datetime(df.index)
    eq = df["equity"] / df["equity"].iloc[0]
    bm = df["benchmark"] / df["benchmark"].iloc[0]

    years = (df.index[-1] - df.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / years) - 1
    bm_cagr = bm.iloc[-1] ** (1 / years) - 1
    mdd = (eq / eq.cummax() - 1).min()
    ret = eq.pct_change().dropna()
    sharpe = ret.mean() / ret.std() * np.sqrt(252)

    def seg(s: str, e: str):
        x = eq.loc[s:e]
        return round((x.iloc[-1] / x.iloc[0] - 1) * 100, 1) if len(x) > 1 else None

    metrics = {
        "cagr": round(cagr * 100, 2),
        "bm_cagr": round(bm_cagr * 100, 2),
        "excess": round((cagr - bm_cagr) * 100, 2),
        "mdd": round(mdd * 100, 2),
        "sharpe": round(sharpe, 2),
        "final": round(eq.iloc[-1], 2),
        "bm_final": round(bm.iloc[-1], 2),
        "years": round(years, 1),
        "start": str(df.index[0].date()),
        "end": str(df.index[-1].date()),
        "segments": [
            {"label": "2015 牛市", "range": "2014-07 ~ 2015-06", "value": seg("2014-07-01", "2015-06-12")},
            {"label": "2015 熊市", "range": "2015-06 ~ 2016-01", "value": seg("2015-06-13", "2016-01-28")},
            {"label": "2017-2018", "range": "震荡下跌", "value": seg("2017-01-01", "2018-12-31")},
            {"label": "2019-2021", "range": "结构牛", "value": seg("2019-01-01", "2021-12-31")},
            {"label": "2022-2024", "range": "震荡市", "value": seg("2022-01-01", "2024-12-31")},
        ],
    }
    s = pd.DataFrame({"eq": eq, "bm": bm}).resample("ME").last().dropna()
    curve = {
        "dates": [str(x.date()) for x in s.index],
        "eq": [round(v, 4) for v in s["eq"]],
        "bm": [round(v, 4) for v in s["bm"]],
    }
    return metrics, curve


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股通用回测框架 · 低价股策略研究</title>
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --panel2:#1c2230; --border:#30363d;
    --text:#e6edf3; --muted:#8b949e; --red:#f85149; --green:#3fb950;
    --blue:#58a6ff; --gold:#d29922; --accent:#bc8cff;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.6}
  .wrap{max-width:1000px;margin:0 auto;padding:0 20px}
  header{padding:56px 0 32px;border-bottom:1px solid var(--border)}
  .tag{display:inline-block;font-size:12px;color:var(--accent);border:1px solid var(--accent);border-radius:20px;padding:3px 12px;margin-bottom:18px}
  h1{font-size:34px;font-weight:700;letter-spacing:-.5px;margin-bottom:12px}
  h1 span{background:linear-gradient(90deg,var(--red),var(--gold));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
  .sub{color:var(--muted);font-size:16px;max-width:680px}
  .sub b{color:var(--text);font-weight:600}
  section{padding:36px 0;border-bottom:1px solid var(--border)}
  h2{font-size:22px;margin-bottom:6px;display:flex;align-items:center;gap:10px}
  h2::before{content:"";width:4px;height:20px;background:var(--red);border-radius:2px}
  .hint{color:var(--muted);font-size:13px;margin-bottom:22px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-top:24px}
  .card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:18px}
  .card .k{color:var(--muted);font-size:12px;margin-bottom:8px}
  .card .v{font-size:26px;font-weight:700}
  .up{color:var(--red)} .down{color:var(--green)} .neutral{color:var(--blue)}
  .chartbox{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:20px;position:relative}
  svg{width:100%;height:auto;display:block;touch-action:none}
  .legend{display:flex;gap:24px;margin-bottom:8px;font-size:13px;color:var(--muted)}
  .legend i{display:inline-block;width:18px;height:3px;border-radius:2px;vertical-align:middle;margin-right:7px}
  table{width:100%;border-collapse:collapse;margin-top:8px;font-size:14px}
  th,td{text-align:left;padding:11px 14px;border-bottom:1px solid var(--border)}
  th{color:var(--muted);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.5px}
  td:last-child,th:last-child{text-align:right}
  pre{background:#0a0d12;border:1px solid var(--border);border-radius:10px;padding:18px;overflow-x:auto;font-size:13px;color:#c9d1d9;margin-top:12px}
  code{font-family:"SFMono-Regular",Consolas,monospace}
  .kw{color:var(--accent)} .cm{color:#6e7681} .st{color:var(--green)}
  .tree{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:20px;font-family:"SFMono-Regular",Consolas,monospace;font-size:13px;color:var(--muted);line-height:1.9}
  .tree b{color:var(--text);font-weight:500}
  .note{background:var(--panel2);border-left:3px solid var(--gold);border-radius:6px;padding:16px 18px;color:#c9d1d9;font-size:14px;margin-top:8px}
  ul{margin:8px 0 0 20px;color:#c9d1d9;font-size:14px} li{margin:7px 0}
  footer{padding:32px 0 60px;color:var(--muted);font-size:13px;text-align:center}
  .tip{position:absolute;pointer-events:none;background:#010409;border:1px solid var(--border);border-radius:8px;padding:9px 12px;font-size:12px;color:var(--text);opacity:0;transition:opacity .1s;white-space:nowrap;z-index:10;box-shadow:0 8px 24px #0008}
  .tip .d{color:var(--muted);margin-bottom:4px}
  @media(max-width:600px){h1{font-size:26px} .card .v{font-size:22px}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="tag">QUANT RESEARCH · OPEN SOURCE</div>
  <h1>A股通用<span>回测框架</span></h1>
  <p class="sub">一套<b>引擎与策略解耦</b>的量化回测框架：换策略只需一行参数。内置低价股等示例策略，基于 <b>5579 只 A 股 · 近 20 年 · 后复权数据</b>。本页展示「低价20股」策略的回测绩效。</p>
  <div class="cards">
    <div class="card"><div class="k">年化收益</div><div class="v up">__CAGR__%</div></div>
    <div class="card"><div class="k">超额年化 (vs 沪深300)</div><div class="v up">+__EXCESS__%</div></div>
    <div class="card"><div class="k">最大回撤</div><div class="v down">__MDD__%</div></div>
    <div class="card"><div class="k">夏普比率</div><div class="v neutral">__SHARPE__</div></div>
    <div class="card"><div class="k">期末净值</div><div class="v up">__FINAL__x</div></div>
  </div>
</header>

<section>
  <h2>净值曲线</h2>
  <p class="hint">__START__ 至 __END__（__YEARS__ 年）· 对数坐标 · 鼠标悬停查看每月末净值</p>
  <div class="chartbox" id="chartbox">
    <div class="legend">
      <span><i style="background:var(--red)"></i>低价20股策略</span>
      <span><i style="background:var(--blue)"></i>沪深300</span>
    </div>
    <svg id="chart" viewBox="0 0 1000 440" preserveAspectRatio="xMidYMid meet"></svg>
    <div class="tip" id="tip"></div>
  </div>
</section>

<section>
  <h2>牛熊分段表现</h2>
  <p class="hint">策略收益高度依赖牛市环境——这是低价股效应的典型特征</p>
  <table>
    <thead><tr><th>区间</th><th>说明</th><th>区间收益</th></tr></thead>
    <tbody id="segbody"></tbody>
  </table>
</section>

<section>
  <h2>框架结构</h2>
  <p class="hint">引擎与策略完全解耦 —— 只实现 on_rebalance 一个方法即可装载任意选股模型</p>
  <div class="tree">
<b>a-share-backtest/</b>
├── <b>backtest/</b>              引擎核心
│   ├── data.py          数据加载 + 脏数据清洗
│   ├── strategy.py      策略基类 BaseStrategy / 上下文 Context
│   ├── engine.py        调仓 · 成交 · 成本 · 净值
│   └── metrics.py       绩效指标 · 牛熊分段
├── <b>strategies/</b>           策略插件（可自由扩展）
│   ├── low_price.py         低价N股
│   ├── low_price_low_vol.py 低价+低波双因子
│   └── low_pb.py            低PB价值
├── <b>run_backtest.py</b>       命令行入口
├── fetch_all.py / update_daily.py    全量抓取 / 增量更新
├── add_*.py                 复权 / 估值 / 退市 / 基准 / 快照 补数
├── make_site.py             生成本页面
└── docs/index.html          GitHub Pages 首页
  </div>
</section>

<section>
  <h2>快速开始</h2>
  <p class="hint">本地需 Python 3.11 + pandas/numpy；数据库用抓取脚本生成（见 README）</p>
  <pre><code><span class="cm"># 1. 安装依赖</span>
pip install pandas numpy requests beautifulsoup4

<span class="cm"># 2. 抓取全A股日线数据，生成 a_share_daily.db</span>
python3 <span class="kw">fetch_all.py</span>
python3 <span class="kw">add_adjusted_prices.py</span>   <span class="cm"># 后复权</span>
python3 <span class="kw">add_benchmarks.py</span>         <span class="cm"># 基准指数</span>

<span class="cm"># 3. 运行回测：低价20股，月度调仓</span>
python3 <span class="kw">run_backtest.py</span> --strategy strategies.low_price:LowPriceStrategy \
    --start 2010-01-01 --end 2024-12-31 --freq monthly --n 20 \
    --save-equity equity.csv

<span class="cm"># 4. 换一个策略，只需改 --strategy</span>
python3 <span class="kw">run_backtest.py</span> --strategy strategies.low_pb:LowPbStrategy --freq monthly</code></pre>
</section>

<section>
  <h2>数据与局限</h2>
  <div class="note">
    <ul>
      <li><b>复权价格</b>：收益一律用后复权价计算，分红送转已还原，避免除权日假暴跌。</li>
      <li><b>幸存者偏差</b>：已扫描补齐 <b>379 只退市股</b>历史数据，大幅降低偏差。</li>
      <li><b>历史估值</b>：PE/PB/PS/市值覆盖 2018 年至今（东财数据），更早区间无估值。</li>
      <li><b>交易设定</b>：T 日选股、T+1 开盘成交，含佣金/印花税/滑点与一字板过滤，无未来函数。</li>
      <li><b>注意事项</b>：回测绝对收益请审慎看待，建议重点用于<b>策略间相对比较</b>；本页不构成投资建议。</li>
    </ul>
  </div>
</section>

<footer>
  A股通用回测框架 · 数据来自公开财经接口 · 仅供量化研究学习，不构成任何投资建议
</footer>
</div>

<script>
const DATA = __DATA__;
const M = __METRICS__;
const svg = document.getElementById('chart');
const tip = document.getElementById('tip');
const W=1000,H=440, L=54,R=16,T=16,B=34;
const iw=W-L-R, ih=H-T-B;
const all = DATA.eq.concat(DATA.bm);
const ymin=Math.log10(Math.min(...all)*0.9), ymax=Math.log10(Math.max(...all)*1.1);
const x=i=> L + i*(iw/(DATA.dates.length-1));
const y=v=> T + ih - (Math.log10(v)-ymin)/(ymax-ymin)*ih;
function path(arr){return arr.map((v,i)=>(i?'L':'M')+x(i).toFixed(1)+','+y(v).toFixed(1)).join(' ');}
let g='';
// 网格 + y轴标签(对数刻度)
[0.5,1,2,5,10].forEach(t=>{ if(t<Math.pow(10,ymin)||t>Math.pow(10,ymax))return;
  const yy=y(t); g+=`<line x1="${L}" y1="${yy}" x2="${W-R}" y2="${yy}" stroke="#30363d" stroke-width="1" stroke-dasharray="3 3"/>`;
  g+=`<text x="${L-10}" y="${yy+4}" fill="#8b949e" font-size="11" text-anchor="end">${t}x</text>`;});
// x轴年份
DATA.dates.forEach((d,i)=>{ if(d.slice(5)!=='01-31'&&d.slice(5)!=='01-30'&&d.slice(5)!=='01-31')return;
  if(d.endsWith('-01-31')||d.endsWith('-01-30')){} });
const seen=new Set();
DATA.dates.forEach((d,i)=>{const yr=d.slice(0,4); if(seen.has(yr))return;
  if(d.slice(5,10)<'01-15')return; seen.add(yr);
  g+=`<text x="${x(i)}" y="${H-10}" fill="#8b949e" font-size="11" text-anchor="middle">${yr}</text>`;});
// 曲线
g+=`<path d="${path(DATA.bm)}" fill="none" stroke="#58a6ff" stroke-width="2" opacity=".85"/>`;
g+=`<path d="${path(DATA.eq)}" fill="none" stroke="#f85149" stroke-width="2.4"/>`;
// 悬停十字线(隐藏)
g+=`<line id="cross" x1="0" y1="${T}" x2="0" y2="${T+ih}" stroke="#8b949e" stroke-width="1" opacity="0"/>`;
svg.innerHTML=g;
const cross=document.getElementById('cross');
const box=document.getElementById('chartbox');
svg.addEventListener('mousemove',e=>{
  const r=svg.getBoundingClientRect();
  const px=(e.clientX-r.left)/r.width*W;
  let i=Math.round((px-L)/iw*(DATA.dates.length-1));
  i=Math.max(0,Math.min(DATA.dates.length-1,i));
  const cx=x(i); cross.setAttribute('x1',cx);cross.setAttribute('x2',cx);cross.setAttribute('opacity','1');
  tip.innerHTML=`<div class="d">${DATA.dates[i]}</div>策略 <b style="color:#f85149">${DATA.eq[i]}x</b> · 沪深300 <b style="color:#58a6ff">${DATA.bm[i]}x</b>`;
  tip.style.opacity='1';
  const bx=(cx/W)*r.width + (r.left-box.getBoundingClientRect().left);
  tip.style.left=Math.min(bx+12, box.clientWidth-tip.offsetWidth-8)+'px';
  tip.style.top='56px';
});
svg.addEventListener('mouseleave',()=>{cross.setAttribute('opacity','0');tip.style.opacity='0';});
// 分段表
const tb=document.getElementById('segbody');
M.segments.forEach(s=>{
  if(s.value===null)return;
  const cls=s.value>0?'up':'down';
  const sign=s.value>0?'+':'';
  tb.innerHTML+=`<tr><td>${s.label}</td><td style="color:var(--muted)">${s.range}</td><td class="${cls}">${sign}${s.value}%</td></tr>`;
});
</script>
</body>
</html>
"""


def main():
    metrics, curve = build_data()
    html = (HTML
            .replace("__CAGR__", str(metrics["cagr"]))
            .replace("__EXCESS__", str(metrics["excess"]))
            .replace("__MDD__", str(metrics["mdd"]))
            .replace("__SHARPE__", str(metrics["sharpe"]))
            .replace("__FINAL__", str(metrics["final"]))
            .replace("__START__", metrics["start"])
            .replace("__END__", metrics["end"])
            .replace("__YEARS__", str(metrics["years"]))
            .replace("__DATA__", json.dumps(curve, ensure_ascii=False))
            .replace("__METRICS__", json.dumps(metrics, ensure_ascii=False)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"已生成 {OUT} ({OUT.stat().st_size/1024:.1f} KB)")


if __name__ == "__main__":
    main()
