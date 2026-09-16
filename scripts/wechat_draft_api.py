#!/usr/bin/env python3
"""
微信公众号草稿箱 API 对接脚本
功能：自动维护 access_token、新建草稿、查询草稿

使用方法：
  python3 wechat_draft_api.py create --title "标题" < markdown_content.md
  
配置通过环境变量 WECHAT_APPID 和 WECHAT_APPSECRET 或配置文件管理。
"""

import os
import sys
import json
import time
import re
import html as _html
import requests
from datetime import datetime
from pathlib import Path

DEFAULT_AUTHOR = "小龙女"
CONFIG_DIR = Path.home() / ".openclaw" / "workspace" / "pipeline" / "config"
CONFIG_FILE = CONFIG_DIR / "wechat_config.json"
TOKEN_CACHE_FILE = CONFIG_DIR / "access_token_cache.json"

# 默认封面图 media_id（从微信素材库查询到的永久素材）
# 2026-06-11 更新：diffusiongemma_cover.png (1200x675)
DEFAULT_THUMB_MEDIA_ID = "9NhIaHtRRLW8sX0zmFnAHV31Mx3vtG6FeRfXdVZtndhDGxq2VjDUsc2yQqQygzTZ"


# ============================================================
# 配置管理
# ============================================================

def ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 多账号配置（读 ~/.openclaw/.env 里的 ZEN_/LeisureLinux_ 变量）
# ============================================================
def _load_env_accounts() -> dict:
    """多环境探测：~/.codex/.env → ~/.openclaw/.env → fallback"""
    candidates = [
        Path.home() / ".codex" / ".env",           # Codex 平台
        Path.home() / ".openclaw" / ".env",        # OpenClaw 平台
        Path.home() / ".openclaw" / "workspace" / "pipeline" / "config" / "env",
    ]
    env_path = None
    for p in candidates:
        if p.exists():
            env_path = p
            break
    if not env_path:
        return {}
    
    accounts = {}
    try:
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k.endswith("_AppID"):
                acct = k[:-6]
                accounts.setdefault(acct, {})["appid"] = v
            elif k.endswith("_AppSecret"):
                acct = k[:-10]
                accounts.setdefault(acct, {})["appsecret"] = v
    except Exception:
        pass
    return accounts
def load_config(account: str = None) -> dict:
    """加载公众号配置

    Args:
        account: 账号别名（与 .env 里 XXX_AppID 的 XXX 一致），如 "ZEN" / "LeisureLinux"
                 传 None 则读 WECHAT_APPID 环境变量或默认 config 文件
    """
    ensure_config_dir()
    config = {"appid": "", "appsecret": ""}

    # 0. 别名映射
    ACCOUNT_ALIAS = {
        "LL": "LeisureLinux",
    }
    if account in ACCOUNT_ALIAS:
        account = ACCOUNT_ALIAS[account]

    # 1. 命令行指定账号 → 从 .env 拿
    if account:
        accounts = _load_env_accounts()
        if account not in accounts:
            available = list(accounts.keys())
            raise ValueError(f"账号 {account!r} 不在 .env 中，可用: {available}")
        cfg = accounts[account]
        config["appid"] = cfg.get("appid", "")
        config["appsecret"] = cfg.get("appsecret", "")
        return config

    # 2. 环境变量覆盖
    env_appid = os.environ.get("WECHAT_APPID")
    env_secret = os.environ.get("WECHAT_APPSECRET")
    if env_appid:
        config["appid"] = env_appid
    if env_secret:
        config["appsecret"] = env_secret

    # 3. 默认 config 文件
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                fc = json.load(f)
                if not env_appid and "appid" in fc:
                    config["appid"] = fc["appid"]
                if not env_secret and "appsecret" in fc:
                    config["appsecret"] = fc["appsecret"]
        except:
            pass

    return config


def list_accounts() -> list:
    """列出 .env 里所有可用账号"""
    return list(_load_env_accounts().keys())


# ============================================================
# Access Token 管理
# ============================================================

def _token_cache_path(appid: str = None) -> Path:
    """多环境：.openclaw → .codex → /tmp/codex-wx（自动探测可写目录）"""
    import tempfile
    for d in [CONFIG_DIR, Path.home() / ".codex", Path("/tmp/codex-wx-tokens")]:
        try:
            d.mkdir(parents=True, exist_ok=True)
            test = d / ".wt"
            test.touch()
            test.unlink()
            if appid is not None:
                safe = str(appid).replace("|", "_").replace("/", "_")[-24:]
                return d / f"token_{safe}.json"
            return d / "access_token_cache.json"
        except (PermissionError, OSError):
            continue
    # 全部失败：用临时目录保底
    tmpd = Path(tempfile.gettempdir()) / "codex-wx-tokens"
    tmpd.mkdir(parents=True, exist_ok=True)
    if appid is not None:
        safe = str(appid).replace("|", "_").replace("/", "_")[-24:]
        return tmpd / f"token_{safe}.json"
    return tmpd / "access_token_cache.json"
