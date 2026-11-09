# 部署指南：从零把项目搬上服务器

> 面向：第一次在 Linux 服务器上部署 Python Web 服务的人。
> 每一步写三件事：**做什么**、**为什么这么做**、**不做会怎样**。
> 环境：Ubuntu 24.04 LTS，2 vCPU / 1 GiB 内存（已加 2 GB swap），Python 3.12。

---

## 先理解要搭的是什么

```
浏览器 ──► Caddy（80/443：鉴权、HTTPS、转发）──► 程序（127.0.0.1:8000）
                                                     ▲
                                          systemd（启动、守护、开机自启）
```

四层各管一件事，出问题时能立刻判断是哪一层：

| 症状 | 去看 |
| --- | --- |
| 网页 502 / 转圈 | `systemctl status tech-chat` + `journalctl -u tech-chat` |
| 网页打不开、连接被拒 | `systemctl status caddy`、防火墙 |
| 能登录但回答报错 | 程序日志里的 Traceback |

---

## 第 1 步：登录服务器

```bash
ssh azure
```

本机 `~/.ssh/config` 里配了别名，等价于 `ssh -i <密钥> magcrisp@<IP>`。

**为什么**：后面所有操作都在服务器上执行。

---

## 第 2 步：更新系统、装基础软件

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip git
```

**为什么**：
- `apt update` 刷新软件源索引；不刷新可能装到旧版本或报「找不到包」。
- **`python3-venv` 是 Ubuntu 的坑**：系统自带的 Python 不含 venv 模块，不装会报 `ensurepip is not available`。第一次建虚拟环境失败就是这个原因。
- `git` 用于拉取代码。

**不做会怎样**：第 5 步建虚拟环境直接失败。

---

## 第 3 步：加交换空间（小内存机器必需）

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
echo "vm.swappiness=10" | sudo tee /etc/sysctl.d/99-swappiness.conf
sudo sysctl -p /etc/sysctl.d/99-swappiness.conf
```

**为什么**：
- 实测服务常驻 450～500 MB（Python + torch + 向量矩阵），而机器只有 887 MB 内存，不开 swap 会被 OOM Killer 杀掉。
- **写进 `/etc/fstab` 是为了重启后自动挂载**，否则重启一次 swap 就没了。
- `swappiness=10`：内存够用时尽量不换出。默认 60 会让系统过早使用 swap，每次请求都卡一下。

**验证**：`swapon --show` 应显示 `/swapfile 2G`。

---

## 第 4 步：拉代码

```bash
git clone https://github.com/MagCrisps/tech-chat.git ~/tech-chat
cd ~/tech-chat
```

**为什么用 git clone 而不是 scp 整个文件夹**：
- 以后更新只需 `git pull`。
- 仓库里只有代码，`.gitignore` 已排除 `.env`、`data/`、`storage/`，不会带上密钥和数据。

---

## 第 5 步：建虚拟环境

```bash
cd ~/tech-chat
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
```

**为什么用虚拟环境**：系统 Python 是多个软件的公共依赖，直接 `pip install` 可能破坏 apt 管理的库版本。

**为什么后面都用 `.venv/bin/python` 完整路径**：systemd 不认识「激活虚拟环境」，它只能执行绝对路径的可执行文件。手动跑和 systemd 跑用同一写法，出问题时行为一致。

---

## 第 6 步：装依赖（顺序很重要）

```bash
# ① 先单独装 CPU 版 torch
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu

# ② 再装其余依赖
.venv/bin/pip install -r requirements-lock.txt
```

**为什么 torch 要单独装并指定源**：
- PyPI 上默认的 torch 是 **CUDA 版**，带几 GB 的 GPU 库；这台机器没有 GPU，纯属浪费磁盘和时间。
- `download.pytorch.org/whl/cpu` 是官方 CPU 专用源（约 200 MB）。
- 顺序反了，`pip install -r` 会先按默认源装一遍 CUDA 版，再装一遍，容易冲突。

**验证**：
```bash
.venv/bin/python -c "import fastapi, langgraph, chromadb, sentence_transformers, lxml; print('依赖 OK')"
```

---

## 第 7 步：准备数据（**在本地建好，只传结果**）

索引和嵌入模型不在仓库里，需要从开发机上传。

```bash
# 本地（Windows / Git Bash）
cd /e/WorkPlace_python/PythonProject/tech-chat
tar -czf deploy_assets.tgz -C storage paper_index_mini -C /e/WorkPlace_python/proDoubaoPic all-MiniLM-L6-v2
scp deploy_assets.tgz azure:~/

# 服务器
cd ~/tech-chat && mkdir -p storage models
tar -xzf ~/deploy_assets.tgz -C /tmp/
mv /tmp/paper_index_mini storage/
mv /tmp/all-MiniLM-L6-v2 models/
rm ~/deploy_assets.tgz
```

