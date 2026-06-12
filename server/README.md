# 交易助手 · 授权服务器

用于审核用户、下发授权、接收交易上报。部署到阿里云 ECS 后，将客户端 `license_server_url` 指向此服务。

## 本地试运行

```bat
cd server
run.bat
```

- 管理后台：http://127.0.0.1:8787/admin  
- 管理员密码需先生成哈希并写入 `TA_ADMIN_PASSWORD_HASH`

## 环境变量

- `TA_ADMIN_PASSWORD_HASH`：管理后台密码哈希，用 `python scripts/hash_admin_password.py` 生成
- `TA_JWT_SECRET`：JWT 签名密钥，必须使用至少 32 字符的随机字符串
- `TA_JWT_EXPIRE_HOURS`：设备 Token 有效期，默认 24 小时
- `TA_DB_PATH`：SQLite 数据库路径，默认 `data/license.db`
- `TA_HOST` / `TA_PORT`：监听地址，默认 `127.0.0.1:8787`

## 阿里云 ECS 部署（简要）

1. 购买 ECS（1核2G 即可），开放安全组 **8787**（或 443 反代）
2. 安装 Python 3.9+，上传 `server/` 目录
3. 生成管理员密码哈希，设置环境变量并启动：

```bash
python scripts/hash_admin_password.py
export TA_ADMIN_PASSWORD_HASH='上一步输出的哈希'
export TA_JWT_SECRET='至少32字符的随机长字符串'
export TA_DB_PATH='/var/lib/tradeassistant/license.db'
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8787
```

4. 建议用 **Nginx + HTTPS** 反代，客户端填 `https://你的域名`
5. 用 systemd 或 supervisor 保活进程

## API 概览

- `POST /api/v1/register` — 客户端提交申请
- `POST /api/v1/heartbeat` — 心跳 / 续期
- `POST /api/v1/trades/batch` — 上报交易（需 Bearer Token）
- `GET /admin` — 管理页面

## 数据备份

定期备份 `TA_DB_PATH` 指向的 SQLite 文件即可。