def load_token_cache(appid: str = None) -> dict:
    p = _token_cache_path(appid)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except:
            pass
    return {}


def save_token_cache(token: str, expires_in: int, appid: str = None):
    ensure_config_dir()
    cache = {
        "access_token": token,
        "expires_at": time.time() + expires_in - 300,
    }
    p = _token_cache_path(appid)
    p.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def get_access_token(appid: str, appsecret: str) -> str:
    cache = load_token_cache(appid)
    if cache:
        token = cache.get("access_token")
        expires_at = cache.get("expires_at", 0)
        if token and time.time() < expires_at:
            print(f"✅ 使用缓存的 access_token {appid[-6:]}（剩余有效期: {int(expires_at - time.time())}s）", file=sys.stderr)
            return token

    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={appid}&secret={appsecret}"

    try:
        resp = requests.get(url, timeout=30)
        result = resp.json()

        if "access_token" in result:
            token = result["access_token"]
            expires_in = result.get("expires_in", 7200)
            save_token_cache(token, expires_in, appid=appid)
            print(f"✅ 获取新 access_token {appid[-6:]}（有效期: {expires_in}s）", file=sys.stderr)
            return token
        else:
            raise RuntimeError(f"获取 access_token 失败: {result}")

    except Exception as e:
        raise RuntimeError(f"获取 access_token 异常: {e}")


# ============================================================
# Markdown → 微信草稿箱 HTML
# ============================================================

