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

## 可选 AI 综述写作服务

项目提供 OpenAI 兼容的模型接口适配层，但默认不配置密钥，也不会在浏览器中保存或暴露密钥。只有部署者在服务器环境中配置以下变量后，工作台的“AI生成综述草稿”才可调用模型：

```bash
ACADEMIC_LLM_API_KEY=你的服务密钥
ACADEMIC_LLM_MODEL=服务商提供的模型名称
ACADEMIC_LLM_BASE_URL=https://api.openai.com/v1
```

如果使用其他 OpenAI 兼容服务，将 `ACADEMIC_LLM_BASE_URL` 设置为该服务的 API 根地址。请通过部署平台的 Secret/Environment Variables 配置，不要把真实密钥提交到 GitHub，也不要填写到前端页面。

AI草稿只接收用户明确勾选的证据卡片，要求模型逐句使用 `[证据#编号]` 标注，并在服务端拒绝未选择的证据编号或完全没有引用编号的输出。但编号校验不等于事实核验：模型仍可能误读原文，研究者必须逐条核对。模型调用会将所选证据的论点、原文摘录和文献元数据发送给所配置的模型服务，请先确认该服务的数据处理和隐私条款。未配置时，AI写作接口会明确返回服务未配置，不会生成模拟内容。

## 数据保存
首次运行后自动创建 `data/academic_library.db` 和 `data/uploads/`。请定期备份整个 `data` 目录。

## 当前阶段边界
这是第一阶段基础工程，不应将其误认为已经完成全文语义检索、AI写作、查重或知网/万方授权接入。后续模块须逐项实现并测试。
