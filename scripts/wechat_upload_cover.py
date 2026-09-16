#!/usr/bin/env python3
"""上传公众号封面图到指定账号的永久素材库，返回 thumb_media_id。

wechat_draft_api.py 没有 upload 动作，本脚本补齐这一环：
    python3 wechat_upload_cover.py <图片.jpg> <账号别名>

账号别名与 ~/.codex/.env 里的 <别名>_AppID / <别名>_AppSecret 对应。
封面 media_id 按账号隔离，跨账号不可复用。
"""

import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from wechat_draft_api import load_config, get_access_token  # noqa: E402


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    img_path, account = sys.argv[1], sys.argv[2]
    if not os.path.isfile(img_path):
        print(f"✗ 找不到图片: {img_path}", file=sys.stderr)
        sys.exit(1)

    cfg = load_config(account=account)
    if not cfg.get("appid"):
        print(f"✗ 账号 {account} 未配置凭据", file=sys.stderr)
        sys.exit(1)
    token = get_access_token(cfg["appid"], cfg["appsecret"])

    url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={token}&type=image"
    with open(img_path, "rb") as f:
        resp = requests.post(url, files={"media": (os.path.basename(img_path), f, "image/jpeg")}, timeout=60)
    out = resp.json()
    if out.get("media_id"):
        print(json.dumps({
            "status": "success",
            "account": account,
            "file": img_path,
            "media_id": out["media_id"],
            "url": out.get("url", ""),
        }, ensure_ascii=False))
    else:
        print(json.dumps({"status": "failed", "account": account, "errcode": out.get("errcode"),
                          "errmsg": out.get("errmsg")}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
