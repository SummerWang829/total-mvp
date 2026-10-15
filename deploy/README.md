# 部署（测试服务器：腾讯云新加坡轻量应用服务器）

一台服务器跑三个容器：`app`（网站）、`livekit`（视频服务器）、`caddy`（自动 https）。**这套只用于内部测试**：数据库是 SQLite，转写默认是演示模式。

## 需要的前提
- 服务器：Ubuntu，已装 Docker（用系统软件源，不跑网上的安装脚本）：`sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2`。
- 两个域名都解析到服务器公网 IP（A 记录）：`APP_HOST` 和 `LK_HOST`（见 `.env.example`）。
- 腾讯云轻量服务器**防火墙**放行：`TCP 80、443`、`UDP 443`、`TCP 7881`、`UDP 50000-50200`（22 默认已开）。

## 步骤
1. 把仓库代码放到服务器（`/opt/total-mvp`）。
2. `cd deploy && cp .env.example .env`，在服务器上用 `openssl rand -hex 32` 生成 `SECRET_KEY`、`openssl rand -hex 24` 生成 `LIVEKIT_API_SECRET`，填上模型密钥。
3. `docker compose up -d --build`；第一次启动 Caddy 会自动申请证书（域名没解析好会失败，解析好后 `docker compose restart caddy`）。
4. 打开 `https://<APP_HOST>`，注册第一个管理员；然后把 `.env` 里 `ALLOW_SIGNUP` 改成 `false`，`docker compose up -d`。
5. 检查：`docker compose ps`、`curl https://<APP_HOST>/api/health`、`docker compose logs --tail=50 livekit`。

## 升级
`git pull && docker compose up -d --build`（数据在卷 `appdata` 里，不会丢）。

## 还没做（上线给真实受访者之前）
Postgres、录像对象存储/备份、限流、云端转写（或服务器上的 whisper）、录像合成放进受限环境、数据保护应急预案。
