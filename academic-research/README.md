# 学术智库

全中文学术文献管理与研究辅助平台。当前阶段为可运行的基础版本，持续完善中。

## 当前实现
- FastAPI 后端与 SQLite 持久化数据库
- PDF、DOCX 文件上传及文本提取
- 个人文献库元数据保存、全文关键词检索
- OpenAlex、Crossref 公开接口检索真实文献元数据
- 研究领域管理；初始四个领域中第04项保持空白
- 未确认分类的文献进入“待确认”状态

## 重要说明
- 不包含虚构论文或演示数据。
- 外部检索只返回接口实际提供的元数据；没有合法开放全文时，不会标记为已获取全文。
- 当前没有接入中国知网或万方的授权全文服务，不会声称已接通。
- 本项目目前使用本地 SQLite，适合单机试用；正式多人部署前需要配置持久化磁盘、备份和访问控制。

## 本地运行
```bash
cd academic-research
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

浏览器访问 http://127.0.0.1:8000 。

## 在线部署（Docker + 持久化磁盘）

已提供 `Dockerfile`，可用于 Render 等支持 Docker 和持久化磁盘的平台。当前仓库只提交部署配置，不会自动创建云服务，也不会替用户开通付费资源。

以 Render 为例，按以下参数手动创建 Web Service：

1. 连接 GitHub 仓库 `bgdcd/my-html-site`，分支选择 `main`。
2. Root Directory（根目录）填写 `academic-research`。
3. Runtime 选择 Docker，Dockerfile 使用根目录中的 `Dockerfile`。
4. 选择支持 Persistent Disk 的付费实例规格；创建磁盘并设置 Mount Path 为 `/app/academic-research/data`。
5. 部署后访问平台分配的 HTTPS 地址，并检查 `/api/health`。
6. 如需AI写作，再按下方说明设置模型服务环境变量。

应用默认将 SQLite 数据库保存到 `data/academic_library.db)，上传原文件保存在 `data/uploads/`。必须把持久化磁盘挂载到上述 data 目录，否则容器重建或重新部署时，本地数据库和上传文件可能丢失。不要把数据库或上传文件提交到 GitHub。

**部署限制与数据安全：**
- 当前方案是单实例 SQLite，适合个人/小规模试用；不适合多实例并发写入。扩大到多人使用前应迁移 PostgreSQL，并完成用户认证、权限隔离和审计。
- 持久化磁盘不等于备份。应定期备份整个 data 目录，并实际演练恢复。
- 当前应用尚未实现完整登录、用户隔离、上传恶意文件扫描、访问频率限制和管理员权限。部署到公网前应先限制访问，不要上传敏感或受保密协议保护的论文材料。
- 部署成功与否必须以云平台构建日志、服务运行状态及健康检查为准；GitHub Actions 通过只代表代码检查通过，不代表线上服务已经部署。

## 账号注册、登录与数据隔离

项目已加入首个管理员账号初始化、账号登录和按账号隔离个人研究数据的基础机制：

- 用户名与密码登录；密码使用 PBKDF2-HMAC-SHA256（每次独立随机盐）保存，不保存明文密码。
- 登录会话使用随机不透明令牌；数据库只保存令牌 SHA-256 摘要，默认有效期 30 天。
- 文献、研究分类、证据卡片、综述项目和综述段落均带有 `owner_id`；ORM 查询自动按当前账号添加所有权过滤，新建数据自动写入当前账号 ID。
- 首个管理员创建成功时，系统把数据库中尚未分配账号的旧数据交给该管理员，避免原有文献丢失。
- 首个管理员必须通过环境变量 `ACADEMIC_BOOTSTRAP_CODE` 提供初始化口令。**部署前必须设置随机强口令**，否则不能创建首个账号。
- 后续注册需要服务器环境变量 `ACADEMIC_REGISTRATION_CODE`。没有邀请码时不开放后续注册。
- 创建首个管理员前，若没有旧版共享访问口令，业务 API 会拒绝访问；健康检查和账号状态、注册、登录接口除外。

云平台至少设置以下 Secret/环境变量：

```text
ACADEMIC_BOOTSTRAP_CODE=请设置随机且足够长的首次管理员初始化口令
ACADEMIC_REGISTRATION_CODE=如需开放新成员注册，再设置独立邀请码
```

如仍在过渡阶段，也可保留 `ACADEMIC_ACCESS_TOKEN` 旧版共享访问口令。个人账号建立后，业务 API 以个人会话令牌为准；不要把共享口令当作个人账号密码。

**当前账号系统的限制：** 目前没有密码找回/修改页面、管理员用户管理界面、账号禁用界面、登录失败限流、邮件验证或双因素认证。后续注册邀请码是服务器级共享配置，不是逐用户邀请。公网部署前应通过平台访问控制/VPN进一步限制入口，并尽快补上登录限流和管理员管理功能。

