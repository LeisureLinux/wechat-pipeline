# 微信公众号发布流水线脚本

LeisureLinux / ZEN 等公众号的双发发布工具集。负责把已排版的 HTML 推送到公众号**草稿箱**，以及封面图上传、签名区规整等辅助环节。

> **「发布」= 推到公众号后台草稿箱**，不是群发上线。群发/上线由人工在后台终审后操作。

## 依赖

```bash
pip install requests
```

## 凭据

脚本**不含任何密钥**。账号凭据从以下位置按顺序读取（见 `wechat_draft_api.py` 的 `load_config` 与 `_load_env_accounts`）：

1. `~/.codex/.env` — 形如 `<别名>_AppID` / `<别名>_AppSecret`
2. `~/.openclaw/.env`
3. `~/.openclaw/workspace/pipeline/config/env`
4. 环境变量 `WECHAT_APPID` / `WECHAT_APPSECRET`

当前可用别名：`LeisureLinux`、`ZEN`（以本机 `.env` 为准，用 `config` 动作可列出）。

access_token 缓存在 `~/.openclaw/workspace/pipeline/config/access_token_cache.json`（多环境自动探测，含 `~/.codex`、`/tmp/codex-wx-tokens` 兜底）。

## 脚本

| 文件 | 用途 |
|------|------|
| `wechat_draft_api.py` | 主工具：新建/更新/查询/删除草稿、自动维护 access_token、**自动提取摘要** |
| `wechat_upload_cover.py` | 上传封面图到素材库，返回 `thumb_media_id`（按账号隔离，不可跨账号复用） |
| `gzh_signature.py` | 统一补齐/规整石墨极简排版稿的尾部作者签名区（幂等） |

## 用法

```bash
# 新建草稿（已排版 HTML，不走 markdown 转码）
python3 wechat_draft_api.py create \
  --title '文章标题' --account LeisureLinux --author "老 徐" \
  --no-markdown --no-advert < 稿件.html

# 列表 / 详情 / 更新 / 删除
python3 wechat_draft_api.py list --account LeisureLinux
python3 wechat_draft_api.py get  --account LeisureLinux --media-id <id>
python3 wechat_draft_api.py update --account LeisureLinux --media-id <id> --title '新标题' < 稿件.html
python3 wechat_draft_api.py config --account LeisureLinux    # 查看当前账号与可用别名
```

## 摘要（digest）

不传 `--digest` 时，`create`/`update` 会**自动从文章内容提取摘要**（`build_digest()`），跳过引言卡、目录卡、英文小标、文末签名、译文来源等版式噪声，取前言段落并按句末断句截断。

**微信长度上限**：官方文档写「不超过 120 个字」，实测口径是 **240 个半角单位：非 ASCII 记 2、ASCII 记 1**。

| 输入 | 结果 |
|---|---|
| 纯中文 120 字 | ✅ |
| 纯中文 121 字 | ❌ `45004 description size out of limit` |
| 纯 ASCII 240 字符 | ✅ |
| 纯 ASCII 241 字符 | ❌ |
| 100 中文 + 40 ASCII（=240） | ✅ |
| 100 中文 + 41 ASCII（=241） | ❌ |

即纯中文封顶 120 字、纯英文 240 字符。生成时截断到 232 单位留余量。

优先级：`--digest` 覆盖 → HTML 内 `<!-- digest: ... -->` 标记 → 自动提取。
不填 digest 字段时，微信会默认抓正文前 54 字（会把版式标签一起吃进去，故不建议省略）。

## ⚠️ 批量删除草稿的铁律

**必须用 `media_id` 精确匹配，绝不能用标题关键词/前缀模糊匹配。**

2026-09-16 教训：清理压测产生的测试草稿时，用了 `"摘要" in title or startswith(前缀)` 的宽泛过滤，误删 7 篇正式草稿（当天发布的文章 + coreutils 连载），草稿箱 11 → 5。

正确做法：

1. 先 `batchget` 列出，**打印命中项的 title + author 供人工核对**
2. 再按 `media_id` 逐个删
3. `batchget` 分页的 `offset` 会因删除而位移，循环里应重新取第 0 页

恢复方式：源 HTML 都在本地（`lore/gzh/<slug>/` 或 `wechat-articles/`），用原始标题重新 `create` 即可（草稿未发布，重建无副作用）。

## 已知怪癖

- **草稿列表返回的中文标题是乱码**（微信返回 UTF-8，但 HTTP header 是 `text/plain`，`requests` 用 latin-1 误读）。这是正常现象，以 `media_id` 核对即可，**别误判失败重发**。
  正确读法：`json.loads(resp.content.decode('utf-8', errors='replace'))`
- `batchget` 单页最多返回 20 条，需翻页。

## 相关仓库

- [`LeisureLinux/lore`](https://github.com/LeisureLinux/lore) — freelamp.com 站点源 + 公众号排版稿存档（`gzh/<slug>/`）

## 作者

**LeisureLinux** — albertxu@freelamp.com
