#!/usr/bin/env python3
"""学术智库 SQLite 与上传文件备份/校验/恢复工具。

示例：
  python backup_data.py backup
  python backup_data.py verify --backup-dir data/backups/备份目录
  python backup_data.py restore --backup-dir data/backups/备份目录 --database data/restored.db --uploads data/restored_uploads --yes

备份输出是一个目录，包含 database.sqlite3、uploads.zip、manifest.json。
该工具不上传云端；请将生成的备份目录另行复制到独立存储位置。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DEFAULT_BACKUP_DIR = DATA_DIR / "backups"
MANIFEST_NAME = "manifest.json"
DB_NAME = "database.sqlite3"
UPLOADS_NAME = "uploads.zip"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_database_path() -> Path:
    raw_url = os.getenv("ACADEMIC_DATABASE_URL", f"sqlite:///{(DATA_DIR / 'academic_library.db').as_posix()}")
    url = make_url(raw_url)
    if url.get_backend_name() != "sqlite":
        raise RuntimeError("当前备份工具仅支持 SQLite；检测到非 SQLite 数据库，请使用对应数据库的原生备份方案。")
    database = url.database
    if not database or database == ":memory:" or database.startswith("file:"):
        raise RuntimeError("不支持内存数据库或 SQLite URI 模式；请配置本地文件型 SQLite 数据库。")
    path = Path(database)
    return path if path.is_absolute() else (ROOT / path).resolve()


def snapshot_sqlite(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"数据库文件不存在：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(source), timeout=30) as src:
        with sqlite3.connect(str(destination), timeout=30) as dst:
            src.backup(dst, pages=256, sleep=0.05)
            result = dst.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"备份数据库完整性检查失败：{result}")


def archive_uploads(source_dir: Path, destination: Path) -> list[dict]:
    files: list[dict] = []
    source_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(source_dir.rglob("*")):
            if path.is_symlink():
                raise RuntimeError(f"上传目录包含符号链接，拒绝备份：{path}")
            if not path.is_file():
                continue
            relative = path.relative_to(source_dir).as_posix()
            files.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
            archive.write(path, arcname=relative)
    return files


def make_backup(output_root: Path, uploads_source: Path = UPLOAD_DIR) -> Path:
    database = sqlite_database_path()
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    final_dir = output_root / f"academic-backup-{stamp}"
    temp_dir = Path(tempfile.mkdtemp(prefix=".academic-backup-", dir=output_root))
    try:
        db_copy = temp_dir / DB_NAME
        uploads_zip = temp_dir / UPLOADS_NAME
        snapshot_sqlite(database, db_copy)
        upload_files = archive_uploads(uploads_source, uploads_zip)
        manifest = {
            "format_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "database_source": str(database),
            "database_file": DB_NAME,
            "database_sha256": sha256_file(db_copy),
            "uploads_archive": UPLOADS_NAME,
            "uploads_archive_sha256": sha256_file(uploads_zip),
            "upload_file_count": len(upload_files),
            "upload_files": upload_files,
            "notes": "SQLite 使用在线备份 API；上传文件在归档期间仍可能变化。高写入场景请暂停上传后执行备份。",
        }
        (temp_dir / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_dir.rename(final_dir)
        return final_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def safe_archive_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError(f"备份包含不安全的文件路径：{name}")
    return path


def verify_backup(backup_dir: Path) -> dict:
    manifest_path = backup_dir / MANIFEST_NAME
    db_path = backup_dir / DB_NAME
    uploads_path = backup_dir / UPLOADS_NAME
    if not manifest_path.is_file() or not db_path.is_file() or not uploads_path.is_file():
        raise RuntimeError("备份目录不完整，缺少 manifest.json、database.sqlite3 或 uploads.zip。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1:
        raise RuntimeError("不支持的备份格式版本。")
    if sha256_file(db_path) != manifest.get("database_sha256"):
        raise RuntimeError("数据库备份 SHA-256 校验失败。")
    if sha256_file(uploads_path) != manifest.get("uploads_archive_sha256"):
        raise RuntimeError("上传文件归档 SHA-256 校验失败。")
    with sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True) as db:
        result = db.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"数据库完整性检查失败：{result}")
    expected = {item["path"]: item for item in manifest.get("upload_files", [])}
    seen: set[str] = set()
    with zipfile.ZipFile(uploads_path, "r") as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename
            safe_archive_path(name)
            if name in seen or name not in expected:
                raise RuntimeError(f"归档文件清单与 manifest 不一致：{name}")
            seen.add(name)
            digest = hashlib.sha256()
            size = 0
            with archive.open(info, "r") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            item = expected[name]
            if size != item["size"] or digest.hexdigest() != item["sha256"]:
                raise RuntimeError(f"上传文件校验失败：{name}")
    if seen != set(expected):
        raise RuntimeError("上传文件归档缺少 manifest 中记录的文件。")
    return manifest


def restore_backup(backup_dir: Path, target_database: Path, target_uploads: Path) -> None:
    verify_backup(backup_dir)
    target_database = target_database.resolve()
    target_uploads = target_uploads.resolve()
    if target_database.exists() or target_uploads.exists():
        raise RuntimeError("恢复目标已存在。为避免覆盖数据，请选择两个尚不存在的目标路径。")
    target_database.parent.mkdir(parents=True, exist_ok=True)
    target_uploads.parent.mkdir(parents=True, exist_ok=True)
    db_temp = target_database.with_name(target_database.name + ".restore-tmp")
    uploads_temp = Path(tempfile.mkdtemp(prefix=".uploads-restore-", dir=target_uploads.parent))
    try:
        with sqlite3.connect(str(backup_dir / DB_NAME)) as src:
            with sqlite3.connect(str(db_temp)) as dst:
                src.backup(dst)
                result = dst.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise RuntimeError(f"恢复数据库完整性检查失败：{result}")
        with zipfile.ZipFile(backup_dir / UPLOADS_NAME, "r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                relative = safe_archive_path(info.filename)
                destination = uploads_temp.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
        uploads_temp.replace(target_uploads)
        try:
            db_temp.replace(target_database)
        except Exception:
            shutil.rmtree(target_uploads, ignore_errors=True)
            raise
    except Exception:
        db_temp.unlink(missing_ok=True)
        shutil.rmtree(uploads_temp, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="学术智库数据备份、校验与恢复")
    sub = parser.add_subparsers(dest="command", required=True)
    backup_parser = sub.add_parser("backup", help="创建 SQLite 与上传文件备份")
    backup_parser.add_argument("--output", type=Path, default=DEFAULT_BACKUP_DIR, help="备份输出目录")
    backup_parser.add_argument("--uploads", type=Path, default=UPLOAD_DIR, help="要归档的上传文件目录")
    verify_parser = sub.add_parser("verify", help="校验备份完整性")
    verify_parser.add_argument("--backup-dir", type=Path, required=True)
    restore_parser = sub.add_parser("restore", help="恢复到新的目标路径，不覆盖现有文件")
    restore_parser.add_argument("--backup-dir", type=Path, required=True)
    restore_parser.add_argument("--database", type=Path, required=True, help="新的 SQLite 文件路径")
    restore_parser.add_argument("--uploads", type=Path, required=True, help="新的上传文件目录")
    restore_parser.add_argument("--yes", action="store_true", help="确认执行恢复操作")
    args = parser.parse_args()
    try:
        if args.command == "backup":
            result = make_backup(args.output.resolve(), args.uploads.resolve())
            manifest = verify_backup(result)
            print(json.dumps({"status": "success", "backup_dir": str(result), "upload_file_count": manifest["upload_file_count"]}, ensure_ascii=False))
        elif args.command == "verify":
            manifest = verify_backup(args.backup_dir.resolve())
            print(json.dumps({"status": "verified", "created_at_utc": manifest["created_at_utc"], "upload_file_count": manifest["upload_file_count"]}, ensure_ascii=False))
        elif args.command == "restore":
            if not args.yes:
                raise RuntimeError("恢复操作需要显式添加 --yes 参数确认。")
            restore_backup(args.backup_dir.resolve(), args.database, args.uploads)
            print(json.dumps({"status": "restored", "database": str(args.database.resolve()), "uploads": str(args.uploads.resolve())}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