def markdown_to_wechat_html(md_text: str) -> str:
    """将 Markdown 转换为微信公众号草稿箱兼容的 HTML

    增强项（针对 IT 禅悟公众号排版质量）：
    1. 跳过空 bullet 项（- / * / 1. 后无内容）
    2. 压缩连续空行（避免微信编辑器出现多头 p）
    3. 跳过“作者/创作来源/数据来源”这类页脚元信息行
    """
    lines = md_text.split('\n')

    # 后处理：去除页脚元信息（“作者/创作来源/数据来源”等）
    # 如果某行以这些词开头，后同段或同一行+后续是作者名/域名/AI 生成，就丢掉
    footer_patterns = [
        r'^[✍️]?[\s\W]*(作者|Author|写作)[\s:：]',
        r'^[🤖]?[\s\W]*(创作来源|Source|Generated by|数据来源|数据由)[\s:：]',
        r'^[📅]?[\s\W]*(数据日期|发布日期|Date)[\s:：]',
        r'^[⏰]?[\s\W]*(发布时间|Published)[\s:：]',
        r'^—\s*小龙女\s*$',
    ]
    filtered_lines = []
    for line in lines:
        if any(re.match(p, line.strip()) for p in footer_patterns):
            continue
        filtered_lines.append(line)
    lines = filtered_lines

    # 隐藏“数据不完整的空章节”：上一行 --- + 当前 ## 标题 + 下一行是 <!--- 暂无明显... --->
    cleaned = []
    skip_next_two = False
    for i, line in enumerate(lines):
        if skip_next_two:
            skip_next_two = False
            continue
        if (line.startswith('## ') and i + 1 < len(lines)
                and '<!-- ' in lines[i + 1] and '暂无明显' in lines[i + 1]):
            # 跳过这行标题、下一行注释、以及上面那行可能的 ---
            cleaned.pop()  # 去掉上轮 append 的 ---
            continue
        # 同样过滤上一行没 --- 但下一行是 <!-- 暂无... 注释 -> 该行及之前的 标题 都要跳过
        if line.strip().startswith('<!-- ') and '暂无明显' in line:
            # 如果上一行是 ## 标题, 上一上可能是 ---, 都不输出
            if cleaned and cleaned[-1].startswith('## '):
                cleaned.pop()
            if cleaned and cleaned[-1].strip() == '---':
                cleaned.pop()
            continue
        cleaned.append(line)
    lines = cleaned

    # 第一行 H1 转为隐藏 (微信群发时会重复显示标题)
    for idx, ln in enumerate(lines):
        if ln.startswith('# '):
            lines[idx] = f'<!-- HIDDEN_H1: {ln[2:].strip()} -->'
            break

    # 预处理: 修复 ### 1. ### 2. 之类被误识别为有序列表的标题
    # 微信编辑器会拆 "1. xxx" 产生空 <ol>, 把标题里的 "1. " "2. " 替换成 " ❶ " " ❷ "
    number_to_emoji = {
        '1': '①', '2': '②', '3': '③', '4': '④', '5': '⑤',
        '6': '⑥', '7': '⑦', '8': '⑧', '9': '⑨', '10': '⑩'
    }
    lines = [
        re.sub(r'^(#{1,6})\s+(\d+)\.\s', lambda m: f'{m.group(1)} {number_to_emoji.get(m.group(2), m.group(2))} ', line)
        for line in lines
    ]
    
    # 预处理：去掉列表内部的空行，使列表项连续
    # 规则：如果当前是列表行（- 或 1. 开头），且下一行是空行，再下一行还是同类列表行，则去掉那个空行
    filtered = []
    i = 0
    while i < len(lines):
        filtered.append(lines[i])
        line = lines[i]
        # 检查是否是列表行且后面跟着 空行+同类列表行
        if i + 2 < len(lines) and not lines[i+1].strip():
            # 列表项（- 开头、* 开头、数字. 开头）
            is_list_item = re.match(r'^\s*[-\*]\s|^\s*\d+\.\s', line)
            next_next = lines[i+2].strip()
            next_is_same_list = bool(re.match(r'^[-\*]\s|^\d+\.\s', next_next))
            if is_list_item and next_is_same_list:
                # 跳过空行，不添加到 filtered
                i += 1
        i += 1
    lines = filtered
    
    result = []
    in_code_block = False
    code_lang = ""
    code_lines = []
    in_list = False
    list_type = None
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # 代码块
        if line.strip().startswith('```'):
            if in_code_block:
                code = '\n'.join(code_lines)
                escaped = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                # 关键排版：white-space:pre-wrap + word-break:break-all + max-width 100%
                # 让超长代码行能自动换行而不溢出
                result.append(f'<pre style="max-width:100%;white-space:pre-wrap;word-wrap:break-word;word-break:break-all;background:#f6f8fa;padding:10px 12px;border-radius:4px;overflow-x:auto;line-height:1.5;font-size:13px;"><code class="language-{code_lang}" style="white-space:pre-wrap;word-break:break-all;display:block;">{escaped}</code></pre>')
                code_lines = []
                code_lang = ""
                in_code_block = False
            else:
                code_lang = line.strip().lstrip('`').strip()
                in_code_block = True
            i += 1
            continue
        
        if in_code_block:
            code_lines.append(line)
            i += 1
            continue
        
        if not line.strip():
            # 空行处理
            # 如果在列表中且下一行还是同类列表项 (- / * / 1. 开头)，则不关闭列表，
            # 避免多个空行导致脚本生成 </ul><ul> 两个分块, 微信会把中间的空格渲染成空 bullet
            if in_list:
                # 只看下一行是不是同类列表, 多空行才打断 (跟 GFM 行为一致)
                j = i + 1
                # 跳过第一段连续空行(容忍 1 个空行)
                if j < len(lines) and not lines[j].strip():
                    j += 1
                nxt_is_same_list = False
                if j < len(lines):
                    nxt = lines[j]
                    nxt_is_same_list = bool(re.match(
                        r'^\s*[-\*]\s+' if list_type == 'ul' else r'^\s*\d+\.\s+',
                        nxt
                    ))
                if nxt_is_same_list:
                    # 还在同一个列表里, 直接吞掉空行不关闭
                    i += 1
                    continue
                # 下一行不是同类列表, 正常关闭
                result.append(f'</{"ol" if list_type == "ol" else "ul"}>')
                in_list = False
                list_type = None
            # 空行：直接跳过，视觉间距由元素的 margin 提供
            i += 1
            continue
        
        if re.match(r'^---+\s*$', line):
            if in_list:
                result.append(f'</{"ol" if list_type == "ol" else "ul"}>')
                in_list = False
                list_type = None
            # hr 在微信里经常被吃掉，改用上下带内距的块状分隔栏
            result.append('<p style="margin:18px 0;text-align:center;color:#8a7abf;letter-spacing:6px;font-size:14px;">━━━ ━━━ ━━━</p>')
            i += 1
            continue
        
        # 标题
        m = re.match(r'^# (.+)$', line)
        if m:
            if in_list:
                result.append(f'</{"ol" if list_type == "ol" else "ul"}>')
                in_list = False
                list_type = None
            t = inline_md(m.group(1))
            result.append(f'<section style="font-size:20px;font-weight:bold;text-align:center;margin:20px 0 10px;color:#333;">{t}</section>')
            i += 1
            continue
        
        m = re.match(r'^## (.+)$', line)
        if m:
            if in_list:
                result.append(f'</{"ol" if list_type == "ol" else "ul"}>')
                in_list = False
                list_type = None
            t = inline_md(m.group(1))
            result.append(f'<section style="font-size:17px;font-weight:bold;text-align:center;margin:25px 0 12px;padding:8px 16px;color:#333;background:#f0edf6;border-radius:4px;">{t}</section>')
            i += 1
            continue
        
        m = re.match(r'^### (.+)$', line)
        if m:
            if in_list:
                result.append(f'</{"ol" if list_type == "ol" else "ul"}>')
                in_list = False
                list_type = None
            t = inline_md(m.group(1))
            result.append(f'<section style="font-size:16px;font-weight:bold;margin:18px 0 8px;color:#111;">{t}</section>')
            i += 1
            continue
        
        # 有序列表（支持缩进嵌套）
        m = re.match(r'^\s*(\d+)\.\s+(.*)$', line)
        if m:
            content = m.group(2).strip()
            leading = len(line) - len(line.lstrip(' \t'))
            if not content:
                i += 1
                continue
            if not in_list or list_type != 'ol':
                if in_list:
                    result.append(f'</{"ol" if list_type == "ol" else "ul"}>')
                result.append('<ol style="padding-left:2em;margin:8px 0;">')
                in_list = True
                list_type = 'ol'
            # 缩进嵌套用额外的 padding-left
            pad = f' style="margin:4px 0;{f"padding-left:{leading*0.5:.1f}em;" if leading else ""}"'
            result.append(f'<li{pad}>{inline_md(content)}</li>')
            i += 1
            continue
        
        # 无序列表（支持缩进嵌套）
        m = re.match(r'^\s*[-\*]\s+(.*)$', line)
        if m:
            content = m.group(1).strip()
            leading = len(line) - len(line.lstrip(' \t'))
            if not content:
                i += 1
                continue
            if not in_list or list_type != 'ul':
                if in_list:
                    result.append(f'</{"ol" if list_type == "ol" else "ul"}>')
                result.append('<ul style="padding-left:2em;margin:8px 0;">')
                in_list = True
                list_type = 'ul'
            pad = f' style="margin:4px 0;{f"padding-left:{leading*0.5:.1f}em;" if leading else ""}"'
            result.append(f'<li{pad}>{inline_md(content)}</li>')
            i += 1
            continue
        
        # 引用
        m = re.match(r'^>\s+(.*)$', line)
        if m:
            if in_list:
                result.append(f'</{"ol" if list_type == "ol" else "ul"}>')
                in_list = False
                list_type = None
            result.append(f'<blockquote style="border-left:3px solid #8a7abf;padding:8px 12px;margin:10px 0;background:#f6f3fc;color:#555;">{inline_md(m.group(1))}</blockquote>')
            i += 1
            continue
        
        # 表格（识别以 | 开头的行为表格行）
        if line.strip().startswith('|') and '|' in line[1:]:
            if in_list:
                result.append(f'</{"ol" if list_type == "ol" else "ul"}>')
                in_list = False
                list_type = None
            # 收集整个表格块
            table_rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_rows.append(lines[i])
                i += 1
            
            if len(table_rows) >= 2:
                # 跳过分隔行（如 |---|---|）
                header_row = table_rows[0]
                data_rows = []
                for r in table_rows[1:]:
                    if re.match(r'^\|[\s:-]+\|', r):
                        continue
                    data_rows.append(r)
                
                def parse_table_row(row: str) -> list:
                    cells = row.strip().split('|')
                    # 去掉首尾空单元格（管道符前后的空串）
                    if cells and cells[0].strip() == '':
                        cells = cells[1:]
                    if cells and cells[-1].strip() == '':
                        cells = cells[:-1]
                    return [c.strip() for c in cells]
                
                # 微信草稿箱支持通过 table 标签渲染
                # 关键排版：table-layout:fixed + word-break:break-all 防列宽溢出
                table_html = ['<table style="border-collapse:collapse;width:100%;margin:12px 0;font-size:14px;table-layout:fixed;word-break:break-all;overflow-wrap:anywhere;">']
                
                # 表头
                header_cells = parse_table_row(header_row)
                table_html.append('<thead><tr>')
                for cell in header_cells:
                    c = inline_md(cell)
                    table_html.append(f'<th style="border:1px solid #ddd;padding:8px 10px;background:#f0edf6;font-weight:bold;text-align:center;word-break:break-all;overflow-wrap:anywhere;">{c}</th>')
                table_html.append('</tr></thead>')
                
                # 数据行
                table_html.append('<tbody>')
                for ridx, row in enumerate(data_rows):
                    cells = parse_table_row(row)
                    bg = '#fafafa' if ridx % 2 == 1 else '#ffffff'
                    table_html.append(f'<tr style="background:{bg};">')
                    for cell in cells:
                        c = inline_md(cell)
                        table_html.append(f'<td style="border:1px solid #ddd;padding:6px 10px;text-align:left;word-break:break-all;overflow-wrap:anywhere;">{c}</td>')
                    table_html.append('</tr>')
                table_html.append('</tbody></table>')
                
                result.append(''.join(table_html))
            continue
        
        # 段落
        if in_list:
            result.append(f'</{"ol" if list_type == "ol" else "ul"}>')
            in_list = False
            list_type = None
        
        processed = inline_md(line)
        if processed.strip():
            # word-wrap:break-word + overflow-wrap:anywhere 防长URL/长串溢出
            result.append(f'<p style="margin:8px 0;line-height:1.75;text-align:justify;word-wrap:break-word;overflow-wrap:anywhere;">{processed}</p>')
        i += 1
    
    if in_list:
        result.append(f'</{"ol" if list_type == "ol" else "ul"}>')
    if in_code_block:
        code = '\n'.join(code_lines)
        escaped = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        result.append(f'<pre><code class="language-{code_lang}">{escaped}</code></pre>')

    # 后处理：彻底去掉所有视觉空行段 <p><br/></p>
    #
    # **重要发现**（2026-06-04 郭大侠反馈 + 代码分析）：
    # 微信编辑器会把 `<p style="margin:6px 0;line-height:1;"><br/></p>` 解析为项目符号点 `·`。
    # 出现位置包括: 列表项 </li> 后面、blockquote 后面、连续段落之间、表格前后。
    # 郭大侠截图里的"空 bullet"就是 </li> 后的 <p><br/></p> 被微信渲染为额外 bullet 点。
    # 之前所有"避免在 list 附近插空段"的过滤逻辑都不够干净,
    # 干脆全部去掉最安全。视觉间距交给各 block 自己的 margin 负责。
    EMPTY_P = re.compile(r'<p\s+style="margin:6px\s+0;line-height:1;"><br\s*/?></p>')
    output = [ln for ln in result if not EMPTY_P.match(ln.strip())]
    return '\n'.join(output)

