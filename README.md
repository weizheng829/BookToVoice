# BookToVoice

> TXT 小说 → 章节切分 → Edge-TTS → 章节 MP3

独立的 Docker 服务：上传 TXT 小说，自动切分章节，逐章用 **Edge-TTS**（微软）合成 MP3，单旁白配音（默认**晓晓**）、**多线程并发生成**（默认 5 路并行），自带 Web UI。

> 生成太慢或太快？点页面右上角 **⚙ 设置** 可实时调节并发生成数等参数，保存即生效，无需重启。

**直连 Edge-TTS**：内置 Python `edge-tts` 库，不依赖 EasyVoice 或任何外部 TTS 容器，单容器即可运行。

> 想自己**构建镜像 / 本地开发 / 看架构与 API**？请阅 [开发文档](docs/DEVELOPMENT.md)。

---

## 界面预览

**新建有声书**（上传 TXT、选声音、调语速、试听）：

<img src="docs/new-book.jpg" width="450" alt="新建有声书">

**书籍详情 / 生成进度**（进度条、章节状态、试听 / 下载 / 重生）：

<img src="docs/book-detail.jpg" width="700" alt="书籍详情">

---

## 目录

- [部署](#部署)
- [使用流程](#使用流程)
- [访问地址](#访问地址)
- [故障排查](#故障排查)
- [数据与备份](#数据与备份)

---

## 部署

> **推荐方式**：直接拉取 GHCR 镜像，无需在本机构建。NAS 装 Docker + Docker Compose 即可（群晖装 Container Manager 即含），端口 `3033` 可用，容器能访问外网（连微软 Edge TTS）。

放一份 `docker-compose.yml`（`volume` 路径换成你 NAS 的）：

```yaml
services:
  book-service:
    image: ghcr.io/weizheng829/booktovoice:latest
    container_name: book-service
    ports:
      - 3033:3033
    volumes:
      - /volume2/SSD/docker/bookToVoice/input:/app/input
      - /volume2/SSD/docker/bookToVoice/output:/app/output
      - /volume2/SSD/docker/bookToVoice/data:/app/data
    restart: always
```

```bash
cd /volume2/SSD/docker/bookToVoice
docker compose pull        # 拉取最新镜像（自动选 amd64/arm64）
docker compose up -d       # 启动 / 重建容器
```

镜像会按 NAS 架构自动匹配，**x86 机器和 ARM NAS（RK3588 等）都能直接用**。

> **离线部署**（NAS 不能联网拉镜像）：去 [GitHub Releases](https://github.com/weizheng829/BookToVoice/releases/latest) 下载对应架构的 `booktovoice-amd64.tar.gz` / `booktovoice-arm64.tar.gz`，传到 NAS 执行 `docker load -i <文件>`，再用 compose 启动（`image: booktovoice:latest`）。详见[开发文档 · 离线下载](docs/DEVELOPMENT.md#离线下载官方-tar)。

---

## 使用流程

1. **新建有声书**：上传 TXT → 填书名 → 选声音（默认晓晓）→ ☑ 朗读章节标题（默认开）→ 上传并开始生成。
2. **详情页**：进度条 + 章节状态表，每 3 秒自动刷新。
3. **完成后**：单章「▶ 试听/下载」，或「⬇ 打包下载 ZIP」。
4. **失败处理**：单章「重生」，或「↻ 重试所有失败章」。
5. **设置**：详情页可改声音 / 朗读标题（仅影响未生成章节）。
6. **暂停/继续**：详情页「⏸ 暂停生成」可停止后续章节（当前正在合成的一章会跑完再停）；「▶ 继续生成」恢复。

---

## 访问地址

浏览器打开 `http://<NAS_IP>:3033/`（`<NAS_IP>` 替换为 NAS 局域网 IP）。

---

## 故障排查

| 现象                           | 排查                                                                                                              |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| 章节合成失败                   | `docker compose logs -f book-service` 看错误；多为到微软的网络波动，会自动重试                                     |
| 端口 `3033` 被占用             | 改 compose 里 `ports` 的宿主机端口                                                                                |
| 上传后提示「未解析到任何章节」 | TXT 缺 `第X章`/`Chapter N` 标题；正常会按字数兜底分块                                                             |
| TXT 中文乱码                   | 已支持 utf-8/gb18030/big5 等多编码自动识别；如仍乱码，把文件另存为 UTF-8                                          |
| 中断后想继续                   | 直接重新启动即可——已生成的 mp3 会自动跳过                                                                         |
| 生成的 mp3 在哪                | 宿主机 `output/<书名>/` 目录                                                                                      |

镜像导入 / 架构报错等部署相关问题见 [开发文档](docs/DEVELOPMENT.md)。

---

## 数据与备份

- 上传的原始 TXT：`input/`
- 生成的章节 MP3：`output/`
- 数据库（书籍/章节状态）：`data/book.db`

迁移或备份：拷贝整个项目目录（含 `input/output/data`）即可。
