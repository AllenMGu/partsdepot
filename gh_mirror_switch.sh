#!/usr/bin/env bash
# GitHub 直连 / 镜像站切换工具
#
# 背景：部分网络环境访问 github.com 不稳定（TCP SYN 丢失、fetch 超时）。
# 本脚本在「直连」与「镜像站（gh-proxy.com）」之间切换 origin 的 *fetch* URL。
#
# 安全约定（重要）：
#   - 镜像站只允许作为 fetch（ls-remote/fetch/pull）地址；
#   - push 永远直连 github.com（git remote set-url --push），凭证与推送内容
#     不经过任何第三方代理；镜像站对 push 也不提供有效支持；
#   - auto 模式只会在「直连不可达 且 镜像可达」时切镜像，两者都不可达时
#     保持现状；origin 是自定义 URL 时 auto 一律不改动。
#
# 用法：
#   ./gh_mirror_switch.sh status    # 查看当前 fetch/push URL 与可达性
#   ./gh_mirror_switch.sh direct    # fetch 与 push 均直连 github.com
#   ./gh_mirror_switch.sh mirror    # fetch 走镜像，push 仍直连 github.com
#   ./gh_mirror_switch.sh auto      # 自动：直连优先；直连挂、镜像活才切镜像
#
# 说明：
#   - 只修改 origin 的 fetch/push URL，不动其它远端与仓库配置；
#   - 镜像为 gh-proxy.com 加速代理（git ls-remote 验证可用）；
#   - 探测超时可用环境变量 GH_PROBE_TIMEOUT 调整（秒，默认 15）。
set -euo pipefail
cd "$(dirname "$0")"

DIRECT_URL="https://github.com/AllenMGu/partsdepot.git"
MIRROR_URL="https://gh-proxy.com/https://github.com/AllenMGu/partsdepot.git"
TIMEOUT_SECONDS="${GH_PROBE_TIMEOUT:-15}"

fetch_url() { git remote get-url origin 2>/dev/null || echo ""; }
push_url()  { git remote get-url --push origin 2>/dev/null || echo ""; }

mode_of() {
  case "$(fetch_url)" in
    *gh-proxy.com*) echo "mirror（fetch 走镜像 gh-proxy.com；push 直连 github.com）" ;;
    *github.com/AllenMGu/partsdepot*) echo "direct（fetch/push 均直连 github.com）" ;;
    *) echo "custom（自定义 URL，auto 模式不会改动）" ;;
  esac
}

probe() { # probe <url> → 0=可达
  timeout "${TIMEOUT_SECONDS}s" git ls-remote --head "$1" HEAD >/dev/null 2>&1
}

show_status() {
  echo "当前模式 : $(mode_of)"
  echo "fetch URL: $(fetch_url)"
  echo "push  URL : $(push_url)"
  echo "直连可达 : $(probe "$DIRECT_URL" && echo "是" || echo "否（超过 ${TIMEOUT_SECONDS}s）")"
  echo "镜像可达 : $(probe "$MIRROR_URL" && echo "是" || echo "否（超过 ${TIMEOUT_SECONDS}s）")"
}

# 直连模式：fetch 与 push 都指向 github.com
apply_direct() {
  git remote set-url origin "$DIRECT_URL"
  git remote set-url --push origin "$DIRECT_URL"
  echo "已切换：fetch/push -> $DIRECT_URL"
}

# 镜像模式：fetch 走镜像加速；push 保持直连（凭证不经过第三方）
apply_mirror() {
  git remote set-url origin "$MIRROR_URL"
  git remote set-url --push origin "$DIRECT_URL"
  echo "已切换：fetch -> $MIRROR_URL"
  echo "        push  -> $DIRECT_URL（保持直连，不经过镜像）"
}

cmd="${1:-status}"
case "$cmd" in
  status)
    show_status
    ;;
  direct)
    apply_direct
    ;;
  mirror)
    apply_mirror
    ;;
  auto)
    cur="$(fetch_url)"
    case "$cur" in
      *gh-proxy.com*|*github.com/AllenMGu/partsdepot*) ;;
      *)
        # 自定义 origin（fork/私有镜像/内网等）：auto 一律不改动
        echo "origin 是自定义 URL（$cur），auto 模式不做改动。"
        echo "如确需切换，请显式执行: $0 direct 或 $0 mirror"
        exit 0
        ;;
    esac
    if probe "$DIRECT_URL"; then
      if [[ "$cur" == *gh-proxy.com* ]]; then
        echo "直连已恢复，切回直连。"
        apply_direct
      else
        echo "直连可用，保持直连。"
      fi
    elif probe "$MIRROR_URL"; then
      if [[ "$cur" == *gh-proxy.com* ]]; then
        echo "直连不可用，当前已是镜像，保持不变。"
      else
        echo "直连不可用（${TIMEOUT_SECONDS}s 内无响应），镜像可达，切换到镜像。"
        apply_mirror
      fi
    else
      echo "警告：直连与镜像均不可达（${TIMEOUT_SECONDS}s 超时），保持当前配置。"
      echo "当前: $(mode_of)"
      exit 1
    fi
    ;;
  *)
    echo "用法: $0 {status|direct|mirror|auto}" >&2
    exit 2
    ;;
esac