def inline_md(text: str) -> str:
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
    text = re.sub(r'`([^`]+)`', r'<code style="font-size:0.88em;padding:2px 5px;background:#f5f5f5;color:#d63384;border-radius:3px;word-break:break-all;overflow-wrap:anywhere;">\1</code>', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" style="color:#8a7abf;text-decoration:underline;">\1</a>', text)
    return text






# ============================================================
# 摘要（digest）生成
# ============================================================
# 微信限制（官方文档）：摘要「总长度不超过 120 个字」，未填写则默认抓取正文前 54 个字。
# 实测口径（draft/add，2026-09-16，ensure_ascii=False）：
#   纯中文 120 字 → 通过；121 字 → 45004 description size out of limit
#   纯 ASCII 240 字符 → 通过；241 字符 → 45004
#   100 中文 + 40 ASCII（=240）→ 通过；+41（=241）→ 45004
# 即上限是 240 个「半角单位」：非 ASCII 记 2、ASCII 记 1（等价于 120 个全角字）。
DIGEST_MAX_UNITS = 240     # 微信硬上限
DIGEST_SAFE_UNITS = 232    # 生成时留 8 单位余量，避开边界波动
DIGEST_MIN_UNITS = 80      # 抓到的段落短于此值时，继续并入下一段

# 版式噪声：这些段落是标签/目录/签名，不该进摘要
_DIGEST_LABELS = {
    "QUOTE", "本文看点", "KEY FACTS", "END", "阅读原文", "关注我们",
    "您或许也对以下文章感兴趣：",
}


def _digest_units(s: str) -> int:
    """微信摘要长度单位：非 ASCII 记 2，ASCII 记 1。"""
    return sum(2 if ord(ch) > 0x7F else 1 for ch in s)


def _normalize_ws(s: str) -> str:
    """压缩空白、去掉零宽字符，并清理标点前后的版式空格。"""
    s = s.replace("\u200b", "").replace("\ufeff", "").replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # 版式里 span 拼接会留下「代号 Antspace 。」这类空格，摘要是纯文本需清理
    s = re.sub(r"\s+([，。、；：！？）」』】》%])", r"\1", s)
    s = re.sub(r"([（「『【《])\s+", r"\1", s)
    return s.strip()


def _strip_inline_spaces(s: str) -> str:
    """清掉内联标签边界留下的中文间空格（逐段用，不会伤到段间拼接）。"""
    return re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", s)


def _html_paragraphs(html_text: str):
    """按块级边界把 HTML 拆成纯文本段落，保持文档顺序。"""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_text, flags=re.S | re.I)
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    parts = re.split(r"(?i)<br\s*/?>|</p>|</h[1-6]>|</li>|</section>|</blockquote>|</td>", t)
    out = []
    for p in parts:
        txt = _html.unescape(re.sub(r"<[^>]+>", "", p))
        txt = _strip_inline_spaces(_normalize_ws(txt))
        if txt:
            out.append(txt)
    return out


