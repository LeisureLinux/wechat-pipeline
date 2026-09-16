#!/usr/bin/env python3
"""公众号石墨极简排版稿：统一补齐/规整「尾部作者签名区」（gzh-design 组件 16）。

背景：gzh-design 石墨极简成品历史稿件签名区写法不统一——
有的只有「点赞三连」段，有的只有一行 tagline，有的整块缺失。
本脚本把签名区统一为「作者自述 + 点赞三连 + 右对齐 tagline」三段式，幂等可重复跑。

用法：
    # 预演（只打印改动，不写文件）
    python3 gzh_signature.py --account ZEN --dry-run 稿件1.html 稿件2.html

    # 实际写入
    python3 gzh_signature.py --account ZEN 稿件1.html *.html

    # 通配
    python3 gzh_signature.py --account ZEN "目录/*_排版_石墨极简风(graphite-minimal).html"

账号默认文案见 ACCOUNTS；可用 --blurb / --tagline / --author 覆盖。
写入后需重新生成预览页：
    python3 ~/.codex/skills/gzh-design-skill/scripts/wrap_preview.py <稿件.html>
"""

import argparse
import glob
import os
import re
import sys

ACCOUNTS = {
    # lore 仓库内容发到 LeisureLinux 公众号，作者署名统一用 FreeLAMP.com（文字稿也用这句）
    "LeisureLinux": {
        "author": "FreeLAMP.com",
        "blurb": "我是 FreeLAMP.com，专注开源安全工具与工程实践，持续分享安全入门与前沿技术的观察。",
    },
    "ZEN": {
        "author": "退休前后",
        "blurb": "我是退休前后，关注健康科学与生活方式的证据，陪你把退休前后的日子过得更明白。",
        "tagline": "—— 退休前后 · 退休不是终点，而是生活的重新开始",
    },
}

SIG_COMMENT = "  <!-- 尾部作者签名区（组件 16） -->"


def build_block(author: str, blurb: str, tagline: str = "") -> str:
    tail = (
        '      <p style="text-align:right;font-size:12px;color:#A1A1AA;margin:18px 0 0;'
        'letter-spacing:1px;">\n'
        f'        <span leaf="">{tagline}</span>\n'
        '      </p>\n'
    ) if tagline else ""
    return f"""{SIG_COMMENT}
  <section style="padding:0 10px 24px;">
    <section style="border-top:1px solid #E4E4E7;padding-top:28px;">
      <p style="margin-bottom:16px;font-size:15px;line-height:1.72;color:#52525B;text-align:justify;letter-spacing:0.3px;">
        <span leaf="">{blurb}</span>
      </p>
      <p style="margin-bottom:{'0' if not tagline else '0'};font-size:15px;line-height:1.72;color:#52525B;text-align:justify;letter-spacing:0.3px;">
        <span leaf="">如果你觉得今天这篇有收获，欢迎</span><strong style="color:#27272A;"><span leaf="">点赞、在看、转发</span></strong><span leaf="">三连，我们下篇见。</span>
      </p>
{tail}    </section>
  </section>
"""


def _find_sig_start(s: str, body_end: int):
    """返回签名区起点（从该处到 body_end 将被替换）；找不到返回 None。"""
    tail = s[:body_end]
    best = -1
    for marker in ("<!-- 尾部作者签名区", "<!-- 签名区", "<!-- 组件16"):
        i = tail.rfind(marker)
        if i != -1:
            # 回退到行首
            i = tail.rfind("\n", 0, i) + 1
            best = max(best, i)
    if best != -1:
        return best

    # 只有一行 tagline 的写法（如「—— 退休前后 · 退休不是终点…」）
    i = tail.rfind("退休不是终点")
    if i != -1:
        j = tail.rfind('<p style="text-align:right', 0, i)
        if j == -1:
            j = tail.rfind("<p", 0, i)
        if j != -1:
            j = tail.rfind("\n", 0, j) + 1
            return j

    # 只有「点赞三连」段的写法
    i = tail.rfind("如果你觉得今天这篇有收获")
    if i != -1:
        j = tail.rfind('border-top:1px solid #E4E4E7', 0, i)
        if j != -1:
            j = tail.rfind("<section", 0, j)
        if j == -1:
            j = tail.rfind("<p", 0, i)
        if j != -1:
            j = tail.rfind("\n", 0, j) + 1
            return j
    return None


def patch(content: str, block: str):
    """返回 (新内容, 动作)。动作 ∈ {'skip','replace','insert'}"""
    s = content.rstrip()
    if not s.endswith("</section>"):
        raise ValueError("不是 gzh-design 正文片段（结尾不是 </section>）")

    body_end = s.rfind("</section>")
    if "退休前后" in s[body_end - 1200:] and "退休不是终点" in s and SIG_COMMENT in s:
        return content, "skip"

    start = _find_sig_start(s, body_end)
    if start is None:
        new = s[:body_end].rstrip() + "\n\n" + block + "\n</section>\n"
        return new, "insert"

    new = s[:start].rstrip() + "\n\n" + block + "\n</section>\n"
    return new, "replace"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="稿件 html 路径或通配串")
    ap.add_argument("--account", "-a", default="ZEN", choices=sorted(ACCOUNTS))
    ap.add_argument("--author")
    ap.add_argument("--blurb")
    ap.add_argument("--tagline")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conf = dict(ACCOUNTS[args.account])
    conf["author"] = args.author or conf["author"]
    conf["blurb"] = args.blurb or conf["blurb"]
    conf["tagline"] = args.tagline or conf.get("tagline", "")
    block = build_block(**conf)

    paths = []
    for pat in args.files:
        hits = sorted(glob.glob(pat)) if any(c in pat for c in "*?[") else [pat]
        if not hits:
            print(f"✗ 无匹配: {pat}", file=sys.stderr)
        paths.extend(hits)

    stats = {"skip": 0, "replace": 0, "insert": 0}
    for p in paths:
        src = open(p, encoding="utf-8").read()
        try:
            new, action = patch(src, block)
        except ValueError as e:
            print(f"✗ {os.path.basename(p)}: {e}", file=sys.stderr)
            continue
        stats[action] += 1
        if action != "skip" and not args.dry_run:
            open(p, "w", encoding="utf-8").write(new)
        if action == "skip":
            print(f"= 跳过（已规范） {os.path.basename(p)}")
        else:
            tag = "预演" if args.dry_run else "已写"
            print(f"✓ {tag} {action} {os.path.basename(p)}")

    print(f"\n合计 {len(paths)} 个文件：替换 {stats['replace']}，新增 {stats['insert']}，跳过 {stats['skip']}")
    if args.dry_run:
        print("（dry-run，未写入）")


if __name__ == "__main__":
    main()
