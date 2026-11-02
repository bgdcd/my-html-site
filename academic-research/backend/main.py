from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from io import BytesIO
from urllib.parse import quote
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import fitz
import httpx
from docx import Document
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, select, or_, inspect, text as sql_text
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
    text_segments: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(80), default="个人上传")
    source_url: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(Text, default="")
    file_name: Mapped[str] = mapped_column(String(500), default="")
    file_sha256: Mapped[str] = mapped_column(String(64), default="")
    fulltext_status: Mapped[str] = mapped_column(String(40), default="未获取")
    verification_status: Mapped[str] = mapped_column(String(40), default="待核验")
    area_id: Mapped[Optional[int]] = mapped_column(ForeignKey("research_areas.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class EvidenceCard(Base):
    __tablename__ = "evidence_cards"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(80), default="研究结论")
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    source_text: Mapped[str] = mapped_column(Text, default="")
    locator: Mapped[str] = mapped_column(String(300), default="")
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    paragraph_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confidence: Mapped[str] = mapped_column(String(40), default="待核验")
    verification_status: Mapped[str] = mapped_column(String(40), default="待核验")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class ReviewProject(Base):
    __tablename__ = "review_projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    topic: Mapped[str] = mapped_column(String(1000), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class ReviewParagraph(Base):
    __tablename__ = "review_paragraphs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("review_projects.id"), nullable=False, index=True)
    heading: Mapped[str] = mapped_column(String(500), default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[str] = mapped_column(Text, default="[]")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

Base.metadata.create_all(engine)

def migrate_schema():
    columns = {column['name'] for column in inspect(engine).get_columns('papers')}
    if 'text_segments' not in columns:
        with engine.begin() as connection:
            connection.execute(sql_text("ALTER TABLE papers ADD COLUMN text_segments TEXT DEFAULT '[]'"))

migrate_schema()

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
        paper_count = len(db.scalars(select(Paper.id)).all())
        area_count = len(db.scalars(select(ResearchArea.id)).all())
    return {"status": "ok", "database": "connected", "papers": paper_count, "areas": area_count}

class AreaMove(BaseModel):
    parent_id: Optional[int] = None

class AreaMerge(BaseModel):
    target_area_id: int

@app.post("/api/areas/{area_id}/move")
def move_area(area_id: int, payload: AreaMove):
    with SessionLocal() as db:
        item = db.get(ResearchArea, area_id)
        if item is None: raise HTTPException(404, "分类不存在")
        if payload.parent_id == area_id: raise HTTPException(400, "分类不能移动到自身下面")
        if payload.parent_id is not None and db.get(ResearchArea, payload.parent_id) is None: raise HTTPException(404, "上级分类不存在")
        parent_id = payload.parent_id
        while parent_id is not None:
            if parent_id == area_id: raise HTTPException(400, "不能移动到自己的下级分类")
            parent = db.get(ResearchArea, parent_id)
            parent_id = parent.parent_id if parent else None
        item.parent_id = payload.parent_id
        db.commit()
        return {"id": item.id, "name": item.name, "parent_id": item.parent_id}

@app.post("/api/areas/{area_id}/merge")
def merge_area(area_id: int, payload: AreaMerge):
    with SessionLocal() as db:
        source = db.get(ResearchArea, area_id)
        target = db.get(ResearchArea, payload.target_area_id)
        if source is None or target is None: raise HTTPException(404, "待合并分类不存在")
        if source.id == target.id: raise HTTPException(400, "不能与自身合并")
        parent_id = target.parent_id
        while parent_id is not None:
            if parent_id == source.id: raise HTTPException(400, "不能合并到自身的下级分类")
            parent = db.get(ResearchArea, parent_id)
            parent_id = parent.parent_id if parent else None
        papers = db.scalars(select(Paper).where(Paper.area_id == source.id)).all()
        for paper in papers: paper.area_id = target.id
        children = db.scalars(select(ResearchArea).where(ResearchArea.parent_id == source.id)).all()
        for child in children: child.parent_id = target.id
        db.delete(source); db.commit()
        return {"merged_area_id": area_id, "target_area_id": target.id, "moved_papers": len(papers), "moved_subareas": len(children)}

@app.delete("/api/areas/{area_id}")
def delete_area(area_id: int):
    with SessionLocal() as db:
        item = db.get(ResearchArea, area_id)
        if item is None: raise HTTPException(404, "分类不存在")
        pc = len(db.scalars(select(Paper.id).where(Paper.area_id == area_id)).all())
        cc = len(db.scalars(select(ResearchArea.id).where(ResearchArea.parent_id == area_id)).all())
        if pc or cc: raise HTTPException(409, "分类仍有关联文献或下级分类，请先移动或合并")
        db.delete(item); db.commit()
        return {"deleted": True, "id": area_id}

class PaperAreaInput(BaseModel):
    area_id: Optional[int] = None

@app.patch("/api/papers/{paper_id}/area")
def assign_paper_area(paper_id: int, payload: PaperAreaInput):
    with SessionLocal() as db:
        paper = db.get(Paper, paper_id)
        if paper is None: raise HTTPException(404, "文献不存在")
        if payload.area_id is not None and db.get(ResearchArea, payload.area_id) is None: raise HTTPException(404, "目标分类不存在")
        paper.area_id = payload.area_id; db.commit()
        return {"id": paper.id, "area_id": paper.area_id, "classification": "待确认" if paper.area_id is None else "已归类"}

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
        if not payload.name.strip(): raise HTTPException(400, "分类名称不能为空")
        if payload.parent_id == area_id: raise HTTPException(400, "分类不能设置自身为上级")
        if payload.parent_id is not None and db.get(ResearchArea, payload.parent_id) is None:
            raise HTTPException(404, "上级分类不存在")
        parent_id = payload.parent_id
        while parent_id is not None:
            if parent_id == area_id: raise HTTPException(400, "不能设置为自己的下级分类")
            parent = db.get(ResearchArea, parent_id)
            parent_id = parent.parent_id if parent else None
        item.name = payload.name.strip()
        item.parent_id = payload.parent_id
        item.is_placeholder = False
        db.commit()
        return {"id": item.id, "name": item.name, "parent_id": item.parent_id}

def extract_pdf_with_segments(path: Path):
    doc = fitz.open(path)
    try:
        chunks, segments = [], []
        cursor = 0
        for index, page in enumerate(doc):
            value = page.get_text("text").strip()
            if not value:
                continue
            if chunks:
                cursor += 1
            start = cursor
            chunks.append(value)
            cursor += len(value)
            segments.append({"kind": "pdf_page", "page_number": index + 1,
                             "char_start": start, "char_end": cursor, "text": value})
        return "\n".join(chunks).strip(), segments
    finally:
        doc.close()

def extract_docx_with_segments(path: Path):
    doc = Document(path)
    chunks, segments = [], []
    cursor = 0
    def add_segment(value, kind, number):
        nonlocal cursor
        value = (value or "").strip()
        if not value:
            return
        if chunks:
            cursor += 1
        start = cursor
        chunks.append(value)
        cursor += len(value)
        segments.append({"kind": kind, "number": number, "char_start": start,
                         "char_end": cursor, "text": value})
    paragraph_number = 0
    for paragraph in doc.paragraphs:
        if paragraph.text.strip():
            paragraph_number += 1
            add_segment(paragraph.text, "docx_paragraph", paragraph_number)
    row_number = 0
    for table in doc.tables:
        for row in table.rows:
            value = " | ".join(cell.text for cell in row.cells)
            if value.strip():
                row_number += 1
                add_segment(value, "docx_table_row", row_number)
    return "\n".join(chunks).strip(), segments

def extract_pdf(path: Path) -> str:
    return extract_pdf_with_segments(path)[0]

def extract_docx(path: Path) -> str:
    return extract_docx_with_segments(path)[0]

def guess_title(text: str, filename: str, metadata_title: str = "") -> str:
    candidate = re.sub(r"\s+", " ", (metadata_title or "")).strip()
    if 5 <= len(candidate) <= 500 and not re.match(r"(?i)^(untitled|microsoft word|扫描件)$", candidate):
        return candidate
    for line in [re.sub(r"\s+", " ", x).strip() for x in text.splitlines()[:45]]:
        if 8 <= len(line) <= 180 and not re.match(r"^(第\s*\d+\s*页|https?://|doi:)", line, re.I):
            return line[:500]
    return Path(filename).stem[:500] or "未识别标题"

def extract_doi(text: str) -> str:
    match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, re.I)
    return match.group(0).rstrip(".,;:)") if match else ""

def extract_candidate_metadata(path: Path, suffix: str, text: str) -> dict:
    result = {"title": "", "authors": "", "year": None, "abstract": "", "keywords": "", "doi": ""}
    try:
        if suffix == ".pdf":
            with fitz.open(path) as doc:
                meta = doc.metadata or {}
                result["title"] = meta.get("title") or ""
                result["authors"] = meta.get("author") or ""
                date_text = meta.get("creationDate") or meta.get("modDate") or ""
                year_match = re.search(r"(?:19|20)\d{2}", date_text)
                if year_match: result["year"] = int(year_match.group(0))
                result["keywords"] = meta.get("keywords") or ""
        else:
            props = Document(path).core_properties
            result["title"] = props.title or ""
            result["authors"] = props.author or ""
            if props.created and props.created.year >= 1900: result["year"] = props.created.year
            result["keywords"] = props.keywords or ""
    except Exception:
        pass
    result["doi"] = extract_doi(text)
    abstract_match = re.search(r"(?:摘要|abstract)\s*[:：]?\s*(.{80,1800}?)(?=\n\s*(?:关键词|关键字|引言|\d+[.、]|key\s*words)\b)", text, re.I | re.S)
    if abstract_match:
        result["abstract"] = re.sub(r"\s+", " ", abstract_match.group(1)).strip()
    if not result["keywords"]:
        kw_match = re.search(r"(?:关键词|关键字|key\s*words)\s*[:：]?\s*([^\n]{2,300})", text, re.I)
        if kw_match: result["keywords"] = kw_match.group(1).strip()
    return result

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
        text, text_segments = extract_pdf_with_segments(target) if suffix == ".pdf" else extract_docx_with_segments(target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(422, f"文件解析失败：{exc.__class__.__name__}")
    candidates = extract_candidate_metadata(target, suffix, text)
    title = guess_title(text, filename, candidates["title"])
    area_id = suggested_area(text)
    with SessionLocal() as db:
        existing = db.scalar(select(Paper).where(Paper.file_sha256 == digest))
        if existing:
            target.unlink(missing_ok=True)
            return {"id": existing.id, "duplicate": True, "message": "该文件已在文献库中"}
        paper = Paper(title=title, authors=candidates["authors"], year=candidates["year"],
                      doi=candidates["doi"], abstract=candidates["abstract"],
                      abstract_type="自动提取候选" if candidates["abstract"] else "未识别",
                      keywords=candidates["keywords"], full_text=text, text_segments=json.dumps(text_segments, ensure_ascii=False), file_path=str(target),
                      file_name=filename, file_sha256=digest, fulltext_status="已解析" if text else "未提取到文本",
                      verification_status="自动提取候选，待人工核验", area_id=area_id, source="个人上传")
        db.add(paper); db.commit(); db.refresh(paper)
        return {"id": paper.id, "title": paper.title, "characters_extracted": len(text),
                "area_id": area_id, "classification": "建议分类" if area_id else "待确认分类",
                "fulltext_status": paper.fulltext_status, "metadata_candidates": candidates,
                "metadata_note": "自动提取结果仅为候选值，请核对原文后确认"}

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
                "keywords": p.keywords, "full_text": p.full_text, "text_segments": json.loads(p.text_segments or "[]"), "file_name": p.file_name, "source": p.source,
                "source_url": p.source_url, "fulltext_status": p.fulltext_status,
                "verification_status": p.verification_status, "area_id": p.area_id}

class PaperMetadataUpdate(BaseModel):
    title: Optional[str] = None
    authors: Optional[str] = None
    journal: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    abstract: Optional[str] = None
    abstract_type: Optional[str] = None
    keywords: Optional[str] = None
    verification_status: Optional[str] = None

@app.patch("/api/papers/{paper_id}/metadata")
def update_paper_metadata(paper_id: int, payload: PaperMetadataUpdate):
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("abstract_type") not in (None, "来源提供", "自动提取候选", "人工核验", "未识别"):
        raise HTTPException(400, "摘要类型只能选择：来源提供、自动提取候选、人工核验或未识别")
    if "abstract" in changes and "abstract_type" not in changes:
        changes["abstract_type"] = "人工核验"
    if "title" in changes and not (changes["title"] or "").strip():
        raise HTTPException(400, "题名不能为空")
    with SessionLocal() as db:
        paper = db.get(Paper, paper_id)
        if paper is None: raise HTTPException(404, "文献不存在")
        for key, value in changes.items():
            setattr(paper, key, value.strip() if isinstance(value, str) else value)
        if "verification_status" not in changes:
            paper.verification_status = "人工核验"
        db.commit()
        return {"id": paper.id, "updated_fields": list(changes.keys()),
                "verification_status": paper.verification_status}

@app.get("/api/papers/{paper_id}/file")
def download_original(paper_id: int):
    with SessionLocal() as db:
        p = db.get(Paper, paper_id)
        if p is None or not p.file_path or not Path(p.file_path).exists():
            raise HTTPException(404, "原始文件不存在")
        return FileResponse(p.file_path, filename=p.file_name or "文献原件")

@app.get("/api/search/external")
async def search_external(q: str = Query(min_length=2), source: str = "openalex", limit: int = Query(10, ge=1, le=30)):
    headers = {"User-Agent": "AcademicResearchLibrary/0.1 (no-contact-email-configured)"}
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

@app.get("/api/search/fulltext")
def search_fulltext(q: str = Query(min_length=1), limit: int = Query(50, ge=1, le=200), context: int = Query(120, ge=30, le=500)):
    """在已保存全文中查找关键词，并返回可回溯的原文上下文；不推断PDF页码。"""
    keyword = q.strip()
    if not keyword:
        raise HTTPException(400, "请输入检索词")
    with SessionLocal() as db:
        rows = db.scalars(
            select(Paper).where(Paper.full_text.like(f"%{keyword}%"))
            .order_by(Paper.created_at.desc()).limit(limit)
        ).all()
        results = []
        for paper in rows:
            source_text = paper.full_text or ""
            positions = [m.start() for m in re.finditer(re.escape(keyword), source_text, re.I)]
            if not positions:
                continue
            start = max(0, positions[0] - context)
            end = min(len(source_text), positions[0] + len(keyword) + context)
            snippet = source_text[start:end]
            results.append({
                "id": paper.id, "title": paper.title, "authors": paper.authors,
                "year": paper.year, "source": paper.source, "source_url": paper.source_url,
                "fulltext_status": paper.fulltext_status, "verification_status": paper.verification_status,
                "match_count": len(positions), "first_match_offset": positions[0],
                "snippet": ("…" if start else "") + snippet + ("…" if end < len(source_text) else ""),
                "snippet_start": start, "snippet_end": end,
                "location_note": "全文字符位置；当前未保存可靠页码"
            })
        return {"query": keyword, "count": len(results), "results": results}


class EvidenceCardInput(BaseModel):
    evidence_type: str = Field(default="研究结论", min_length=1, max_length=80)
    claim: str = Field(min_length=1, max_length=5000)
    source_text: str = Field(default="", max_length=12000)
    locator: str = Field(default="", max_length=300)
    page_number: Optional[int] = Field(default=None, ge=1)
    paragraph_number: Optional[int] = Field(default=None, ge=1)
    confidence: str = Field(default="待核验", max_length=40)
    verification_status: str = Field(default="待核验", max_length=40)
    notes: str = Field(default="", max_length=5000)

@app.get("/api/papers/{paper_id}/evidence")
def list_evidence_cards(paper_id: int):
    with SessionLocal() as db:
        if db.get(Paper, paper_id) is None:
            raise HTTPException(404, "文献不存在")
        rows = db.scalars(select(EvidenceCard).where(EvidenceCard.paper_id == paper_id).order_by(EvidenceCard.created_at.desc())).all()
        return [{"id": x.id, "paper_id": x.paper_id, "evidence_type": x.evidence_type,
                 "claim": x.claim, "source_text": x.source_text, "locator": x.locator,
                 "page_number": x.page_number, "paragraph_number": x.paragraph_number,
                 "confidence": x.confidence, "verification_status": x.verification_status,
                 "notes": x.notes, "created_at": x.created_at.isoformat()} for x in rows]

@app.post("/api/papers/{paper_id}/evidence")
def create_evidence_card(paper_id: int, payload: EvidenceCardInput):
    with SessionLocal() as db:
        if db.get(Paper, paper_id) is None:
            raise HTTPException(404, "文献不存在")
        item = EvidenceCard(paper_id=paper_id, **payload.model_dump())
        db.add(item); db.commit(); db.refresh(item)
        return {"id": item.id, "paper_id": paper_id, "saved": True}

@app.patch("/api/evidence/{evidence_id}")
def update_evidence_card(evidence_id: int, payload: EvidenceCardInput):
    with SessionLocal() as db:
        item = db.get(EvidenceCard, evidence_id)
        if item is None:
            raise HTTPException(404, "证据卡片不存在")
        for key, value in payload.model_dump().items():
            setattr(item, key, value)
        db.commit()
        return {"id": item.id, "updated": True}

@app.delete("/api/evidence/{evidence_id}")
def delete_evidence_card(evidence_id: int):
    with SessionLocal() as db:
        item = db.get(EvidenceCard, evidence_id)
        if item is None:
            raise HTTPException(404, "证据卡片不存在")
        db.delete(item); db.commit()
        return {"id": evidence_id, "deleted": True}

@app.get("/api/review/evidence-matrix")
def evidence_matrix(paper_ids: str = Query(min_length=1, description="逗号分隔的文献编号")):
    try:
        ids = list(dict.fromkeys(int(value.strip()) for value in paper_ids.split(",") if value.strip()))
    except ValueError:
        raise HTTPException(400, "文献编号必须为数字")
    if not ids or len(ids) > 50 or any(value < 1 for value in ids):
        raise HTTPException(400, "请选择1至50篇有效文献")
    with SessionLocal() as db:
        papers = db.scalars(select(Paper).where(Paper.id.in_(ids))).all()
        found = {paper.id: paper for paper in papers}
        missing = [value for value in ids if value not in found]
        if missing:
            raise HTTPException(404, "以下文献编号不存在：" + ", ".join(map(str, missing)))
        cards = db.scalars(select(EvidenceCard).where(EvidenceCard.paper_id.in_(ids)).order_by(EvidenceCard.evidence_type, EvidenceCard.paper_id, EvidenceCard.id)).all()
        grouped = {}
        for card in cards:
            grouped.setdefault(card.evidence_type, []).append({
                "id": card.id, "paper_id": card.paper_id, "claim": card.claim,
                "source_text": card.source_text, "locator": card.locator,
                "page_number": card.page_number, "paragraph_number": card.paragraph_number,
                "verification_status": card.verification_status,
                "confidence": card.confidence, "notes": card.notes
            })
        return {
            "paper_count": len(papers), "evidence_count": len(cards),
            "papers": [{"id": p.id, "title": p.title, "authors": p.authors,
                        "journal": p.journal, "year": p.year, "doi": p.doi,
                        "source": p.source, "source_url": p.source_url,
                        "verification_status": p.verification_status} for p in papers],
            "groups": [{"evidence_type": key, "count": len(value), "items": value}
                       for key, value in grouped.items()],
            "notice": "本结果仅汇总研究者已建立的证据卡片，不自动推断论文结论；请核验原文后再形成综述。"
        }


class ReviewProjectInput(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    topic: str = Field(default="", max_length=1000)
    notes: str = Field(default="", max_length=10000)

class ReviewParagraphInput(BaseModel):
    heading: str = Field(default="", max_length=500)
    content: str = Field(min_length=1, max_length=20000)
    evidence_ids: list[int] = Field(default_factory=list, max_length=100)
    sort_order: int = 0

def serialize_review_project(db, project):
    paragraphs = db.scalars(select(ReviewParagraph).where(
        ReviewParagraph.project_id == project.id).order_by(
        ReviewParagraph.sort_order, ReviewParagraph.id)).all()
    output = []
    for paragraph in paragraphs:
        ids = json.loads(paragraph.evidence_ids or "[]")
        cards = db.scalars(select(EvidenceCard).where(EvidenceCard.id.in_(ids))).all() if ids else []
        card_map = {card.id: card for card in cards}
        citations = []
        for evidence_id in ids:
            card = card_map.get(evidence_id)
            if card is None:
                continue
            paper = db.get(Paper, card.paper_id)
            if paper is None:
                continue
            citations.append({
                "evidence_id": card.id, "paper_id": paper.id,
                "title": paper.title, "authors": paper.authors,
                "journal": paper.journal, "year": paper.year, "doi": paper.doi,
                "claim": card.claim, "source_text": card.source_text,
                "locator": card.locator, "page_number": card.page_number,
                "paragraph_number": card.paragraph_number,
                "verification_status": card.verification_status
            })
        output.append({"id": paragraph.id, "heading": paragraph.heading,
                       "content": paragraph.content, "evidence_ids": ids,
                       "sort_order": paragraph.sort_order, "citations": citations})
    return {"id": project.id, "title": project.title, "topic": project.topic,
            "notes": project.notes, "created_at": project.created_at.isoformat(),
            "paragraphs": output}

@app.get("/api/review/projects")
def list_review_projects():
    with SessionLocal() as db:
        projects = db.scalars(select(ReviewProject).order_by(ReviewProject.created_at.desc())).all()
        return [{"id": p.id, "title": p.title, "topic": p.topic,
                 "created_at": p.created_at.isoformat()} for p in projects]

@app.post("/api/review/projects")
def create_review_project(payload: ReviewProjectInput):
    with SessionLocal() as db:
        project = ReviewProject(**payload.model_dump())
        db.add(project); db.commit(); db.refresh(project)
        return {"id": project.id, "created": True}

@app.get("/api/review/projects/{project_id}")
def get_review_project(project_id: int):
    with SessionLocal() as db:
        project = db.get(ReviewProject, project_id)
        if project is None:
            raise HTTPException(404, "综述项目不存在")
        return serialize_review_project(db, project)

@app.patch("/api/review/projects/{project_id}")
def update_review_project(project_id: int, payload: ReviewProjectInput):
    with SessionLocal() as db:
        project = db.get(ReviewProject, project_id)
        if project is None:
            raise HTTPException(404, "综述项目不存在")
        for key, value in payload.model_dump().items():
            setattr(project, key, value)
        db.commit()
        return {"id": project.id, "updated": True}

@app.delete("/api/review/projects/{project_id}")
def delete_review_project(project_id: int):
    with SessionLocal() as db:
        project = db.get(ReviewProject, project_id)
        if project is None:
            raise HTTPException(404, "综述项目不存在")
        paragraphs = db.scalars(select(ReviewParagraph).where(ReviewParagraph.project_id == project_id)).all()
        for paragraph in paragraphs:
            db.delete(paragraph)
        db.delete(project); db.commit()
        return {"id": project_id, "deleted": True}

@app.post("/api/review/projects/{project_id}/paragraphs")
def create_review_paragraph(project_id: int, payload: ReviewParagraphInput):
    with SessionLocal() as db:
        if db.get(ReviewProject, project_id) is None:
            raise HTTPException(404, "综述项目不存在")
        if payload.evidence_ids:
            cards = db.scalars(select(EvidenceCard).where(EvidenceCard.id.in_(payload.evidence_ids))).all()
            if len({card.id for card in cards}) != len(set(payload.evidence_ids)):
                raise HTTPException(400, "引用的证据卡片不存在")
        paragraph = ReviewParagraph(project_id=project_id, heading=payload.heading,
            content=payload.content, evidence_ids=json.dumps(list(dict.fromkeys(payload.evidence_ids))),
            sort_order=payload.sort_order)
        db.add(paragraph); db.commit(); db.refresh(paragraph)
        return {"id": paragraph.id, "created": True}

@app.patch("/api/review/paragraphs/{paragraph_id}")
def update_review_paragraph(paragraph_id: int, payload: ReviewParagraphInput):
    with SessionLocal() as db:
        paragraph = db.get(ReviewParagraph, paragraph_id)
        if paragraph is None:
            raise HTTPException(404, "综述段落不存在")
        if payload.evidence_ids:
            cards = db.scalars(select(EvidenceCard).where(EvidenceCard.id.in_(payload.evidence_ids))).all()
            if len({card.id for card in cards}) != len(set(payload.evidence_ids)):
                raise HTTPException(400, "引用的证据卡片不存在")
        paragraph.heading = payload.heading
        paragraph.content = payload.content
        paragraph.evidence_ids = json.dumps(list(dict.fromkeys(payload.evidence_ids)))
        paragraph.sort_order = payload.sort_order
        db.commit()
        return {"id": paragraph.id, "updated": True}

@app.delete("/api/review/paragraphs/{paragraph_id}")
def delete_review_paragraph(paragraph_id: int):
    with SessionLocal() as db:
        paragraph = db.get(ReviewParagraph, paragraph_id)
        if paragraph is None:
            raise HTTPException(404, "综述段落不存在")
        db.delete(paragraph); db.commit()
        return {"id": paragraph_id, "deleted": True}


@app.get("/api/review/projects/{project_id}/export.docx")
def export_review_project_docx(project_id: int, style: str = Query("gb", pattern="^(gb|simple)$")):
    with SessionLocal() as db:
        project = db.get(ReviewProject, project_id)
        if project is None:
            raise HTTPException(404, "综述项目不存在")
        data = serialize_review_project(db, project)

    document = Document()
    section = document.sections[0]
    section.top_margin = section.bottom_margin =  section.left_margin = section.right_margin = __import__("docx").shared.Cm(2.54)

    def set_run_font(run, size=12, bold=False):
        run.font.name = "宋体"
        run.font.size = __import__("docx").shared.Pt(size)
        run.bold = bold
        rpr = run._element.get_or_add_rPr()
        rfonts = rpr.rFonts
        if rfonts is not None:
            rfonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia", "宋体")

    title = document.add_heading(data["title"] or "文献综述", 0)
    title.alignment = 1
    for run in title.runs:
        set_run_font(run, 18, True)
    topic = document.add_paragraph()
    topic.alignment = 1
    set_run_font(topic.add_run("研究主题："+(data["topic"] or "未填写")), 10)

    bibliography = []
    number_by_paper = {}
    for paragraph in data["paragraphs"]:
        for citation in paragraph["citations"]:
            paper_id = citation["paper_id"]
            if paper_id not in number_by_paper:
                number_by_paper[paper_id] = len(number_by_paper) + 1
                bibliography.append(citation)

    for index, paragraph in enumerate(data["paragraphs"], 1):
        heading = paragraph["heading"] or f"第{index}部分"
        h = document.add_heading(heading, level=1)
        for run in h.runs:
            set_run_font(run, 14, True)
        markers = list(dict.fromkeys(number_by_paper[c["paper_id"]] for c in paragraph["citations"] if c["paper_id"] in number_by_paper))
        if style == "gb" and markers:
            body_text = paragraph["content"] + " [" + "][".join(str(n) for n in markers) + "]"
        elif style == "simple" and paragraph["citations"]:
            names = []
            for citation in paragraph["citations"]:
                author = (citation.get("authors") or "作者未记录").split("，")[0].split(",")[0]
                label = author + "，" + str(citation.get("year") or "年份缺失")
                if label not in names:
                    names.append(label)
            body_text = paragraph["content"] + "（" + "；".join(names) + "）"
        else:
            body_text = paragraph["content"]
        p = document.add_paragraph()
        p.paragraph_format.first_line_indent = __import__("docx").shared.Cm(0.74)
        p.paragraph_format.line_spacing = 1.5
        set_run_font(p.add_run(body_text), 12)

    document.add_heading("参考文献", level=1)
    for citation in bibliography:
        authors = (citation.get("authors") or "作者信息缺失").strip().replace("，", ", ").replace("、", ", ")
        paper_title = (citation.get("title") or "题名缺失").strip()
        journal = (citation.get("journal") or "").strip()
        year = str(citation.get("year") or "年份缺失")
        doi = (citation.get("doi") or "").strip()
        if style == "gb":
            reference = authors + ". " + paper_title + ("[J]. " + journal + ", " if journal else "[文献类型待核验]. ") + year + (". DOI: " + doi if doi else "") + "."
            number = number_by_paper[citation["paper_id"]]
            reference = f"[{number}] " + reference
        else:
            reference = authors + "（" + year + "）. " + paper_title + ". " + (journal or "出版来源待核验") + (". DOI: " + doi if doi else "") + "."
        p = document.add_paragraph(reference)
        p.paragraph_format.left_indent = __import__("docx").shared.Cm(0.74)
        p.paragraph_format.first_line_indent = __import__("docx").shared.Cm(-0.74)
        for run in p.runs:
            set_run_font(run, 10)

        locators = []
        for paragraph in data["paragraphs"]:
            for item in paragraph["citations"]:
                if item["paper_id"] == citation["paper_id"]:
                    locator = ("第"+str(item["page_number"])+"页" if item.get("page_number") else
                               ("第"+str(item["paragraph_number"])+"段" if item.get("paragraph_number") else ""))
                    if locator and locator not in locators:
                        locators.append(locator)
        if locators:
            note = document.add_paragraph("引用定位："+ "；".join(locators))
            for run in note.runs:
                set_run_font(run, 9)

    note = document.add_paragraph("说明：本报告根据个人文献库中已保存的综述段落和证据卡片生成。参考文献条目仅使用现有元数据；作者、年份、期刊或文献类型缺失时，须对照原文核验。GB/T 7714 格式为基于现有字段的初步排版，尚未完成所有文献类型的完整著录规则校验。")
    for run in note.runs:
        set_run_font(run, 9)

    stream = BytesIO()
    document.save(stream)
    stream.seek(0)
    filename = quote((data["title"] or "文献综述") + ".docx")
    return StreamingResponse(stream, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + filename})

@app.get("/api/stats")
def stats():
    with SessionLocal() as db:
        papers = db.scalars(select(Paper)).all()
        return {"total": len(papers), "uploaded": sum(p.source == "个人上传" for p in papers),
                "external": sum(p.source != "个人上传" for p in papers),
                "pending": sum(p.area_id is None for p in papers)}