def _markdown_paragraphs(md_text: str):
    """Markdown → 纯文本段落（标题/粗体/行内代码/链接只留文字）。"""
    t = re.sub(r"```.*?```", "", md_text, flags=re.S)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"^\s*>\s?", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\d+\.\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*_]{3,}\s*$", "", t, flags=re.M)
    return [p for p in (_strip_inline_spaces(_normalize_ws(x)) for x in re.split(r"\n\s*\n", t)) if p]


def _md_h1(md_text: str) -> str:
    """取 Markdown 的首个一级标题（用于从摘要候选里剔除文章标题）。"""
    m = re.search(r"^\s{0,3}#\s+(.+)$", md_text, flags=re.M)
    return _strip_inline_spaces(_normalize_ws(m.group(1))) if m else ""


def _looks_like_title(txt: str) -> bool:
    """首段是不是被当成正文的文章标题（无标点、极短、不带句号）。"""
    t = txt.strip()
    if len(t) > 45 or any(ch in t for ch in "。！？；"):
        return False
    return t.startswith(("批驳", "别再", "关于", "为什么", "如何", "一文"))


def _is_attribution(txt: str) -> bool:
    """翻译稿的来源/授权/元数据说明，不该进摘要。"""
    t = txt.strip()
    if t.startswith("原文：") or t.startswith("原文:"):
        return True
    if t.startswith("原标题：") or t.startswith("原标题:"):
        return True
    if t.startswith("译自") or t.startswith("本文翻译自") or t.startswith("译文来源"):
        return True
    if "本文由 LeisureLinux" in t:
        return True
    if re.match(r"^作者\s*[：:]", t) or re.match(r"^日期\s*[：:]", t):
        return True
    # 元数据聚合段：同时出现「原文」与链接/译自/作者等字样
    if ("原文" in t and ("http" in t or "译自" in t or "原标题" in t)):
        return True
    return False


