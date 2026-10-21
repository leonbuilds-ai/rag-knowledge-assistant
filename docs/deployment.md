# 部署与模型配置

知衡支持 Docker Compose 部署，也支持独立启动 API、前端和文档处理 Worker。开发环境可以使用 SQLite 与 Qdrant local 验证完整流程；需要独立 Worker 时，应使用 Qdrant server。

## Docker Compose 部署

在项目根目录准备配置：

```bash
cp .env.production.example .env.docker
```

PowerShell 可使用：

```powershell
Copy-Item .env.production.example .env.docker
```

编辑 `.env.docker`，替换全部 `replace-*` 占位值，填写模型服务地址、模型名称与所需密钥，然后执行：

```bash
docker compose --env-file .env.docker config --quiet
docker compose --env-file .env.docker up --build -d
docker compose --env-file .env.docker ps
```

默认访问地址：

| 服务 | 地址与用途 |
| --- | --- |
| Web 工作台 | `http://localhost:8080`，由 Nginx 提供静态文件并转发 `/api` |
| API 健康检查 | `http://127.0.0.1:8010/api/health`，宿主机 API 端口仅绑定回环地址 |
| PostgreSQL、Redis、Qdrant | 仅供 Compose 内部网络访问，未映射宿主机端口 |

Compose 启动 Web、API、独立 Worker、PostgreSQL、Redis 与 Qdrant。后端以 `production` 模式运行，会校验 PostgreSQL、Redis、Qdrant server 及至少 32 字符的非开发默认签名密钥。该校验不替代部署环境的 HTTPS、入口访问控制、备份和容量规划。

镜像默认安装 CPU PyTorch、`ml,evaluation` 可选依赖和 Tesseract。首次构建及模型下载可能耗时较长；镜像不包含模型权重。Compose 将模型缓存放在 `app-data` 卷的 `/app/data/models`，同一卷还保存原始上传文件。PostgreSQL、Redis 和 Qdrant 各自使用独立数据卷。

`.env.docker` 用于 Compose 变量替换，并非所有 `RAG_*` 设置都会自动进入容器。需要调整未在 `docker-compose.yml` 中透传的配置时，应将它加入 API 与 Worker 共用的 `environment`，使两个进程保持一致。

### 连接宿主机 Ollama

容器中的 `127.0.0.1` 指向容器本身。使用宿主机模型服务时，可在 `.env.docker` 中配置：

```dotenv
RAG_LLM_PROVIDER=ollama
RAG_LLM_BASE_URL=http://host.docker.internal:11434
RAG_LLM_MODEL=qwen3.5:9b
RAG_EMBEDDING_PROVIDER=ollama
RAG_EMBEDDING_BASE_URL=http://host.docker.internal:11434
RAG_EMBEDDING_MODEL=qwen3-embedding:8b
RAG_EMBEDDING_DIMENSION=4096
RAG_VISION_MODEL=qwen3.5:9b
```

上述模型名称为配置示例，需先在模型服务中准备对应权重。Ollama 服务必须监听容器能够访问的地址，并通过主机防火墙限制访问范围。

Linux Docker Engine 可添加覆盖文件 `docker-compose.ollama.yml`：

```yaml
services:
  api:
    extra_hosts:
      - "host.docker.internal:host-gateway"
  worker:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

使用覆盖文件启动：

```bash
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.ollama.yml up --build -d
```

也可以将模型服务放入同一 Docker 网络，使用其服务名作为地址。没有鉴权的模型端口应限制在可信网络内。

## 本地开发

以下示例使用 Python 3.12、Node.js 22 和 npm，与项目镜像的运行时版本保持一致。所有命令从仓库根目录开始执行。

根目录 `.env.example` 提供无需外部数据库和模型服务的开发默认配置：SQLite、Qdrant local、确定性哈希向量和原文摘录回答。哈希向量用于验证检索流程，不具备语义模型的理解能力；原文摘录模式也不等同于大模型生成。

### Linux / macOS：venv

```bash
cp .env.example .env
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e "./backend[dev]"
cd frontend
npm ci
```

在独立终端启动 API：

```bash
source .venv/bin/activate
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

在另一个终端启动前端：

```bash
cd frontend
npm run dev
```

访问 `http://127.0.0.1:5173`。Vite 默认将 `/api` 代理到 `http://127.0.0.1:8010`。如需修改代理目标，在 `frontend/.env` 中设置 `VITE_DEV_API_TARGET`；`VITE_API_BASE` 可用于指定前端请求的 API 根地址。

