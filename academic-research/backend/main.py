from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import fitz
import httpx
from docx import Document
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, select, or_
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
DATABASE_URL = os.getenv("ACADEMIC_DATABASE_URL", f"sqlite:///{(DATA_DIR / 'academic_library.db').as_posix()}")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

class ResearchArea(Base):
    __tablename__ = "research_areas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("research_areas.id"), nullable=True)
    is_placeholder: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class Paper(Base):
    __tablename__ = "papers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(1000), default="未识别标题")
    authors: Mapped[str] = mapped_column(Text, default="")
    journal: Mapped[str] = mapped_column(String(500), default="")
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    doi: Mapped[str] = mapped_column(String(300), default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    abstract_type: Mapped[str] = mapped_column(String(40), default="未识别")
    keywords: Mapped[str] = mapped_column(Text, default="")
    full_text: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(80), default="个人上传")
    source_url: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(Text, default="")
    file_name: Mapped[str] = mapped_column(String(500), default="")
    file_sha256: Mapped[str] = mapped_column(String(64), default="")
    fulltext_status: Mapped[str] = mapped_column(String(40), default="未获取")
    verification_status: Mapped[str] = mapped_column(String(40), default="待核验")
    area_id: Mapped[Optional[int]] = mapped_column(ForeignKey("research_areas.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

Base.metadata.create_all(engine)

def seed_areas():
    with SessionLocal() as db:
        if db.scalar(select(ResearchArea.id).limit(1)) is not None:
            return
        for name in ["01 体育学与运动训练", "02 小学数学", "03 多学科融合"]:
            db.add(ResearchArea(name=name))
        db.add(ResearchArea(name="04", is_placeholder=True))
        db.commit()

seed_areas()
app = FastAPI(title="学术智库", version="0.1.0", description="真实文献元数据检索与个人文献管理")
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")

@app.get("/")
def home():
    return FileResponse(ROOT / "frontend" / "index.html")

@app.get("/api/health")
def health():
    with SessionLocal() as db:
        paper_count = db.scalar(select(Paper.id).count()) if False else len(db.scalars(select(Paper.id)).all())
        area_count = len(db.scalars(select(ResearchArea.id)).all())
    return {"status": "ok", "database": "connected", "papers": paper_count, "areas": area_count}

@app.get("/api/areas")
def list_areas():
    with SessionLocal() as db:
        rows = db.scalars(select(ResearchArea).order_by(ResearchArea.id)).all()
        return [{"id": x.id, "name": x.name, "parent_id": x.parent_id, "is_placeholder": x.is_placeholder} for x in rows]

class AreaInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: Optional[int] = None

@app.post("/api/areas")
def add_area(payload: AreaInput):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "分类名称不能为空")
    with SessionLocal() as db:
        if payload.parent_id and db.get(ResearchArea, payload.parent_id) is None:
            raise HTTPException(404, "上级分类不存在")
        item = ResearchArea(name=name, parent_id=payload.parent_id, is_placeholder=False)
        db.add(item); db.commit(); db.refresh(item)
        return {"id": item.id, "name": item.name, "parent_id": item.parent_id}

@app.patch("/api/areas/{area_id}")
def rename_area(area_id: int, payload: AreaInput):
    with SessionLocal() as db:
        item = db.get(ResearchArea, area_id)
        if item is None: raise HTTPException(404, "分类不存在")
        item.name = payload.name.strip()
        item.parent_id = payload.parent_id
        item.is_placeholder = False
        db.commit()
        return {"id": item.id, "name": item.name, "parent_id": item.parent_id}

def extract_pdf(path: Path) -> str:
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text("text") for page in doc).strip()
    finally:
        doc.close()

def extract_docx(path: Path) -> str:
    doc = Document(path)
    chunks = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            chunks.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(chunks).strip()

def guess_title(text: str, filename: str) -> str:
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) >= 5:
            return line[:500]
    return Path(filename).stem[:500] or "未识别标题"

def suggested_area(text: str) -> Optional[int]:
    t = text.lower()
    rules = [
        (["体育", "运动训练", "体能", "身体活动", "运动技能"], "01 体育学与运动训练"),
        (["小学数学", "数学教学", "数学教育", "数感", "几何教学"], "02 小学数学"),
        (["跨学科", "多学科", "项目式学习", "主题课程"], "03 多学科融合"),
    ]
    with SessionLocal() as db:
        for terms, area_name in rules:
            if any(term in t for term in terms):
                area = db.scalar(select(ResearchArea).where(ResearchArea.name == area_name))
                if area: return area.id
    return None