def _is_digest_noise(txt: str) -> bool:
    """判断段落是否为版式噪声（标签、英文小标、编号、署名、相关阅读）。"""
    t = txt.strip()
    if not t:
        return True
    if t in _DIGEST_LABELS:
        return True
    if re.fullmatch(r"[0-9]{1,2}", t):                    # 01 / 02 / 03
        return True
    if re.fullmatch(r"[A-Z0-9 &·/\-—]{1,40}", t):          # THE INVOICE / KEY FACTS
        return True
    if t.startswith("—") or t.startswith("——"):            # 引言卡署名
        return True
    if t.startswith("· "):                                # 相关阅读列表
        return True
    if "关注 Linux、开源与信创安全" in t:                   # 文末签名
        return True
    if re.match(r"^GNU COREUTILS", t, re.I):               # 连载条
        return True
    return False


def _truncate_units(text: str, limit: int) -> str:
    """按微信单位截断，优先在句末断句；硬截时不留半句尾巴。"""
    if _digest_units(text) <= limit:
        return _clean_tail(text)
    best, cur = "", 0
    for i, ch in enumerate(text):
        cur += 2 if ord(ch) > 0x7F else 1
        if cur > limit:
            break
        if ch in "。！？；":
            best = text[: i + 1]
    if best and _digest_units(best) >= limit * 0.6:
        return _clean_tail(best)
    out, cur = [], 0
    for ch in text:
        u = 2 if ord(ch) > 0x7F else 1
        if cur + u > limit:
            break
        out.append(ch)
        cur += u
    return _clean_tail("".join(out))


def _clean_tail(s: str) -> str:
    """清掉截断留下的尾段标点/破折号，避免摘要以「，」「——」收尾。"""
    s = s.strip()
    s = re.sub(r"[\s，、；：,“”‘’—-]+$", "", s)
    return s.strip()


def build_digest(content: str, is_html: bool = True, override: str = None) -> str:
    """从文章内容自动提取摘要，满足微信 120 字（240 半角单位）限制。

    优先级：--digest 覆盖 > HTML 内 <!-- digest: ... --> 标记 > 前言段落自动提取。
    提取时会跳过引言卡金句、目录卡、章节小标、文末签名等版式噪声，
    取正文「前言」为首选；过短时并入后续段落，最后按句末截断到安全长度。
    """
    if override and override.strip():
        return _truncate_units(_normalize_ws(override), DIGEST_SAFE_UNITS)

    if is_html:
        m = re.search(r"<!--\s*digest\s*:\s*(.*?)\s*-->", content, flags=re.S | re.I)
        if m:
            return _truncate_units(_normalize_ws(_html.unescape(m.group(1))), DIGEST_SAFE_UNITS)
        paras = _html_paragraphs(content)
        h1 = ""
    else:
        m = re.search(r"^\s*digest\s*[:：]\s*(.+)$", content, flags=re.M | re.I)
        if m:
            return _truncate_units(_normalize_ws(m.group(1)), DIGEST_SAFE_UNITS)
        h1 = _md_h1(content)
        paras = _markdown_paragraphs(content)

    # 跳过引言卡：定位 QUOTE 标签后的第一段金句及其「——」署名
    start = 0
    for i, p in enumerate(paras):
        if p.strip() == "QUOTE":
            start = i + 1
            break

    cands = []
    for p in paras[start:]:
        if _is_digest_noise(p):
            continue
        if p.startswith("—"):
            continue
        if _is_attribution(p):
            continue
        if h1 and p == h1:
            continue
        if _looks_like_title(p):
            continue
        cands.append(p)
    if start and cands:
        cands = cands[1:]  # 有 QUOTE 引言卡时，去掉卡内金句，保留其后正文

    chosen, total = [], 0
    for p in cands:
        if not chosen and _digest_units(p) < 20:
            continue  # 正文首段至少要像样
        chosen.append(p)
        total += _digest_units(p)
        if total >= DIGEST_MIN_UNITS:
            break

    if not chosen:
        # 兜底：整篇取首个足够长的段落
        for p in paras:
            if not _is_digest_noise(p) and _digest_units(p) >= 20:
                chosen = [p]
                break

    digest = _normalize_ws(" ".join(chosen))
    return _truncate_units(digest, DIGEST_SAFE_UNITS)