| 目录 | 作用 | 是否必需 |
| --- | --- | --- |
| `storage/paper_index_mini/` | 论文索引（SQLite 全文索引 + 向量矩阵，约 66 MB） | 必需 |
| `models/all-MiniLM-L6-v2/` | 把查询编码成向量的模型（约 88 MB） | 必需 |
| `datasets/ccf_a_mini/` | 原始数据集，只在重建索引时用 | 不必传 |

**为什么不在服务器上建索引**：建索引要算 2 万个向量，本机约 10 分钟；这台 1 GB 的突发型机器会被吃满内存和 CPU，可能中途被杀。索引结果与机型无关，**在快的地方建好，只传结果**。

**模型为什么也要传**：`.env` 配置为从本地路径加载（`EMBEDDING_USE_LOCAL=true`），目录不存在会启动即报 `FileNotFoundError`。

---

## 第 8 步：写 `.env`

```bash
cp .env.example .env
nano .env
chmod 600 .env
```

必须与开发机不同的配置：

| 配置 | 值 | 为什么 |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | 只监听本机；对外由 Caddy 转发，Python 不直接暴露 |
| `PORT` | `8000` | 与 Caddy 的转发目标一致 |
| `OPEN_BROWSER` | `false` | 服务器没有图形界面 |
| `ALLOW_UPLOAD` | `false` | 关闭文档上传，防止别人往服务器塞文件 |
| `EMBEDDING_USE_LOCAL` | `true` | 用第 7 步上传的本地模型，不去 HuggingFace 下载 |
| `EMBEDDING_LOCAL_PATH` | `/home/magcrisp/tech-chat/models/all-MiniLM-L6-v2` | 绝对路径 |
| `PAPER_INDEX_DIR` | `./storage/paper_index_mini` | 指向小数据集索引 |
| `CHECKPOINT_DB` | `./storage/checkpoints.sqlite` | 会话记忆，重启不丢 |

**`chmod 600` 为什么必须**：文件含 API Key，默认权限下同机其他用户可读。

**检查有没有重复键**（重复键哪个生效取决于解析顺序，很容易「明明改了却没生效」）：
```bash
grep -nE "^(OPENAI_|LLM_MODEL)" .env | cut -c1-45
```

---

## 第 9 步：先手动跑一次（**不要跳**）

```bash
cd ~/tech-chat
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

依次应看到：`会话记忆已就绪` → `LangGraph 状态图已编译完成` → `加载本地 Embedding 模型` → `论文索引已加载：N 篇` → `Application startup complete`（约 20～30 秒）。

另开终端验证：

```bash
curl 127.0.0.1:8000/health
curl "127.0.0.1:8000/api/papers/search?q=graph+neural+network&k=3"
```

**为什么必须先手动跑通**：systemd 里失败的报错要绕一层 `journalctl` 才能看到；手动跑能直接看到堆栈。先有一个「已知能工作」的状态，后面出问题才能确定是 systemd 或 Caddy 的配置问题，而不是代码问题。

跑通后 `Ctrl+C` 停掉。**注意**：手动跑的时候 systemd 那份必须停掉，否则端口冲突（`address already in use`）。

---

## 第 10 步：交给 systemd

```bash
sudo nano /etc/systemd/system/tech-chat.service
```

```ini
[Unit]
Description=CCF-A 论文搜索助手
After=network-online.target
Wants=network-online.target

[Service]
User=magcrisp
WorkingDirectory=/home/magcrisp/tech-chat
ExecStart=/home/magcrisp/tech-chat/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
Environment=PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
Restart=on-failure
RestartSec=5
MemoryMax=800M

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/magcrisp/tech-chat/storage /home/magcrisp/tech-chat/data