### Windows：Conda 与 PowerShell 脚本

环境名称可以自行指定，以下使用 `rag-assistant`：

```powershell
conda create -n rag-assistant python=3.12
conda activate rag-assistant
.\scripts\bootstrap.ps1 -CondaEnv rag-assistant
```

`bootstrap.ps1` 会在 `.env` 不存在时复制默认配置，并安装后端开发依赖和前端依赖。需要本地语义模型、重排或 OCR 时增加 `-WithModels`；需要 Ragas 时增加 `-WithEvaluation`：

```powershell
.\scripts\bootstrap.ps1 -CondaEnv rag-assistant -WithModels -WithEvaluation
```

在项目根目录用一个终端启动前端、API 与所需的 Worker：

```powershell
.\scripts\dev.ps1 -CondaEnv rag-assistant
# 只检查依赖和现有服务，不启动进程
.\scripts\dev.ps1 -Service status -CondaEnv rag-assistant
# 启动窗口已关闭时，停止此项目的三个服务
.\scripts\dev.ps1 -Service stop -CondaEnv rag-assistant
```

不指定 `-CondaEnv` 时默认使用 `cook-rag-1`。后端监听 `127.0.0.1:8010`，前端监听 `127.0.0.1:5173`，不同服务日志使用 `[API]`、`[Worker]`、`[Web]` 前缀，并保存在 `artifacts/dev/`。启动失败时清理本次已经启动的进程；运行中任一受管服务退出时，停止其余受管服务。按 Ctrl+C 退出后，PowerShell 返回原目录。

已运行且身份匹配的 API/前端，以及同项目目录中具有有效心跳的本机 Worker 会被复用，退出时保留。未知程序占用 8010 或 5173 时停止启动并提示检查，不自动结束进程或切换端口。第一次从多个终端迁移时，可先在原窗口按 Ctrl+C 停止服务，再运行一键命令，让三个服务都由新窗口管理。

启动器只管理开发应用进程，Docker 基础服务和 Ollama 应提前启动。需要单独调试时，仍可使用 `-Service backend`、`-Service frontend`、`-Service worker`，或直接运行上面的 `python -m uvicorn` 和 `npm` 命令。

### 独立 Worker

未配置 `RAG_REDIS_URL` 时，API 会启动进程内文档处理线程，无需额外启动 Worker。此模式适合单进程开发；Qdrant local 的同一数据目录不能由多个进程同时打开。

配置 Redis 后，必须同时配置 Qdrant server，并启动独立 Worker：

```bash
source .venv/bin/activate
cd backend
python -m app.worker
```

一键启动器在启用 Redis 时自动启动或复用独立 Worker。Windows 单独调试 Worker 时可使用：

```powershell
.\scripts\dev.ps1 -Service worker -CondaEnv rag-assistant
```

SQL 保存持久任务状态与处理租约，Redis 提供队列唤醒和缓存。Worker 会扫描排队任务与租约过期的处理中任务；进程重启或 Redis 短暂不可用后，可以依据 SQL 恢复处理。

## 模型适配

修改配置后，重启 API 与 Worker。后端从运行目录的 `../.env` 和 `.env` 读取配置，本地开发应在 `backend` 目录启动。

| 能力 | 配置方式与要求 |
| --- | --- |
| Ollama 生成 | `RAG_LLM_PROVIDER=ollama`；设置 `RAG_LLM_BASE_URL=http://127.0.0.1:11434` 与实际 `RAG_LLM_MODEL`，无需 API key |
| Ollama 嵌入 | `RAG_EMBEDDING_PROVIDER=ollama`；设置模型、`RAG_EMBEDDING_BASE_URL` 及实际输出维度；维度不符时索引会报错 |
| OpenAI 兼容生成 | `RAG_LLM_PROVIDER=openai`；设置包含 `/v1` 的服务地址、实际模型名与 API key，适用于兼容的云服务或 vLLM 端点；无鉴权的本地端点可使用非空占位 key |
| OpenAI 兼容嵌入 | `RAG_EMBEDDING_PROVIDER=openai`；可独立配置 `RAG_EMBEDDING_BASE_URL`、`RAG_EMBEDDING_API_KEY`，未设置时复用生成接口地址和密钥；端点需支持请求中的 `dimensions` 参数 |
| 本地语义嵌入 | 安装 `ml` 可选依赖；设置 `RAG_EMBEDDING_PROVIDER=sentence-transformers` 与模型名称或本地路径，例如 `BAAI/bge-small-zh-v1.5`，程序读取模型实际维度 |
| Cross-Encoder 重排 | 安装 `ml` 可选依赖；设置 `RAG_RERANKER_MODEL` 为兼容模型名称或本地路径，例如 `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`；留空则不启用 |
| 视觉转录 | 设置 `RAG_VISION_MODEL` 为支持图像输入的模型，例如 Qwen-VL 或支持视觉的 Qwen 模型；复用生成模型的接口地址与鉴权配置 |

