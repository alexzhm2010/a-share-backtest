#!/usr/bin/env bash
# ============================================================
# A股通用回测框架 —— 一键部署到 GitHub + 开启 GitHub Pages
# 用法：在安装了 git 和 gh 的终端里运行  bash deploy_to_github.sh
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "============================================"
echo " A股回测框架 · 一键部署到 GitHub"
echo "============================================"

# --- 1. 检查工具 ---
if ! command -v git >/dev/null 2>&1; then echo "缺少 git，请先安装：https://git-scm.com/"; exit 1; fi
if ! command -v gh  >/dev/null 2>&1; then echo "缺少 GitHub CLI，请安装：https://cli.github.com/"; exit 1; fi
if ! gh auth status >/dev/null 2>&1; then
  echo "尚未登录 GitHub，请先运行： gh auth login"
  echo "（按提示选择 GitHub.com → HTTPS → 浏览器登录）"
  exit 1
fi

# --- 2. 读取用户名 / 仓库名 ---
USER=$(gh api user --jq .login)
read -rp "仓库名（直接回车用默认 a-share-backtest）: " REPO
REPO=${REPO:-a-share-backtest}
echo "→ 用户: $USER   仓库: $REPO"

# --- 3. 把 README 里的演示页链接占位符替换成真实用户名 ---
if [[ "$OSTYPE" == "darwin"* ]]; then
  sed -i '' "s|YOUR_USERNAME|$USER|g" README.md
else
  sed -i "s|YOUR_USERNAME|$USER|g" README.md
fi

# --- 4. 初始化 git 并提交 ---
git init -q
git branch -M main
git add -A
if ! git diff --cached --quiet; then
  git commit -m "Initial commit: A-share backtest framework + GitHub Pages"
else
  echo "→ 无新改动，跳过 commit"
fi

# --- 5. 创建远程仓库（已存在则复用）并推送 ---
if gh repo view "$USER/$REPO" >/dev/null 2>&1; then
  echo "→ 仓库已存在，推送到 origin/main"
  git remote remove origin 2>/dev/null || true
  git remote add origin "https://github.com/$USER/$REPO.git"
  git push -u origin main
else
  echo "→ 创建新公开仓库并推送"
  gh repo create "$REPO" --public --source=. --remote=origin --push
fi

# --- 6. 开启 GitHub Pages（来源 = main 分支的 /docs 目录）---
echo "→ 配置 GitHub Pages ..."
if echo '{"source":{"branch":"main","path":"/docs"}}' \
   | gh api -X PUT "repos/$USER/$REPO/pages" --input - >/dev/null 2>&1; then
  echo "→ Pages 已开启"
else
  echo "→ Pages 自动配置未成功，请手动开启："
  echo "   仓库 Settings → Pages → Source 选 'Deploy from a branch'"
  echo "   Branch 选 main，目录选 /docs，Save"
fi

echo ""
echo "============================================"
echo " 部署完成！"
echo " 仓库首页 : https://github.com/$USER/$REPO"
echo " 静态演示页: https://$USER.github.io/$REPO/"
echo "（Pages 首次生效约需 1-2 分钟）"
echo "============================================"
