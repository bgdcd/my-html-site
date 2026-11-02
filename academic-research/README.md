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

## 数据保存
首次运行后自动创建 `data/academic_library.db` 和 `data/uploads/`。请定期备份整个 `data` 目录。

## 当前阶段边界
这是第一阶段基础工程，不应将其误认为已经完成全文语义检索、AI写作、查重或知网/万方授权接入。后续模块须逐项实现并测试。