本地安装可选能力：

```bash
python -m pip install -e "./backend[ml,evaluation]"
```

`RAG_OLLAMA_NUM_CTX` 控制生成上下文窗口，`RAG_EMBEDDING_NUM_CTX` 控制 Ollama 嵌入上下文，`RAG_EMBEDDING_BATCH_SIZE` 控制嵌入批量大小。应根据模型实际支持的窗口和部署资源调整。应用会依据配置的窗口，以 UTF-8 字节数保守裁剪证据与历史；无法容纳的超长问题会返回明确错误。

Qdrant 集合标识包含嵌入提供方、模型、`RAG_EMBEDDING_BASE_URL` 与维度。切换嵌入服务时，应显式设置 `RAG_EMBEDDING_BASE_URL`，并对已有文档调用重新索引接口 `POST /api/documents/{id}/retry`，使文档向量与当前配置一致。旧模型集合不会参与新配置的查询；删除文档时会清理当前项目集合前缀下的历史模型向量。共享 Qdrant 实例时，不同部署应使用不同的 `RAG_QDRANT_COLLECTION_PREFIX`。

### Ragas 与历史答案复评

`evaluation` 可选依赖固定使用 Ragas 0.3.9。启用 Ragas 需要真实生成模型和语义 Embedding；评分使用当前模型配置，并顺序执行。普通检索指标不要求 Ragas，参考答案词项召回率也不等同于忠实度或答案相关性。

历史问答可从聊天中加入评测，复用服务端存档的答案和引用，无需重新生成答案。Ragas 评分仍可能调用模型；相同问题、答案、上下文、参考答案和模型端点配置可复用有效期内的评分缓存。Hit@5、Recall@5 和 MRR@5 需要标注期望命中的文档；缺少标注时相应指标为 `null`。

## OCR 与文档解析

设置 `RAG_OCR_ENABLED=true` 后，OCR 使用 Tesseract 的 `chi_sim+eng` 语言组合。本地运行需安装 `ml` 可选依赖、Tesseract 可执行程序及两种语言包，并确保 `tesseract` 位于进程的 `PATH`。Docker 镜像已安装对应组件。

可使用以下命令检查本地安装：

```bash
tesseract --version
tesseract --list-langs
```

图片和扫描 PDF 页需要 OCR 或可用的视觉模型；DOCX 内嵌图片也会在相关能力启用时提取。OCR 与视觉模型同时配置时会同时参与处理，任一启用能力的依赖都应可用。纯文本 PDF 不依赖 OCR。

当前解析边界：

- 单文件最大 50 MB，可通过 `RAG_MAX_UPLOAD_MB` 降低上限；Nginx 的 51 MB 请求上限为 multipart 元数据保留空间。
- 支持 PDF、DOCX、TXT、Markdown、CSV、JSON、HTML 和 PNG/JPEG/WebP；加密 PDF 需先解除密码。
- PDF 最多 2,000 页；图片最多 4,000 万像素；DOCX 解压后总大小不得超过 200 MiB。
- PDF 保留来源页码；DOCX 和纯文本使用逻辑页，不能据此推断原始排版页码。

## 配置参考

