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

## 开启实时字幕
在腾讯云开通“语音识别”后，在服务器上自己编辑 `deploy/.env`，填 `TENCENT_ASR_APPID`、`TENCENT_ASR_SECRET_ID`、`TENCENT_ASR_SECRET_KEY`，然后 `docker compose up -d`（只重启 app）。`docker compose exec app python -m app.doctor` 应显示“实时字幕：已配置”。密钥不要发到聊天里。

## 排查“卡”
在 `deploy/.env` 里加 `LOOP_WATCH_S=2`，`docker compose up -d`；之后 `docker compose logs app | grep -E "slow request|event loop blocked"` 会列出慢请求（带线程池/连接池占用）和事件循环被堵时的代码栈。查完把它删掉。

## 升级
`git pull && docker compose up -d --build`（数据在卷 `appdata` 里，不会丢）。

## 监控与告警
`deploy/monitor.sh` 每 5 分钟检查一次：数据库、磁盘剩余、最近一次备份是否超过 36 小时、账号邮件是否堆积失败、视频服务能否连上、舆情来源是否全部出错；**状态变化时**才通知（出问题一条、恢复一条，不会每 5 分钟刷屏）。应用容器自己挂了时，由脚本直接发通知。

1. 在聊天工具里建一个机器人，拿到它的 webhook 地址（飞书、企业微信、Slack 都行，接收 `{"text": "..."}`）；填进 `deploy/.env`：`ALERT_WEBHOOK_URL=...`，然后 `docker compose up -d`。
2. 服务器上加定时：`crontab -e`，写一行 `*/5 * * * * /opt/total-mvp/deploy/monitor.sh >> /var/log/xingtong-monitor.log 2>&1`。
3. 另外在腾讯云监控或 UptimeRobot 加一条对 `https://你的域名/api/health` 的网站探测（整台服务器挂了的时候，上面这个脚本也跟着挂，只有外部探测能发现）。
4. `docker compose exec app python -m app.doctor` 里会显示“监控告警”是否已配置。

## 自动检查（CI）
`.github/workflows/ci.yml`：每次推送到 main 和每个拉取请求，GitHub 都会跑 `scripts/qa.sh`（逻辑测试、全站浏览器检查、真实视频访谈、一键启动），失败时把报告和截图作为附件留 7 天。**不会自动部署**，部署仍然是人工的一步。

## 还没做（上线给真实受访者之前）
Postgres、录像对象存储和异地备份（数据库已异地备份，录像还没有）、云端转写（或服务器上的 whisper）、录像合成放进受限环境、数据保护应急预案、一键回滚。