[Install]
WantedBy=multi-user.target
```

| 配置 | 作用 | 不写会怎样 |
| --- | --- | --- |
| `User=magcrisp` | 用普通用户运行 | 出漏洞时攻击者直接拿到 root |
| `WorkingDirectory` | `.env`、相对路径的基准目录 | 找不到 `.env`，报「未配置 OPENAI_API_KEY」 |
| `--workers 1` | 只开一个进程 | 多进程各自加载模型和索引，内存翻倍直接 OOM |
| `--no-access-log` | 关闭逐请求访问日志 | 日志刷屏，真正的报错被淹没 |
| `Restart=on-failure` | 崩溃后自动拉起 | 进程一死网站一直 502，直到人工发现 |
| `MemoryMax=800M` | 内存上限 | 一个服务吃满内存，SSH 都登不进 |
| `ProtectSystem=strict` + `ReadWritePaths` | 文件系统只读，只允许写数据目录 | 程序被利用后可改系统文件 |
| `WantedBy=multi-user.target` | 开机自启 | 服务器一重启，网站就没了 |

启用：

```bash
sudo systemctl daemon-reload        # 改了 unit 文件后必须执行
sudo systemctl enable --now tech-chat
systemctl status tech-chat
```

**`daemon-reload` 最容易忘**：新建或修改 `.service` 后不执行它，`start` 仍按旧配置跑，或报「找不到服务」。

---

## 第 11 步：装 Caddy 做反向代理

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

**为什么需要反向代理，不能直接对外开 8000**：
1. **HTTPS**：Caddy 自动申请续期证书，Python 服务做不到。
2. **鉴权**：密码在 Caddy 层校验，程序代码不用改。
3. **隔离**：Python 只听 127.0.0.1，外网碰不到。
4. **流式**：可配置不缓冲，保证回答逐字输出。

配置 `/etc/caddy/Caddyfile`：

```
magcrisp.duckdns.org {
	encode gzip

	# 健康检查免密，方便监控
	@health path /health
	handle @health {
		reverse_proxy 127.0.0.1:8000
	}

	handle {
		# 鉴权由应用自身的登录功能负责（见「附录 A：用户与登录」），
		# Basic Auth 已关闭 —— 两套同时开会弹两次密码框。
		# 上传入口双层关闭（第一道）
		@upload path /api/upload
		respond @upload 403
		reverse_proxy 127.0.0.1:8000 {
			# 流式输出必须立即转发
			flush_interval -1
		}
	}

	request_body {
		max_size 64KB
	}

	header {
		X-Content-Type-Options nosniff
		-Server
	}
}
```

要点：
- 写域名 → Caddy 自动申请 Let's Encrypt 证书并把 http 跳转到 https；**没有域名时写 `:80`**。
- `flush_interval -1` **必须写**：不写会把响应攒起来，流式回答变成「等几十秒后一次性出现」。
- 若暂时不想做登录功能，也可以用 Caddy 的 `basic_auth` 顶替（`caddy hash-password` 生成哈希，不要写明文）。但应用已有登录功能时**不要同时启用**，否则会弹两次密码框。
- `@upload → 403` 与程序里的 `ALLOW_UPLOAD=false` 形成两层（第二道在后端）。

应用：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile   # 先验证
sudo systemctl reload caddy                         # 热加载，不断开现有连接
```

---

## 第 12 步：防火墙

```bash
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw default deny incoming
sudo ufw enable
```

**为什么**：Azure 的网络安全组是第一道门，ufw 是机器自己的第二道门。

**顺序很重要**：先 `allow 22` 再 `enable`。反过来会当场断开 SSH。

---

## 第 13 步：验证清单

从**外部**逐条测：

```bash
curl -o /dev/null -w "%{http_code}\n" https://<域名>/                      # 200：页面可匿名打开
curl https://<域名>/health                                                # 免密可达
curl -o /dev/null -w "%{http_code}\n" "https://<域名>/api/papers/search?q=test"   # 401：接口要求登录
curl -c /tmp/c -X POST https://<域名>/api/auth/login \
     -H 'Content-Type: application/json' \
     -d '{"username":"<用户名>","password":"<密码>"}'                     # 200 且下发 Cookie
curl -b /tmp/c -o /dev/null -w "%{http_code}\n" "https://<域名>/api/papers/search?q=test"   # 200：登录后可检索
```

浏览器完整走一遍：匿名能看到界面 → 点发送弹登录框 → 登录后自动发出那条问题 → 看到逐字流式回答 → 刷新页面历史还在 → 退出登录。

**为什么每条都要测**：部署失败常常是「大部分对、某一项静默失效」。

---

## 日常运维速查

| 场景 | 命令 |
| --- | --- |
| 启动 / 停止 / 重启 | `sudo systemctl start / stop / restart tech-chat` |
| 看状态 | `systemctl status tech-chat` |
| 最近日志 | `sudo journalctl -u tech-chat -n 50` |
| 实时日志 | `sudo journalctl -u tech-chat -f` |
| 更新代码 | `cd ~/tech-chat && git pull && sudo systemctl restart tech-chat` |
| 改 Caddy 配置 | `sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy` |
| 内存 | `free -h` |
| 各服务资源 | `systemd-cgtop` |
| 添加用户 | `cd ~/tech-chat && .venv/bin/python -m app.users add <用户名>` |
| 列出用户 | `.venv/bin/python -m app.users list` |
| 改密码 / 停用 / 删除 | `.venv/bin/python -m app.users passwd\|disable\|enable\|delete <用户名>` |
| 解封 IP | `sudo fail2ban-client set caddy-auth unbanip <IP>` |

