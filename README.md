# BookToVoice

> TXT 小说 → 章节切分 → Edge-TTS → 章节 MP3

独立的 Docker 服务：上传 TXT 小说，自动切分章节，逐章用 **Edge-TTS**（微软）合成 MP3，单旁白配音（默认**晓晓**）、串行生成，自带 Web UI。

**直连 Edge-TTS**：内置 Python `edge-tts` 库，不依赖 EasyVoice 或任何外部 TTS 容器，单容器即可运行。

---

## 界面预览

**新建有声书**（上传 TXT、选声音、调语速、试听）：

<img src="docs/new-book.jpg" width="380" alt="新建有声书">

**书籍详情 / 生成进度**（进度条、章节状态、试听 / 下载 / 重生）：

<img src="docs/book-detail.jpg" width="600" alt="书籍详情">

---

## 目录

- [BookToVoice](#booktovoice)
  - [界面预览](#界面预览)
  - [目录](#目录)
  - [架构](#架构)
  - [目录结构](#目录结构)
  - [前置条件](#前置条件)
  - [打包 + 部署（离线镜像）](#打包--部署离线镜像)
    - [1. 开发机构建并导出镜像](#1-开发机构建并导出镜像)
    - [2. 传到 NAS](#2-传到-nas)
    - [3. NAS 导入镜像](#3-nas-导入镜像)
    - [4. 用 compose 启动](#4-用-compose-启动)
    - [5. 访问](#5-访问)
    - [更新镜像（改代码后）](#更新镜像改代码后)
    - [常用运维命令](#常用运维命令)
  - [访问 URL](#访问-url)
    - [浏览器（宿主机 / 局域网）](#浏览器宿主机--局域网)
    - [REST API（供脚本 / 外部调用）](#rest-api供脚本--外部调用)
  - [使用流程](#使用流程)
  - [验证部署](#验证部署)
  - [配置（环境变量）](#配置环境变量)
  - [Edge-TTS 说明](#edge-tts-说明)
  - [故障排查](#故障排查)
  - [数据与备份](#数据与备份)
  - [本地开发（可选）](#本地开发可选)

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

## 前置条件

- **NAS**：已安装 Docker 与 Docker Compose（群晖装 Container Manager 即含）；只能 `docker load` 导入镜像、不能 build 也没关系。
- **开发机**（Windows/Mac/Linux）：装有 Docker，用于构建并导出镜像。
- NAS 端口 `3033` 可用。
- 容器需能访问外网（连微软 Edge TTS 服务）。

---

## 打包 + 部署（离线镜像）

> 适用于不能在 NAS 上 `docker build`、只能导入 `.tar/.tar.gz` 镜像的环境。
> 流程：**开发机构建 → 导出 tar.gz → 传到 NAS → 导入 → compose 启动**。

### 1. 开发机构建并导出镜像

在装了 Docker 的开发机（Windows/Mac/Linux）上，项目根目录执行：

```bash
cd D:\code\pycharmProject\bookToVoice

# 构建
docker build -t booktovoice:latest .

# 导出为 tar.gz（压缩，体积约为镜像的 40-60%）
docker save booktovoice:latest | gzip > booktovoice.tar.gz
```

得到 `booktovoice.tar.gz`（约 200-300MB）。

> 不压缩（.tar）用 `docker save -o booktovoice.tar booktovoice:latest`，文件更大但导入略快。

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
# 开发机
docker build -t booktovoice:latest .
docker save booktovoice:latest | gzip > booktovoice.tar.gz

# NAS（传完文件后）
docker load -i booktovoice.tar.gz
docker compose up -d        # 检测到镜像更新会重建容器
```

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

## 访问 URL

### 浏览器（宿主机 / 局域网）

| 用途                          | URL                                                                |
| ----------------------------- | ------------------------------------------------------------------ |
| **Book Service 主页（书架）** | `http://<NAS_IP>:3033/`                                            |
| 新建有声书（上传 TXT）        | `http://<NAS_IP>:3033/books/new`                                   |
| 某 book 详情页                | `http://<NAS_IP>:3033/books/<book_id>`                             |
| 全书打包下载                  | `http://<NAS_IP>:3033/books/<book_id>/download`                    |
| 单章下载/试听                 | `http://<NAS_IP>:3033/books/<book_id>/chapters/<chapter_id>/audio` |
| 接口调试（Swagger）           | `http://<NAS_IP>:3033/docs`                                        |

### REST API（供脚本 / 外部调用）

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

---

## 使用流程

1. **新建有声书**：上传 TXT → 填书名 → 选声音（默认晓晓）→ ☑ 朗读章节标题（默认开）→ 上传并开始生成。
2. **详情页**：进度条 + 章节状态表，每 3 秒自动刷新。
3. **完成后**：单章「▶ 试听/下载」，或「⬇ 打包下载 ZIP」。
4. **失败处理**：单章「重生」，或「↻ 重试所有失败章」。
5. **设置**：详情页可改声音 / 朗读标题（仅影响未生成章节）。
6. **暂停/继续**：详情页「⏸ 暂停生成」可停止后续章节（当前正在合成的一章会跑完再停）；「▶ 继续生成」恢复。

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

## 配置（环境变量）

`docker-compose.yml` 默认**不写 `environment`**，直接用代码内置默认值（晓晓、`+0%`、朗读标题开、重试 3 次）。如需覆盖，给 `book-service` 加一个 `environment:` 段，改完执行 `docker compose up -d`：

```yaml
    environment:
      - DEFAULT_VOICE=zh-CN-YunxiNeural
      - NARRATE_TITLE_DEFAULT=false
```

| 变量                    | 默认                   | 说明                   |
| ----------------------- | ---------------------- | ---------------------- |
| `DEFAULT_VOICE`         | `zh-CN-XiaoxiaoNeural` | 默认声音（晓晓）       |
| `DEFAULT_RATE`          | `+0%`                  | 默认语速               |
| `NARRATE_TITLE_DEFAULT` | `true`                 | 新建书默认是否朗读标题 |
| `MAX_RETRIES`           | `3`                    | 单章最大重试次数       |

---

## Edge-TTS 说明

- 使用微软 Edge 浏览器的 TTS 接口，免费、无需 API Key。
- 参数格式：`rate`/`volume` 百分比（如 `+50%`/`-20%`），`pitch` 赫兹（如 `+10Hz`）。
- 常用中文声音：`zh-CN-XiaoxiaoNeural`（晓晓，女）、`zh-CN-YunxiNeural`（云希，男）等；容器内执行 `edge-tts --list-voices` 可查全部。
- 整章文本一次调用合成；与本地 `edge-tts` 脚本同源。

---

## 故障排查

| 现象                                           | 排查                                                                                            |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| 章节合成失败                                   | `docker compose logs -f book-service` 看错误；多为到微软的网络波动，会自动重试                  |
| 首次合成就失败                                 | 按上面「验证部署」第 3 步测容器内 edge-tts 连通性                                               |
| 端口 `3033` 被占用                             | 改 compose 里 `ports` 的宿主机端口                                                              |
| compose 报 `image not found` / `no such image` | 镜像未导入：先 `docker load -i booktovoice.tar.gz`，再 `docker images \| grep booktovoice` 确认 |
| 上传后提示「未解析到任何章节」                 | TXT 缺 `第X章`/`Chapter N` 标题；正常会按字数兜底分块                                           |
| TXT 中文乱码                                   | 已支持 utf-8/gb18030/big5 等多编码自动识别；如仍乱码，把文件另存为 UTF-8                        |
| 中断后想继续                                   | 直接重新启动即可——已生成的 mp3 会自动跳过                                                       |
| 生成的 mp3 在哪                                | 宿主机 `output/<书名>/` 目录                                                                    |

---

## 数据与备份

- 上传的原始 TXT：`input/`
- 生成的章节 MP3：`output/`
- 数据库（书籍/章节状态）：`data/book.db`

迁移或备份：拷贝整个项目目录（含 `input/output/data`）即可。

---

## 本地开发（可选）

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
