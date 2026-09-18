#!/usr/bin/env bash
# GitHub 直连 / 镜像站切换工具
#
# 背景：部分网络环境访问 github.com 不稳定（TCP SYN 丢失、fetch/push 超时）。
# 本脚本在「直连」与「镜像站（gh-proxy.com）」之间切换 origin 远端 URL，
# 网络不稳时快速切到镜像继续 fetch/pull/push。
#
# 用法：
#   ./gh_mirror_switch.sh status    # 查看当前模式与远端 URL
#   ./gh_mirror_switch.sh direct    # 切换到 github.com 直连
#   ./gh_mirror_switch.sh mirror    # 切换到 gh-proxy.com 镜像
#   ./gh_mirror_switch.sh auto      # 自动：探测直连，不稳定则切镜像；直连恢复则切回
#
# 说明：
#   - 只修改 origin 远端 URL，不动其它远端与仓库配置；
#   - 镜像为 gh-proxy.com 加速代理（git ls-remote 验证可用）；
#   - 探测超时可用环境变量 GH_PROBE_TIMEOUT 调整（秒，默认 15）。
set -euo pipefail
cd "$(dirname "$0")"

DIRECT_URL="https://github.com/AllenMGu/partsdepot.git"
MIRROR_URL="https://gh-proxy.com/https://github.com/AllenMGu/partsdepot.git"
TIMEOUT_SECONDS="${GH_PROBE_TIMEOUT:-15}"

current_url() { git remote get-url origin 2>/dev/null || echo ""; }

mode_of() {
  case "$(current_url)" in
    *gh-proxy.com*) echo "mirror（镜像 gh-proxy.com）" ;;
    *github.com/AllenMGu/partsdepot*) echo "direct（直连 github.com）" ;;
    *) echo "unknown（自定义 URL）" ;;
  esac
}

probe() { # probe <url> → 0=可达
  timeout "${TIMEOUT_SECONDS}s" git ls-remote --head "$1" HEAD >/dev/null 2>&1
}

show_status() {
  echo "当前模式 : $(mode_of)"
  echo "origin   : $(current_url)"
  echo "直连可达 : $(probe "$DIRECT_URL" && echo "是" || echo "否（超过 ${TIMEOUT_SECONDS}s）")"
  echo "镜像可达 : $(probe "$MIRROR_URL" && echo "是" || echo "否（超过 ${TIMEOUT_SECONDS}s）")"
}

set_url() { # set_url <url>
  git remote set-url origin "$1"
  echo "已切换 origin -> $1"
}

cmd="${1:-status}"
case "$cmd" in
  status)
    show_status
    ;;
  direct)
    set_url "$DIRECT_URL"
    ;;
  mirror)
    set_url "$MIRROR_URL"
    ;;
  auto)
    if probe "$DIRECT_URL"; then
      if [[ "$(current_url)" == *gh-proxy.com* ]]; then
        echo "直连已恢复，切回直连。"
        set_url "$DIRECT_URL"
      else
        echo "直连可用，保持直连。"
      fi
    else
      if [[ "$(current_url)" == *gh-proxy.com* ]]; then
        echo "直连不稳定，当前已是镜像，保持不变。"
      else
        echo "直连不稳定（${TIMEOUT_SECONDS}s 内无响应），切换到镜像。"
        set_url "$MIRROR_URL"
      fi
    fi
    ;;
  *)
    echo "用法: $0 {status|direct|mirror|auto}" >&2
    exit 2
    ;;
esac
