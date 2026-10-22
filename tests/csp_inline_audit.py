#!/usr/bin/env python3
"""CSP 内联脚本审计（CI 护栏）。

带 CSP meta 的页面用 script-src 的 sha256 哈希钉住内联 <script> 本体。
一旦修改内联 JS 而未更新哈希，浏览器会静默拦截整段脚本——页面整个"失能"
（按钮无监听、导出/提交全部无反应），API 层测试完全感知不到。
本次审计在 CI 中失败任何：
  1) CSP 页面的内联脚本哈希不在 script-src 白名单内；
  2) CSP 页面出现内联事件处理器（onclick= 等，仓库约定：事件委托）。

用法：python tests/csp_inline_audit.py [frontend目录]
"""
import base64
import hashlib
import pathlib
import re
import sys


def _inline_scripts(html: str):
    return re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", html, re.S)


def _csp_meta(html: str):
    for meta in re.findall(r"<meta[^>]+>", html):
        if re.search(r"content-security-policy", meta, re.I):
            m = re.search(r'content="([^"]*)"', meta)
            return m.group(1) if m else None
    return None


def _strip_scripts(html: str) -> str:
    return re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S)


def audit(frontend: pathlib.Path) -> list:
    problems = []
    pages = sorted(frontend.glob("*.html")) + sorted(frontend.glob("mobile/*.html"))
    for f in pages:
        html = f.read_text(encoding="utf-8")
        csp = _csp_meta(html)
        if csp is None:
            continue
        pinned = set(re.findall(r"'sha256-([A-Za-z0-9+/=]+)'", csp))
        for i, script in enumerate(_inline_scripts(html)):
            h = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()
            if h not in pinned:
                problems.append(
                    f"{f}: 内联脚本 #{i} 的哈希 sha256-{h} 不在 script-src 白名单内"
                    f"（白名单：{sorted(pinned) or '空'}）——浏览器将拦截整段脚本"
                )
        # 仓库约定：CSP 禁止内联事件处理器（动态按钮须事件委托）。
        # 只在 <script> 之外的 HTML 区域检查，避免 JS 字符串误报。
        handlers = re.findall(r'\bon[a-z]+=', _strip_scripts(html), re.I)
        if handlers:
            problems.append(f"{f}: 检测到内联事件处理器 {sorted(set(handlers))}（CSP 约定禁用）")
    return problems


def main() -> int:
    frontend = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent.parent / "frontend"
    if not frontend.is_dir():
        print(f"目录不存在: {frontend}", file=sys.stderr)
        return 2
    problems = audit(frontend)
    if problems:
        print("CSP 内联脚本审计失败：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("CSP 内联脚本审计：全部页面哈希与白名单一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