上传接口另有分块读取、40MB大小限制、PDF文件头校验、DOCX压缩包结构校验及解压后总大小限制；这些措施不能替代恶意软件扫描或沙箱隔离。

## 可选 AI 综述写作服务

项目提供 OpenAI 兼容的模型接口适配层，但默认不配置密钥，也不会在浏览器中保存或暴露密钥。只有部署者在服务器环境中配置以下变量后，工作台的“AI生成综述草稿”才可调用模型：

```bash
ACADEMIC_LLM_API_KEY=你的服务密钥
ACADEMIC_LLM_MODEL=服务商提供的模型名称
ACADEMIC_LLM_BASE_URL=https://api.openai.com/v1
```

如果使用其他 OpenAI 兼容服务，将 `ACADEMIC_LLM_BASE_URL` 设置为该服务的 API 根地址。请通过部署平台的 Secret/Environment Variables 配置，不要把真实密钥提交到 GitHub，也不要填写到前端页面。

AI草稿只接收用户明确勾选的证据卡片，要求模型逐句使用 `[证据#编号]` 标注，并在服务端拒绝未选择的证据编号或完全没有引用编号的输出。但编号校验不等于事实核验：模型仍可能误读原文，研究者必须逐条核对。模型调用会将所选证据的论点、原文摘录和文献元数据发送给所配置的模型服务，请先确认该服务的数据处理和隐私条款。未配置时，AI写作接口会明确返回服务未配置，不会生成模拟内容。

## 数据备份、完整性校验与恢复

项目提供 `backup_data.py` 命令行工具，备份 SQLite 数据库和个人上传原文件。SQLite 数据库使用 Python SQLite 在线备份 API 创建一致性快照；上传目录归档为 ZIP，并生成 SHA-256 清单。

在项目目录（容器内为 `/app/academic-research`）执行：

```bash
# 创建备份，默认写入 data/backups/
python backup_data.py backup

# 校验指定备份目录中的数据库、归档及每个上传文件
python backup_data.py verify --backup-dir data/backups/academic-backup-备份时间戳

# 只保留最近7份校验通过的备份；校验失败的目录不会被自动删除
python backup_data.py prune --backup-root data/backups --keep 7

# 恢复到两个全新的目标路径（不会覆盖已有文件）
python backup_data.py restore --backup-dir data/backups/academic-backup-备份时间戳 --database data/restore-test.db --uploads data/restore-test-uploads --yes
```

备份目录包含 `database.sqlite3`、`uploads.zip` 和 `manifest.json`。恢复前会校验哈希和 SQLite 完整性；恢复目标必须不存在，避免意外覆盖当前数据。恢复到生产环境时，应先停止服务、将数据恢复到新的路径、核对文件和数据库，再按维护流程切换挂载或配置；不要在运行中的实例上直接覆盖数据库。

**重要限制：** 默认备份放在 `data/backups/`，与业务数据位于同一持久盘，只能用于误操作回滚或文件损坏时的辅助恢复，不能抵御磁盘丢失、账号被盗或云平台故障。请定期将备份目录复制到独立的异地/对象存储，并实际演练恢复。工具当前仅支持本地文件型 SQLite；如果设置 PostgreSQL 等数据库，必须改用相应数据库原生备份方案。上传文件在归档期间仍可能变化，高写入场景请暂停上传后备份。

## PostgreSQL 数据库（可选）

应用通过 SQLAlchemy 连接数据库。默认使用 SQLite；如需更高并发和后续多用户扩展，可在部署平台配置：

```text
ACADEMIC_DATABASE_URL=postgresql+psycopg://用户名:密码@主机:5432/数据库名
```

请将真实连接串作为平台 Secret/环境变量保存，不要提交到仓库。切换数据库不会自动迁移旧 SQLite 数据；正式切换前需要单独执行数据迁移并核对文献记录、证据卡片、综述项目及文件路径。当前 `backup_data.py` 只备份 SQLite，PostgreSQL 必须使用 `pg_dump` / `pg_restore` 等原生工具。原始上传文件仍保存在应用的 `data/uploads/`，需单独做持久化和异地备份。

GitHub Actions 使用独立 PostgreSQL 16 服务运行应用导入、建表、迁移和 API 检查；Docker 健康检查仍覆盖默认 SQLite 启动路径。

## 数据保存
首次运行后自动创建 `data/academic_library.db` 和 `data/uploads/`。请定期备份整个 `data` 目录。

## 当前阶段边界
这是第一阶段基础工程，不应将其误认为已经完成全文语义检索、AI写作、查重或知网/万方授权接入。后续模块须逐项实现并测试。