# ============================================================
# 微信公众号草稿箱 API
# ============================================================

def create_draft(access_token: str, title: str, html_content: str, _retry: int = 0,
                 author: str = "IT 禅悟",
                 source: str = "内容由 AI 生成",
                 is_original: bool = True,
                 need_open_advert: bool = False,
                 digest: str = None) -> dict:
    """新建草稿（带自动重试）。digest 为 None 时按文章内容自动提取。"""
    if not digest:
        digest = build_digest(html_content, is_html=True)

    if '<mp-style-type' not in html_content:
        html_content += '<p style="display:none"><mp-style-type data-value="10000"></mp-style-type></p>'

    article = {
        "title": title,
        "author": author,
        "content": html_content,
        "digest": digest,
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
        "need_open_advert": 1 if need_open_advert else 0,
    }

    if is_original:
        article["original_author_name"] = author
        article["original_article_url"] = ""
    thumb_id = os.environ.get("WECHAT_THUMB_MEDIA_ID")
    if thumb_id == "":
        # 明确跳过封面图
        pass
    elif thumb_id is None:
        thumb_id = DEFAULT_THUMB_MEDIA_ID
        if thumb_id and thumb_id.strip():
            article["thumb_media_id"] = thumb_id
    elif thumb_id and thumb_id.strip():
        article["thumb_media_id"] = thumb_id

    if is_original:
        print(f"📌 声明原创 (草稿阶段仅作记录, 发布时需要后台确认)", file=sys.stderr)
    print(f"✍️ 作者: {author}", file=sys.stderr)
    print(f"🤖 创作来源: {source}", file=sys.stderr)

    payload = {"articles": [article]}
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={access_token}"

    try:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        resp = requests.post(url, data=body, headers={'Content-Type': 'application/json; charset=utf-8'}, timeout=60)
        result = resp.json()

        if "media_id" in result:
            print(f"✅ 草稿创建成功！media_id: {result['media_id']}", file=sys.stderr)
            return result

        if result.get("errcode") == 40001 and _retry < 1:
            print(f"⚠️ access_token 失效，正在刷新...", file=sys.stderr)
            cache_file = Path.home() / ".openclaw" / "workspace" / "pipeline" / "config" / "access_token_cache.json"
            if cache_file.exists():
                cache_file.unlink()
            config = load_config()
            new_token = get_access_token(config["appid"], config["appsecret"])
            return create_draft(new_token, title, html_content, _retry=1)

        raise RuntimeError(f"创建草稿失败: {result}")

    except Exception as e:
        raise RuntimeError(f"创建草稿异常: {e}")


def get_draft(access_token: str, media_id: str) -> dict:
    url = f"https://api.weixin.qq.com/cgi-bin/draft/get?access_token={access_token}"
    try:
        resp = requests.post(url, json={"media_id": media_id}, timeout=30)
        # 微信响应无 charset，requests 会按 latin-1 兜底解码导致中文双重编码，强制 UTF-8
        return json.loads(resp.content.decode('utf-8'))
    except Exception as e:
        raise RuntimeError(f"获取草稿失败: {e}")


def list_drafts(access_token: str, offset: int = 0, count: int = 20) -> dict:
    url = f"https://api.weixin.qq.com/cgi-bin/draft/batchget?access_token={access_token}"
    try:
        resp = requests.post(url, json={"offset": offset, "count": count, "no_content": 1}, timeout=30)
        return resp.json()
    except Exception as e:
        raise RuntimeError(f"获取草稿列表失败: {e}")


def update_draft(access_token: str, media_id: str, title: str, html_content: str,
                 thumb_media_id: str = None, author: str = None, digest: str = None) -> dict:
    """修改草稿。digest 为 None 时按文章内容自动提取。"""
    digest = digest or build_digest(html_content, is_html=True)

    if '<mp-style-type' not in html_content:
        html_content += '<p style="display:none"><mp-style-type data-value="10000"></mp-style-type></p>'

    thumb = thumb_media_id or os.environ.get("WECHAT_THUMB_MEDIA_ID") or DEFAULT_THUMB_MEDIA_ID
    article = {
        "title": title,
        "content": html_content,
        "thumb_media_id": thumb,
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }
    if author:
        article["author"] = author
    if digest:
        article["digest"] = digest

    payload = {"media_id": media_id, "index": 0, "articles": article}
    url = f"https://api.weixin.qq.com/cgi-bin/draft/update?access_token={access_token}"

    try:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        resp = requests.post(url, data=body, headers={'Content-Type': 'application/json; charset=utf-8'}, timeout=60)
        return resp.json()
    except Exception as e:
        raise RuntimeError(f"修改草稿失败: {e}")


