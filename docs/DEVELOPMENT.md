# BookToVoice 开发文档

> 面向开发 / 运维人员的技术文档。如果你只是想**部署并使用**本服务，请看 [README](../README.md)。
>
> 本文涵盖：架构、代码结构、本地开发、镜像构建与离线部署、运维命令、REST API、CI。

---

## 目录

- [架构](#架构)
- [目录结构](#目录结构)
- [本地开发](#本地开发)
- [验证部署](#验证部署)
- [打包 + 部署（离线镜像）](#打包--部署离线镜像)
  - [前置条件](#前置条件)
  - [1. 开发机构建并导出镜像](#1-开发机构建并导出镜像)
    - [方式一：NAS 与开发机同架构（x86\_64）](#方式一nas-与开发机同架构x86_64)
    - [方式二：在 Windows 上打包 ARM64 镜像（给 RK3588 等 ARM NAS）](#方式二在-windows-上打包-arm64-镜像给-rk3588-等-arm-nas)
  - [2. 传到 NAS](#2-传到-nas)
  - [3. NAS 导入镜像](#3-nas-导入镜像)
  - [4. 用 compose 启动](#4-用-compose-启动)
  - [5. 访问](#5-访问)
  - [更新镜像（改代码后）](#更新镜像改代码后)
  - [常用运维命令](#常用运维命令)
- [REST API](#rest-api)
- [CI / GitHub Actions](#ci--github-actions)

---

## 架构

```
                ┌───────────────┐
   浏览器 ──►   │  Book Service │ ── 内置 edge-tts 库 ──► 微软 Edge TTS
  :3033 (UI)    │  FastAPI      │
                │  SQLite+Worker│
                └───────────────┘
                     │  自己的 volume
                     ▼
            output/<书名>/第0001章_xxx.mp3
```

- **单容器**：不依赖 EasyVoice，不需要共享 Docker 网络。
- 串行 worker 逐章合成；失败指数退避重试（最多 3 次）；**目标 mp3 已存在则自动跳过**（文件级断点续传）。
- 上传 TXT 自动识别编码（utf-8/gb18030/big5…）。

---

## 目录结构

```
bookToVoice/
├─ docker-compose.yml     # 单容器 book-service
├─ Dockerfile
├─ requirements.txt       # 含 edge-tts
├─ tests/
│  └─ test_smoke.py       # 冒烟测试（parser/db，无需第三方库）
└─ app/
   ├─ main.py             # FastAPI：页面 + API + 下载
   ├─ config.py           # 环境变量配置
   ├─ db.py               # SQLite schema + CRUD
   ├─ parser.py           # TXT → 章节；多编码 decode_bytes
   ├─ tts.py              # 直连 edge-tts 合成
   ├─ worker.py           # 串行 worker + 重试 + 断点续跑
   ├─ templates/          # base/index/new_book/book_detail
   └─ static/             # style.css + app.js（进度轮询）
```

运行时自动生成（挂载目录）：`input/`、`output/`、`data/`。

---

## 本地开发

```bash
python -m venv .venv 
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

运行冒烟测试（仅测 parser/db，不依赖第三方库）：

```bash
python tests/test_smoke.py          # 直接运行
# 或
pip install pytest && pytest tests/ # 用 pytest
```

启动开发服务器：

```bash
uvicorn app.main:app --reload --port 3033
```

---

## 验证部署

1. **容器在跑**：`docker compose ps`，book-service 为 `running`。
2. **Web UI 存活**：浏览器打开 `http://<NAS_IP>:3033/`，能看到「我的书架」。
3. **edge-tts 可用**：进容器跑一句合成——
   ```bash
   docker exec -it book-service sh
   python -c "import asyncio,edge_tts; asyncio.run(edge_tts.Communicate('测试一下','zh-CN-XiaoxiaoNeural').save('/tmp/t.mp3'))" && ls -l /tmp/t.mp3
   ```
   生成 `/tmp/t.mp3` 且大小 > 0 即网络与 edge-tts 正常。
4. **端到端**：上传一本小 TXT，详情页看到进度推进，`output/<书名>/` 下出现 `第0001_*.mp3`。

---

## 打包 + 部署（离线镜像）

> 适用于不能在 NAS 上 `docker build`、只能导入 `.tar/.tar.gz` 镜像的环境。
> 流程：**开发机构建 → 导出 tar.gz → 传到 NAS → 导入 → compose 启动**。

### 前置条件

- **NAS**：已安装 Docker 与 Docker Compose（群晖装 Container Manager 即含）；只能 `docker load` 导入镜像、不能 build 也没关系。
- **开发机**（Windows/Mac/Linux）：装有 Docker，用于构建并导出镜像。
- NAS 端口 `3033` 可用。
- 容器需能访问外网（连微软 Edge TTS 服务）。

### 1. 开发机构建并导出镜像

> **先确认目标 NAS 的 CPU 架构**——在 NAS 上执行 `uname -m`：
> - 输出 `x86_64` → 开发机与 NAS 同架构，用**方式一**（默认构建）。
> - 输出 `aarch64` / `arm64`（如 **RK3588 / RK3588C**、树莓派 4/5）→ 用**方式二**（跨架构构建）。

#### 方式一：NAS 与开发机同架构（x86_64）

开发机和 NAS 都是 Intel/AMD 时，用默认构建即可：

```bash
cd D:\code\pycharmProject\bookToVoice

# 构建（默认构建本机架构）
docker build -t booktovoice:latest .

# 导出为 tar.gz（压缩，体积约为镜像的 40-60%）
docker save booktovoice:latest | gzip > booktovoice.tar.gz
```

#### 方式二：在 Windows 上打包 ARM64 镜像（给 RK3588 等 ARM NAS）

开发机是 x86、NAS 是 ARM64 时，`docker build` 默认只会构建出 amd64 镜像，ARM NAS 导入后会报「**不支持的架构格式** / `exec format error`」。需用 **buildx 跨架构构建**（Docker Desktop 自带 QEMU，可直接交叉构建）：

```bash
cd D:\code\pycharmProject\bookToVoice

# 1. 创建并启用 buildx 构建器（只需一次）
docker buildx create --use --name armbuilder
docker buildx inspect --bootstrap

# 2. 交叉构建 arm64 镜像，直接导出成 tar，再 gzip（产物名与方式一一致）
docker buildx build --platform linux/arm64 -t booktovoice:latest -o type=docker,dest=booktovoice.tar .
docker buildx build --pull=false --platform linux/arm64 -t booktovoice:latest -o type=docker,dest=booktovoice.tar .

gzip booktovoice.tar
```

两种方式最终都得到 `booktovoice.tar.gz`（约 200-300MB），后续「传到 NAS → 导入 → 启动」步骤完全相同。

> 依赖（fastapi / uvicorn / jinja2 / edge-tts）全是纯 Python，均有 arm64 预编译 wheel，跨架构构建不会卡在 C 编译上。
> 不压缩（.tar）：方式一用 `docker save -o booktovoice.tar booktovoice:latest`；方式二去掉最后的 `gzip` 即可（直接产出 `booktovoice.tar`），文件更大但导入略快。

### 2. 传到 NAS

把以下**两个**文件传到 NAS 同一目录（例如 `/volume2/SSD/docker/bookToVoice/`）：

- `booktovoice.tar.gz`（镜像包）
- `docker-compose.yml`

> 不需要传 `app/`、`Dockerfile`、`requirements.txt`——它们都已打进镜像。
> `input/`、`output/`、`data/` 不用传，compose 启动时会自动创建。

### 3. NAS 导入镜像

```bash
cd /volume2/SSD/docker/bookToVoice
docker load -i booktovoice.tar.gz
```

确认已导入：

```bash
docker images | grep booktovoice
# 应看到：booktovoice   latest   <image_id>   ...
```

### 4. 用 compose 启动

`docker-compose.yml` 完整内容（第 2 步已随镜像包一起传到 NAS）：

```yaml
services:
  book-service:
    image: booktovoice:latest
    container_name: book-service
    ports:
      - 3033:3033
    volumes:
      - /volume2/SSD/docker/bookToVoice/input:/app/input
      - /volume2/SSD/docker/bookToVoice/output:/app/output
      - /volume2/SSD/docker/bookToVoice/data:/app/data
    restart: always
```

启动：

```bash
docker compose up -d
```

> `image: booktovoice:latest` 直接用上一步导入的镜像，**不会 build**。

查看状态：`docker compose ps`（book-service 为 `running`）。

### 5. 访问

浏览器打开 `http://<NAS_IP>:3033/`（`<NAS_IP>` 替换为 NAS 局域网 IP）。

### 更新镜像（改代码后）

开发机重新构建 + 导出 → 覆盖传到 NAS → 重新导入 → 重启：

```bash
# 开发机（x86 NAS 用方式一，ARM NAS 用方式二的 buildx 命令）
docker build -t booktovoice:latest .
docker save booktovoice:latest | gzip > booktovoice.tar.gz

# NAS（传完文件后）
docker load -i booktovoice.tar.gz
docker compose up -d        # 检测到镜像更新会重建容器
```

> ARM NAS（RK3588 等）更新时同样要改用**方式二**的 `docker buildx build --platform linux/arm64 ...` 跨架构重新构建，否则又会导成 amd64 导致容器起不来。

### 常用运维命令

| 操作         | 命令                                  |
| ------------ | ------------------------------------- |
| 查看实时日志 | `docker compose logs -f book-service` |
| 查看状态     | `docker compose ps`                   |
| 停止         | `docker compose down`                 |
| 重启         | `docker compose restart book-service` |
| 进入容器     | `docker exec -it book-service sh`     |
| 删除镜像     | `docker rmi booktovoice:latest`       |

---

## REST API

除 Web UI 外，所有功能均可通过 HTTP 接口调用，便于脚本 / 外部系统集成。

| 方法 | 路径                                                | 说明                                                                     |
| ---- | --------------------------------------------------- | ------------------------------------------------------------------------ |
| POST | `/books`                                            | 上传 TXT 建书（multipart：`name`/`file`/`voice`/`rate`/`narrate_title`） |
| GET  | `/api/books/<book_id>`                              | 查询进度与章节状态（轮询用，JSON）                                       |
| POST | `/books/<book_id>/retry-failed`                     | 重试所有失败章                                                           |
| POST | `/books/<book_id>/chapters/<chapter_id>/regenerate` | 重生单章                                                                 |
| POST | `/books/<book_id>/settings`                         | 改声音 / 朗读标题开关                                                    |
| POST | `/books/<book_id>/pause`                            | 暂停生成（当前章跑完后停）                                               |
| POST | `/books/<book_id>/resume`                           | 继续生成                                                                 |
| GET  | `/books/<book_id>/download`                         | 全书 ZIP                                                                 |

接口调试页（Swagger）：`http://<NAS_IP>:3033/docs`。

---

## CI / GitHub Actions

代码推到 `main` 或打 `v*` 版本 tag 后，[GitHub Actions 工作流](../.github/workflows/docker-publish.yml) 会自动构建 **amd64 + arm64** 双架构镜像并推送到 GHCR（`ghcr.io/weizheng829/booktovoice`）。

- **首次发布后**：GHCR package 默认可能继承仓库可见性。若仓库 public 却想免登录拉取，去 GitHub 个人主页 → **Packages**，把 `booktovoice` 这个 package 的可见性设为 **Public**。仓库私有则保持 private，NAS 拉取前需先 `docker login ghcr.io`（用 personal access token，权限勾 `read:packages`）。
- 打 tag `v1.2.0` 时 CI 会产出 `:1.2.0` / `:1.2` / `:1` 三个版本 tag，可锁定特定版本部署。

开发机如需复现 CI 的镜像构建，可参考工作流里的 `docker/build-push-action` 多架构构建写法。