下表为应用配置类的默认值；Compose 部署可能通过 `docker-compose.yml` 或 `.env.production.example` 覆盖它们。`.env` 和 `.env.docker` 均不应提交真实凭据。

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `RAG_ENVIRONMENT` | `development` | `production` 启用基础设施与签名密钥校验 |
| `RAG_DATABASE_URL` | `sqlite:///./data/rag.db` | 可使用 `postgresql+psycopg://...` 连接 PostgreSQL |
| `RAG_DATA_DIR` | `./data` | 上传文件与本地持久数据目录，相对于后端工作目录 |
| `RAG_SECRET_KEY` | 开发占位值 | 部署时替换为至少 32 字符的独立随机密钥 |
| `RAG_CORS_ORIGINS` | 本地 `5173` 的 localhost / 127.0.0.1 来源 | JSON 数组；前后端跨域部署时填写实际 Web 来源 |
| `RAG_REDIS_URL` | 空 | 非空时使用独立 Worker，同时要求 Qdrant server |
| `RAG_QDRANT_URL` / `RAG_QDRANT_API_KEY` | 空 | 未配置 URL 时使用 Qdrant local |
| `RAG_QDRANT_COLLECTION_PREFIX` | `rag_assistant` | 隔离不同部署的向量集合 |
| `RAG_EMBEDDING_PROVIDER` | `hash` | 可选 `sentence-transformers`、`ollama`、`openai` |
| `RAG_EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | `hash` 模式不加载此模型 |
| `RAG_EMBEDDING_DIMENSION` | `384` | 哈希及远程嵌入使用的维度；sentence-transformers 自动读取 |
| `RAG_EMBEDDING_BASE_URL` / `RAG_EMBEDDING_API_KEY` | 空 | 未设置时使用生成服务对应配置 |
| `RAG_EMBEDDING_NUM_CTX` / `RAG_EMBEDDING_BATCH_SIZE` | `4096` / `8` | Ollama 嵌入窗口与批量大小 |
| `RAG_LLM_PROVIDER` | `openai` | 可选 `ollama`；未完成模型配置时使用原文摘录模式 |
| `RAG_LLM_BASE_URL` | `https://api.openai.com/v1` | 生成模型服务地址 |
| `RAG_LLM_MODEL` / `RAG_LLM_API_KEY` | 空 | 模型名与密钥，Ollama 无需密钥 |
| `RAG_OLLAMA_NUM_CTX` | `16384` | 生成上下文预算；必须大于输出预算加 2,048 |
| `RAG_MAX_OUTPUT_TOKENS` | `2048` | 单次模型回答的输出预算 |
| `RAG_MODEL_TIMEOUT` | `180` 秒 | 模型请求时限；远程 OpenAI 嵌入请求另使用 60 秒时限 |
| `RAG_RERANKER_MODEL` / `RAG_VISION_MODEL` | 空 | 留空时不启用相应可选模型 |
| `RAG_OCR_ENABLED` | `false` | 启用 Tesseract OCR |
| `RAG_MAX_UPLOAD_MB` | `50` | 支持 `1–50` MB |
| `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` | `800` / `120` | 切块长度与重叠长度；重叠必须小于块长 |
| `RAG_CACHE_TTL` | `3600` 秒 | 答案与评分缓存有效期，`0` 关闭缓存 |
| `RAG_AGENT_MAX_REWRITES` | `2` | Agent 查询改写配置，当前执行上限为3次 |
| `RAG_WORKER_POLL_SECONDS` / `RAG_JOB_LEASE_SECONDS` | `2` 秒 / `600` 秒 | 持久任务扫描间隔与处理租约 |
| `RAG_TOKEN_LIFETIME_SECONDS` | `86400` 秒 | 登录令牌有效期 |

## 运维与排查

### 日志与健康检查

```bash
docker compose --env-file .env.docker logs --tail=100 api worker
curl http://127.0.0.1:8010/api/health
```

健康检查用于确认 API 进程可响应；模型和解析能力应结合“系统与模型”页面及实际任务检查。

| 现象 | 检查方向 |
| --- | --- |
| 文档持续排队 | 配置 Redis 后是否启动独立 Worker；检查其日志、数据库连接与任务租约 |
| 文档解析失败 | 查看文档错误详情，核对文件格式、大小、密码及 OCR / 视觉模型依赖 |
| Qdrant local 目录被占用 | 停止打开同一目录的其他进程；需要独立 Worker 或多进程时使用 Qdrant server |
| 切换模型后检索异常 | 确认嵌入端点和实际输出维度，并对已有文档重新索引 |
| 未配置模型仍返回答案 | 检查运行状态；`extractive` 是原文摘录模式 |
| Ragas 无法运行 | 安装 `evaluation` 依赖，配置真实生成模型与语义嵌入，并填写参考答案 |
| 评测命中率为 `null` | 为测试用例标注期望命中的文档；没有标注时不计算命中率 |
| 模型请求超时 | 检查服务可达性、模型加载与资源占用；同时核对应用与反向代理时限 |
| 本地前端无法连接 API | 检查 `5173` 与 `8010` 服务、Vite 代理目标，以及跨域部署的 CORS 配置 |