# ============================================================
# 主函数
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="微信公众号草稿箱 API 工具")
    parser.add_argument("action", choices=["create", "get", "list", "update", "config"],
                       help="操作类型")
    parser.add_argument("--title", "-t", help="文章标题")
    parser.add_argument("--media-id", "-m", help="草稿 media_id")
    parser.add_argument("--account", "-a", help="账号别名")
    parser.add_argument("--author", help="作者名")
    parser.add_argument("--thumb-media-id", help="封面图 media_id")
    parser.add_argument("--digest", help="摘要（不传则按文章内容自动提取，上限 120 字/240 半角单位）")
    parser.add_argument("--source", help="创作来源")
    parser.add_argument("--no-original", action="store_true", help="不声明原创")
    parser.add_argument("--no-advert", action="store_true", help="不开启广告")
    parser.add_argument("--no-markdown", action="store_true",
                       help="跳过 markdown → 微信HTML 转码，直接把 stdin 当作已排版的 HTML 发送（用于 gzh-design-skill 等自定义主题）")

    args = parser.parse_args()

    config = load_config(account=args.account)
    if not config["appid"] or not config["appsecret"]:
        print(f"❌ 未配置微信凭据！账号={args.account or '(default)'}", file=sys.stderr)
        sys.exit(1)

    access_token = get_access_token(config["appid"], config["appsecret"])

    if args.action == "config":
        from wechat_draft_api import list_accounts
        print(json.dumps({
            "current_account": args.account or "(default)",
            "appid": config["appid"],
            "available_accounts": list_accounts(),
        }, ensure_ascii=False, indent=2))
        return

    if args.action == "create":
        raw_content = sys.stdin.read()
        if not raw_content.strip():
            print("❌ 请在 stdin 提供 Markdown 或 HTML 内容", file=sys.stderr)
            sys.exit(1)

        title = args.title
        if not title:
            m = re.search(r'^# (.+)$', raw_content, re.MULTILINE)
            title = m.group(1).strip() if m else f"小龙女技术早报 {datetime.now().strftime('%Y-%m-%d')}"

        if args.no_markdown:
            # 直接把 stdin 当作已排版的 HTML 发送，跳过 markdown_to_wechat_html 转码
            html_content = raw_content.strip()
            print("⏭  跳过 markdown 转码，直接发送已排版 HTML（自定义主题）", file=sys.stderr)
        else:
            print("🔄 转换 Markdown → 微信HTML...", file=sys.stderr)
            html_content = markdown_to_wechat_html(raw_content)
        print(f"📏 HTML 长度: {len(html_content)} 字符", file=sys.stderr)

        if args.author:
            author = args.author
        elif args.account == "ZEN":
            author = "ZEN"
        else:
            author = "IT 禅悟"
        source = args.source or "内容由 AI 生成"
        is_original = not args.no_original

        if args.thumb_media_id is not None:  # allow empty string to skip
            os.environ["WECHAT_THUMB_MEDIA_ID"] = args.thumb_media_id or ""

        print(f"📝 创建草稿: {title}", file=sys.stderr)
        print(f"   账号: {args.account or '(default)'}", file=sys.stderr)
        print(f"   作者: {author}", file=sys.stderr)
        need_advert = not args.no_advert
        result = create_draft(access_token, title, html_content,
                              author=author, source=source, is_original=is_original,
                              need_open_advert=need_advert, digest=args.digest)

        output = {
            "status": "success",
            "title": title,
            "media_id": result.get("media_id"),
            "account": args.account or "default",
            "author": author,
            "created_at": datetime.now().isoformat(),
        }
        print(json.dumps(output, ensure_ascii=False))

    elif args.action == "get":
        if not args.media_id:
            print("❌ 请指定 --media-id", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(get_draft(access_token, args.media_id), ensure_ascii=False, indent=2))

    elif args.action == "list":
        print(json.dumps(list_drafts(access_token), ensure_ascii=False, indent=2))

    elif args.action == "update":
        if not args.media_id:
            print("❌ 请指定 --media-id", file=sys.stderr)
            sys.exit(1)
        raw_content = sys.stdin.read()
        title = args.title or f"小龙女技术早报 {datetime.now().strftime('%Y-%m-%d')}"
        if args.no_markdown:
            html_content = raw_content.strip()
            print("⏭  跳过 markdown 转码，直接发送已排版 HTML（自定义主题）", file=sys.stderr)
        else:
            html_content = markdown_to_wechat_html(raw_content)
        print(json.dumps(update_draft(access_token, args.media_id, title, html_content,
                                       thumb_media_id=args.thumb_media_id,
                                       author=args.author, digest=args.digest),
                          ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

