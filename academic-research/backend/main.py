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
        text = extract_pdf(target) if suffix == ".pdf" else extract_docx(target)
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
                      keywords=candidates["keywords"], full_text=text, file_path=str(target),
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
                "keywords": p.keywords, "full_text": p.full_text, "source": p.source,
                "source_url": p.source_url, "fulltext_status": p.fulltext_status,
                "verification_status": p.verification_status, "area_id": p.area_id}

class PaperMetadataUpdate(BaseModel):
    title: Optional[str] = None
    authors: Optional[str] = None
    journal: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    abstract: Optional[str] = None
    keywords: Optional[str] = None
    verification_status: Optional[str] = None

@app.patch("/api/papers/{paper_id}/metadata")
def update_paper_metadata(paper_id: int, payload: PaperMetadataUpdate):
    changes = payload.model_dump(exclude_unset=True)
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


@app.get("/api/stats")
def stats():
    with SessionLocal() as db:
        papers = db.scalars(select(Paper)).all()
        return {"total": len(papers), "uploaded": sum(p.source == "个人上传" for p in papers),
                "external": sum(p.source != "个人上传" for p in papers),
                "pending": sum(p.area_id is None for p in papers)}