**排查顺序**：`systemctl status` → `journalctl` 最后几十行 → Caddy 日志 → 防火墙。

---

## 常见问题

**`ensurepip is not available`**
缺 `python3-venv`，见第 2 步。

**`No module named 'fastapi'`**
用的是系统 Python，不是 `.venv/bin/python`。

**`address already in use`**
已有进程占用 8000。`sudo ss -tlnp | grep 8000` 查出 PID，`sudo kill <PID>`，或先 `sudo systemctl stop tech-chat`。

**网页 502**
程序没起来。`systemctl status tech-chat` 看是否 active，`journalctl` 找 Traceback。

**流式回答变成一次性出现**
Caddyfile 里漏了 `flush_interval -1`。

**改了配置没生效**
- 改 `.service` → 需要 `daemon-reload` 再 `restart`。
- 改 `.env` → 需要 `restart`。
- 改 `Caddyfile` → 需要 `validate` 再 `reload`。

**服务反复重启**
多半是内存超限。`journalctl` 里搜索 `Killed`，或 `free -h` 看 swap 使用。可调大 `MemoryMax` 或减少并发。

**登录接口返回 503「系统还没有任何用户」**
还没建账号。用 `.venv/bin/python -m app.users add <用户名>` 添加，见附录 A。

**改了前端，浏览器还是旧界面**
静态资源已设 `Cache-Control: no-cache`，正常刷新即可生效；若浏览器仍缓存旧版，按 `Ctrl+Shift+R` 强制刷新一次。

**打开页面提示「历史记录加载失败」**
旧版前端的问题：未登录时 `/api/history` 返回 401 被当作错误。当前版本未登录不再请求历史，更新代码后强制刷新即可。

---

## 不要做的事

- **不要在服务器上重建索引**（内存不够，见第 7 步）。
- **不要把 `.env` 提交到 git**（`.gitignore` 已排除，别强行 `git add -f`）。
- **不要用 `--workers` 大于 1**（每份进程各加载一份模型，内存翻倍）。
- **不要在公网关闭 `ALLOW_UPLOAD` 以外什么都不做**（没有鉴权就等于把 API 额度公开）。
- **不要同时开 Caddy 的 Basic Auth 和应用登录**（会弹两次密码框）。

---

## 附录 A：用户与登录

系统**不开放注册**，账号只能在服务器上用命令行添加，这样只有持有服务器权限的人才能决定谁能用。

### 创建账号

```bash
cd ~/tech-chat
.venv/bin/python -m app.users add hr      # 回车后输入两次密码（至少 8 位）
.venv/bin/python -m app.users list        # 确认
```

### 常用命令

| 操作 | 命令 |
| --- | --- |
| 添加用户 | `python -m app.users add <用户名>` |
| 修改密码（旧会话立即失效） | `python -m app.users passwd <用户名>` |
| 停用（立即踢下线） | `python -m app.users disable <用户名>` |
| 重新启用 | `python -m app.users enable <用户名>` |
| 删除 | `python -m app.users delete <用户名>` |
| 列出 | `python -m app.users list` |

（命令前加 `.venv/bin/`，或在项目目录 `source .venv/bin/activate` 之后直接 `python -m app.users ...`）

### 行为说明

- **页面可以匿名浏览**，点击发送时才弹登录框；登录成功后自动把刚才那条问题发出去。
- 登录状态用 HttpOnly + SameSite=Lax 的 Cookie，有效期由 `SESSION_TTL_DAYS` 控制（默认 7 天）。
- 密码存 bcrypt 哈希，会话只存 token 的 SHA-256，数据库泄露也拿不到可用凭据。
- 同一用户名 15 分钟内失败 10 次，锁定 15 分钟。
- 改密码、停用账号会立即让该用户所有会话失效。
- **当前所有账号看到的是同一份会话历史**（会话尚未按用户隔离），知道会话 ID 即可读取。需要按人隔离时，要把会话与用户名绑定，属于后续工作。

### 相关接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/auth/login` | `{"username","password"}` → 下发会话 Cookie |
| `POST` | `/api/auth/logout` | 删除服务端会话并清 Cookie |
| `GET` | `/api/auth/me` | 返回 `{"logged_in","username"}` |

`/api/chat`、`/api/history`、`/api/papers/*` 要求登录；`/health` 保持公开。