Nginx 和 Vite 开发代理默认使用 600 秒时限。问答可使用带保活的真实 SSE；评测提交后返回 202，由前端轮询结果。模型供应商仍有独立请求时限。

### 备份与升级

同步备份 PostgreSQL、原始上传文件和 Qdrant 快照，并记录对应嵌入模型与集合配置。Redis 保存队列通知和缓存，SQL 持久任务是任务恢复依据。日常重启应保留 Compose 数据卷；删除卷会移除持久数据。

数据库使用 Alembic 版本迁移。Compose 的 migrate 服务先升级表结构，API 与 Worker 检查迁移版本后启动。本地开发默认自动迁移；正式升级使用维护窗口和协调备份，升级后旧登录凭证需要重新登录。


### 迁移与协调备份

在全部 API、摄取 Worker 和评测 Worker 停止后，执行离线备份。`--quiesced` 表示已停止所有写入方；此参数不会代替停服动作。数据库、上传文件和向量服务必须使用同一时间点的状态。

```bash
docker compose --env-file .env.docker stop web api worker evaluation-worker
docker compose --env-file .env.docker run --rm --no-deps api python -m app.maintenance backup --path /app/data/backups/before-upgrade --quiesced
docker compose --env-file .env.docker run --rm --no-deps api python -m app.maintenance verify-backup --path /app/data/backups/before-upgrade
docker compose --env-file .env.docker run --rm --no-deps api python -m app.maintenance migrate
docker compose --env-file .env.docker up -d
```

备份目录必须是新目录，包含数据库 dump、上传文件、项目集合快照及 SHA256 清单。备份包含业务原件及数据库内容，应保存到受控的加密存储，并独立保管部署密钥。主机/数据卷损坏时，需要异地主副本；同卷备份仅适用于升级回退。

恢复时，配置一个**空数据库、空上传目录和未使用的集合前缀**，停止目标环境的全部写入方，再运行：

```bash
python -m app.maintenance restore --path /secure-backup/snapshot --quiesced
python -m app.maintenance reconcile
```

恢复会先校验全部文件并拒绝覆盖已有表或集合；支持重定位上传文件路径和项目集合前缀。本机未安装 pg_dump/pg_restore 时，可用 `--postgres-container <数据库容器名>` 通过本机 PostgreSQL 容器执行工具。容器镜像已包含 PostgreSQL 客户端。

恢复后验证登录、文档数量、原文下载、版本引用、问答及待处理任务，记录业务可接受的恢复点和恢复耗时。向量快照兼容性参考 [Qdrant 官方说明](https://qdrant.tech/documentation/operations/snapshots/)。

`reconcile` 默认只报告；在停写后可执行 `reconcile --apply --quiesced` 清理无主向量。缺失或模型切换后的向量，应通过文档“重新索引”修复。恢复中途失败时保留备份并使用新的空目标重试。

### 监控与模型探针

```bash
curl http://127.0.0.1:8010/api/ready
python -m app.health models
python scripts/configure-monitoring.py --env-file .env.docker
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.monitoring.yml up -d
```

监控配置把独立令牌保存到忽略提交的 `.secrets/metrics_token`；API 重新启动后启用 `/api/metrics`，Prometheus 在本机 9090 端口提供查询。示例规则覆盖 API 不可用、Worker 心跳缺失、任务积压、清理重试及错误率，实际阈值需要按业务负载调整。告警通知渠道由部署方接入 Alertmanager。

模型探针只检查 API 连通与模型列表，不执行付费生成；部分兼容服务不提供 `/models`，此时需要结合实际推理验证。HTTP 指标统计到响应头的耗时，完整生成时延查看 RAG 阶段指标及消息运行元数据。

### 组织、登录与文档源

组织角色、知识库单独授权、OIDC 参数、会话撤销、配额与目录同步的配置见[团队治理与可靠性](enterprise.md)。HTTPS 入口配置实际的 `RAG_PUBLIC_BASE_URL`、`RAG_CORS_ORIGINS` 和 `RAG_COOKIE_SECURE=true`，反向代理仅信任受控网关。

当前 Compose 为单实例存储部署。多个 API/Worker 主机需要共享原件存储、数据库/向量副本和模型容量规划，并在目标环境验证故障切换。
