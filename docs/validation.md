# 评测与验证

本文记录自动化检查、模型链路和容器验证结果，便于复现、排查问题及比较后续版本。记录日期：2026年10月15日至21日。

## 自动化检查

| 检查项 | 结果 | 覆盖内容 |
| --- | --- | --- |
| 后端测试 | 20项通过 | 账户权限、上传、任务恢复、检索、引用与历史复评 |
| 静态检查 | 通过 | Ruff |
| 前端检查 | 通过 | TypeScript类型检查与Vite构建 |
| 持续集成 | 通过 | Linux环境的后端测试与前端构建 |

持续集成记录：[GitHub Actions](https://github.com/leonbuilds-ai/rag-knowledge-assistant/actions/runs/38844819562)。

测试覆盖50MiB上传边界、SHA256去重、路径校验、过期token、租约恢复、向量清理、权限范围过滤、单文档BM25、PDF空白页、DOCX表格、统一精排、上下文预算及缓存失效。

```bash
python -m ruff check backend scripts
cd backend
python -m pytest -q
cd ../frontend
npm run build
```

## 端到端模型链路

验证组合为 **PostgreSQL + Redis + Qdrant + Ollama**，生成模型使用 `qwen3.5:9b`，Embedding使用 `qwen3-embedding:8b`。

| 验证项 | 结果 |
| --- | --- |
| 异步文档摄取 | 两份示例文档完成解析与索引 |
| 向量写入 | 4096维非零向量，片段ID与SQL记录一致 |
| 标准问答 | 返回答案、来源编号与原文片段 |
| Agent问答 | 检索、证据检查与生成链路完成 |
| 多轮上下文 | 同一会话内完成连续追问 |
| 答案缓存 | 重复请求复用答案 |
| 历史复评 | 使用已保存的问题、答案与引用 |
| 评分缓存 | 命中已保存的评分，四项结果保持一致 |

完整结果：[模型链路记录](artifacts/smoke-2026-10-15.json)、[核心流程记录](artifacts/smoke-2026-10-15-core.json)。

## 检索与Ragas样例

以下结果来自仓库内两份示例文档、一条标注案例，Ragas版本为0.3.9。该用例用于检查评测流程，效果比较应结合具体业务评测集进行。

| 检索指标 | Dense | Hybrid |
| --- | ---: | ---: |
| Hit@5 | 1.0000 | 1.0000 |
| Recall@5 | 1.0000 | 1.0000 |
| MRR@5 | 1.0000 | 1.0000 |

| Ragas指标 | 得分 |
| --- | ---: |
| Faithfulness | 0.5000 |
| Answer relevancy | 0.8742 |
| Context precision | 约1.0000 |
| Context recall | 1.0000 |

复用同一历史答案与评审配置时，评分缓存命中1条，新增评审案例为0条。

## 多模态与重排适配

| 适配器 | 验证方式 | 结果 |
| --- | --- | --- |
| 多语言CrossEncoder | 对相关/无关语句分别打分 | 0.9565 / -4.6102 |
| Qwen视觉解析 | 通过正式解析入口处理英文测试图片 | 保留500与30两个关键数值 |

测试图片为黑白英文样例，视觉检查未启用OCR。文档摄取与问答用例、独立重排适配器用例分别记录。完整输出见 [模型适配记录](artifacts/model-adapters-2026-10-16.json)。

## 容器与界面

Docker Compose配置检查及前后端镜像构建通过。后端默认包含`ml,evaluation`依赖，在离线、非root容器中通过以下检查：

- `pip check`无依赖冲突。
- Ragas、SentenceTransformers与CPU PyTorch导入成功。
- `/api/health`返回200。
- Tesseract包含中文与英文语言包。

容器记录见 [Docker验证](artifacts/docker-2026-10-16.json)。验证范围包括镜像构建与独立容器健康检查；完整Compose编排见部署文档。

浏览器检查覆盖注册、知识库创建、批量上传与文档状态显示。后续版本可继续扩展检索评测集、中文扫描文档集和负载测试。


## 团队治理与可靠性验收

本轮新增组织角色、受限知识库授权、会话撤销、配额、持久 BM25、版本切换、清理任务、真实 SSE、异步评测、迁移和恢复工具。代码回归覆盖并发准入、会话顺序、幂等重放、跨站请求、登录预算、授权撤销后的历史隐藏、更新中查询、版本恢复、过期缓存、数字核对与 OIDC 协议校验。

业务题集为人工编写的合成语料：8份文档、30条问题，包括26条有答案问题和4条无答案问题。hash/摘录模式验证来源命中、拒答和过期来源过滤；本地 Ollama 另选6条代表案例，包含无答案、恶意文档和跨文档问题。结果仅用于该样例集的回归，不是企业准确率统计。

| 验证 | 实际记录 |
| --- | --- |
| 自动化回归 | SQLite 开发环境49项通过；PostgreSQL + Redis + Qdrant环境48项通过、1项SQLite专用恢复用例跳过 |
| 30条合成业务回归 | 有答案来源 Hit@5 / Recall@5 均为1；4条无答案全部拒答；过期来源未返回 |
| 5000分块持久 BM25 | 40次查询首位命中40次；P95约60ms，仅统计单进程SQL检索 |
| 本地 Ollama 6条代表案例 | 5条有答案来源命中；1条无答案拒答；其中2条生成内容经核验回退为原文摘录 |
| 跨存储恢复演练 | PostgreSQL、文件与Qdrant快照恢复后，登录、检索与原文下载通过 |
| 完整 Compose | 独立迁移、摄取/评测Worker、前端代理、Cookie登录、问答和监控接口通过 |
| Prometheus 告警规则 | 官方 promtool 校验通过，共5条规则 |
| 实际 Ragas 异步闭环 | 四指标返回、历史答案复用与评分缓存回放通过 |

最新 Ragas 单案例为 faithfulness=0.5、answer_relevancy≈0.9451、context_precision≈1、context_recall=1。该结果保留原始评审分歧；同源模型评审与小样本不能替代独立人评或支持总体质量承诺。

完整证据：[业务与容量](artifacts/enterprise-benchmark-demo.json)、[本地模型代表案例](artifacts/enterprise-benchmark-ollama.json)、[真实基础设施与恢复](artifacts/enterprise-infrastructure.json)、[Compose](artifacts/enterprise-compose.json)、[异步模型及Ragas](artifacts/enterprise-smoke-ollama.json)。

```bash
python scripts/benchmark.py --load-chunks 5000 --queries 40 --output artifacts/benchmark.json
python scripts/validate-infrastructure.py --postgres-container <本机数据库容器> --output artifacts/infrastructure.json
python scripts/smoke.py --with-ragas --report artifacts/model-and-ragas.json
```

基础设施脚本只在当前配置服务上创建有唯一前缀的临时数据库、测试schema及向量命名空间，结束后清理本次资源。它不会在用户知识库内运行故障用例。OIDC测试覆盖签名、nonce、audience、PKCE、一次性state与同邮箱冲突；未配置真实企业身份提供商时，不宣称已完成其端到端接入。


## 平台管理与检索修订（2026-10-18）

本轮增加平台管理员全局及账号配额编辑、摄取/评测任务取消与重试、处理中删除确认、逐页解析进度，并调整界面字号和小窗口侧栏。管理员名单由部署配置指定，不能由普通账号或组织角色自行授予。

SQLite回归62项通过；真实PostgreSQL、Redis、Qdrant回归61项通过、1项SQLite专用恢复测试跳过，独立跨存储备份恢复演练通过。覆盖配额即时生效、越权拒绝、处理中取消/删除、同页相邻证据补全、中文分词粒度、代码数组下标与引用编号区分。

30条合成业务题集和5000分块SQL检索探测通过。实际本机知识库另用于人工排查；真实资料与回答仅保留本机，不作为可公开复现的总体准确率样本。文档中没有完整证据的问题保守拒答，Markdown引用的图片未随资料上传时，需要补充相应图片或PDF。

验收记录：[基础设施与恢复](artifacts/platform-infrastructure-2026-10-18.json)、[合成题集与容量](artifacts/platform-benchmark-2026-10-18.json)。

后续问题相关性修订新增4项回归，本地共66项通过，前端构建通过。覆盖数值指标与泛词干扰区分、有来源但不切题的句子拒绝、已核验原句裁剪与复核、最终引用重编号。首页移除无切换逻辑的工作台下拉入口；本机资料实测记录保留在本地。

条款问答修订再新增3项回归，本地共69项通过，30条合成业务回归通过。新增覆盖单位及标准编号紧邻引用、章节内完整条目摘录与标题空结果、流式条款答案核验后保留。实际文档的草稿与最终答案另在本机核对，公开测试只使用合成条款。

## 单终端开发入口（2026-10-19）

新增7项启动器生命周期回归，本地完整回归共76项通过，静态检查和PowerShell语法检查通过。覆盖重复启动锁、已有服务复用、未知端口占用、启动失败清理、Ctrl+C退出路径、超时强制回收，以及真实失败子进程回收。当前三项开发服务的只读复用检查通过；Windows CI另执行启动器测试及脚本语法检查。

## Markdown 结构与回答核验（2026-10-21）

SQLite 完整回归99项通过；真实 PostgreSQL、Redis、Qdrant 回归98项通过、1项 SQLite 专用恢复测试跳过。Ruff、PowerShell 语法检查通过；前端3项队列测试、TypeScript 检查和 Vite 构建通过。自动化功能测试使用哈希向量或模拟模型，不能据此推断真实大模型准确率。

新增覆盖 Markdown 标题归属、原始行号、长代码围栏与表格续块、导航清理、未识别图片提示、上下文完整单元裁剪、比较题两侧方案召回、复合子问题、逐句引用、额外结束条件及操作禁令核对。浏览器实测三个文件中间一个提交失败后，首尾文档均完成索引，失败项可以单独重试；[界面记录与使用说明](markdown-quality.md)。

隔离测试账户的154份实际 Markdown 全部完成重新索引，1937个有效分块均具有来源定位；检查未发现残留目录导航行或不完整代码围栏。此记录描述导入与结构检查，不代表知识内容全部正确。

另用本机 `qwen3.5:9b`、`qwen3-embedding:8b` 对18条真实问答及缓存、追问、跨库范围、幂等、上传去重、越权读取、排队删除和 SSE 链路复测。原始资料、账户与回答保留本机，不上传仓库。部分复杂比较及条件题仍可能保守拒答，通用概念题也可能只得到产品实例；不把这些结果计为完全回答。

独立检查还发现：与资料一致的扩展句也可能过度概括。例如 Go 堆栈分配需要结合逃逸分析，不能仅由变量类型或“被引用”判断，参见 [Go 官方 FAQ](https://go.dev/doc/faq#stack_or_heap)。回答是否忠实于资料、是否完整切题、技术结论是否可靠需要分别评审，当前语义核验仍有误判风险。