@app.post("/api/upload")
async def upload_paper(file: UploadFile = File(...)):
    filename = Path(file.filename or "上传文件").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        raise HTTPException(400, "目前仅支持 PDF 和 DOCX 文件")
    raw = await file.read()
    if not raw: raise HTTPException(400, "文件内容为空")
    if len(raw) > 40 * 1024 * 1024: raise HTTPException(413, "单个文件不能超过40MB")
    digest = hashlib.sha256(raw).hexdigest()
    target = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    target.write_bytes(raw)
    try:
        text = extract_pdf(target) if suffix == ".pdf" else extract_docx(target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(422, f"文件解析失败：{exc.__class__.__name__}")
    area_id = suggested_area(text)
    with SessionLocal() as db:
        existing = db.scalar(select(Paper).where(Paper.file_sha256 == digest))
        if existing:
            target.unlink(missing_ok=True)
            return {"id": existing.id, "duplicate": True, "message": "该文件已在文献库中"}
        paper = Paper(title=guess_title(text, filename), full_text=text, file_path=str(target),
                      file_name=filename, file_sha256=digest, fulltext_status="已解析" if text else "未提取到文本",
                      verification_status="待核验", area_id=area_id, source="个人上传")
        db.add(paper); db.commit(); db.refresh(paper)
        return {"id": paper.id, "title": paper.title, "characters_extracted": len(text),
                "area_id": area_id, "classification": "建议分类" if area_id else "待确认分类",
                "fulltext_status": paper.fulltext_status}

@app.get("/api/papers")
def list_papers(q: str = "", limit: int = Query(100, ge=1, le=500)):
    with SessionLocal() as db:
        stmt = select(Paper).order_by(Paper.created_at.desc()).limit(limit)
        if q.strip():
            term = f"%{q.strip()}%"
            stmt = select(Paper).where(or_(Paper.title.like(term), Paper.authors.like(term),
                    Paper.abstract.like(term), Paper.keywords.like(term), Paper.full_text.like(term))).order_by(Paper.created_at.desc()).limit(limit)
        rows = db.scalars(stmt).all()
        return [{"id": p.id, "title": p.title, "authors": p.authors, "journal": p.journal,
                 "year": p.year, "doi": p.doi, "abstract": p.abstract, "source": p.source,
                 "source_url": p.source_url, "fulltext_status": p.fulltext_status,
                 "verification_status": p.verification_status, "area_id": p.area_id,
                 "file_name": p.file_name} for p in rows]

@app.get("/api/papers/{paper_id}")
def get_paper(paper_id: int):
    with SessionLocal() as db:
        p = db.get(Paper, paper_id)
        if p is None: raise HTTPException(404, "文献不存在")
        return {"id": p.id, "title": p.title, "authors": p.authors, "journal": p.journal,
                "year": p.year, "doi": p.doi, "abstract": p.abstract, "abstract_type": p.abstract_type,
                "keywords": p.keywords, "full_text": p.full_text, "source": p.source,
                "source_url": p.source_url, "fulltext_status": p.fulltext_status,
                "verification_status": p.verification_status, "area_id": p.area_id}

@app.get("/api/papers/{paper_id}/file")
def download_original(paper_id: int):
    with SessionLocal() as db:
        p = db.get(Paper, paper_id)
        if p is None or not p.file_path or not Path(p.file_path).exists():
            raise HTTPException(404, "原始文件不存在")
        return FileResponse(p.file_path, filename=p.file_name or "文献原件")

@app.get("/api/search/external")
async def search_external(q: str = Query(min_length=2), source: str = "openalex", limit: int = Query(10, ge=1, le=30)):
    headers = {"User-Agent": "AcademicResearchLibrary/0.1 (mailto:contact@example.org)"}
    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        try:
            if source == "crossref":
                resp = await client.get("https://api.crossref.org/works", params={"query": q, "rows": limit})
                resp.raise_for_status()
                items = resp.json().get("message", {}).get("items", [])
                results = []
                for x in items:
                    title = (x.get("title") or [""])[0]
                    if not title: continue
                    results.append({"title": title, "authors": "; ".join(" ".join(filter(None,[a.get("given"),a.get("family")])) for a in x.get("author",[])),
                        "journal": (x.get("container-title") or [""])[0], "year": (x.get("published",{}).get("date-parts") or [[None]])[0][0],
                        "doi": x.get("DOI",""), "abstract": re.sub("<[^>]+>", " ", x.get("abstract","")),
                        "source": "Crossref", "source_url": x.get("URL",""), "fulltext_status": "未验证"})
            else:
                resp = await client.get("https://api.openalex.org/works", params={"search": q, "per-page": limit})
                resp.raise_for_status()
                items = resp.json().get("results", [])
                results = []
                for x in items:
                    loc = x.get("primary_location") or {}
                    source_obj = loc.get("source") or {}
                    oa = x.get("open_access") or {}
                    results.append({"title": x.get("title") or "", "authors": "; ".join(a.get("author",{}).get("display_name","") for a in x.get("authorships",[])),
                        "journal": source_obj.get("display_name",""), "year": x.get("publication_year"),
                        "doi": (x.get("doi") or "").replace("https://doi.org/",""), "abstract": "",
                        "source": "OpenAlex", "source_url": x.get("doi") or x.get("id",""),
                        "fulltext_status": "开放获取线索，全文链接待核验" if oa.get("is_oa") else "未标记开放获取"})
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"外部文献服务暂时不可用：{exc.__class__.__name__}")
    return {"source": source, "count": len(results), "results": results}

class SaveExternal(BaseModel):
    title: str
    authors: str = ""
    journal: str = ""
    year: Optional[int] = None
    doi: str = ""
    abstract: str = ""
    source: str
    source_url: str = ""
    area_id: Optional[int] = None

@app.post("/api/papers/external")
def save_external(payload: SaveExternal):
    with SessionLocal() as db:
        if payload.doi:
            found = db.scalar(select(Paper).where(Paper.doi == payload.doi))
            if found: return {"id": found.id, "duplicate": True}
        p = Paper(title=payload.title, authors=payload.authors, journal=payload.journal,
                  year=payload.year, doi=payload.doi, abstract=payload.abstract,
                  abstract_type="来源提供" if payload.abstract else "无摘要",
                  source=payload.source, source_url=payload.source_url,
                  fulltext_status="仅元数据/摘要", verification_status="来源接口返回，待人工核验",
                  area_id=payload.area_id)
        db.add(p); db.commit(); db.refresh(p)
        return {"id": p.id, "saved": True}

@app.get("/api/stats")
def stats():
    with SessionLocal() as db:
        papers = db.scalars(select(Paper)).all()
        return {"total": len(papers), "uploaded": sum(p.source == "个人上传" for p in papers),
                "external": sum(p.source != "个人上传" for p in papers),
                "pending": sum(p.area_id is None for p in papers)}
