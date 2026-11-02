from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine


def parse_args():
    parser = argparse.ArgumentParser(
        description="将学术智库 SQLite 数据库迁移到一个全新的 PostgreSQL 数据库。"
    )
    parser.add_argument("--source-db", required=True, help="SQLite 数据库文件路径")
    parser.add_argument(
        "--target-url",
        default=os.getenv("ACADEMIC_DATABASE_URL", ""),
        help="PostgreSQL SQLAlchemy URL；也可通过 ACADEMIC_DATABASE_URL 环境变量提供",
    )
    parser.add_argument(
        "--uploads-source",
        help="可选：原始上传文件目录。与 --uploads-destination 同时使用以迁移原件。",
    )
    parser.add_argument(
        "--uploads-destination",
        help="可选：目标运行环境的上传目录；必须是一个尚不存在的目录。",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    return parser.parse_args()


def assert_target_is_postgresql(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise SystemExit("目标数据库必须是 PostgreSQL；本工具不会覆盖 SQLite 数据库。")


def table_row_count(connection, table) -> int:
    return int(connection.execute(select(table.count()) if hasattr(table, "count") else text(
        f'SELECT COUNT(*) FROM "{table.name}"'
    )).scalar_one())


def migrate_uploads(source_dir: Path, destination_dir: Path, paper_table):
    source_dir = source_dir.resolve()
    destination_dir = destination_dir.resolve()
    if not source_dir.is_dir():
        raise SystemExit(f"上传源目录不存在：{source_dir}")
    if destination_dir.exists():
        raise SystemExit(f"上传目标目录已存在，为避免覆盖已拒绝：{destination_dir}")

    with source_dir.open("rb") if source_dir.is_file() else _null_context() as _:
        pass
    for row in paper_table.metadata.bind.connect().execute(select(paper_table)).mappings():
        raw_path = row.get("file_path") or ""
        if not raw_path:
            continue
        file_path = Path(raw_path).resolve()
        try:
            file_path.relative_to(source_dir)
        except ValueError as exc:
            raise SystemExit(f"文献原件路径不在指定上传目录内：{raw_path}") from exc
        if not file_path.is_file():
            raise SystemExit(f"找不到文献原件：{file_path}")

    shutil.copytree(source_dir, destination_dir)
    return source_dir, destination_dir


class _null_context:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False


def main():
    args = parse_args()
    source_db = Path(args.source_db).expanduser().resolve()
    if not source_db.is_file():
        raise SystemExit(f"SQLite 数据库文件不存在：{source_db}")
    if not args.target_url:
        raise SystemExit("请使用 --target-url 或 ACADEMIC_DATABASE_URL 提供 PostgreSQL 连接串。")
    if bool(args.uploads_source) != bool(args.uploads_destination):
        raise SystemExit("--uploads-source 与 --uploads-destination 必须同时提供。")
    if args.batch_size < 1 or args.batch_size > 10000:
        raise SystemExit("--batch-size 必须在1至10000之间。")

    source_url = "sqlite:///" + source_db.as_posix()
    os.environ["ACADEMIC_DATABASE_URL"] = source_url
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    # 导入应用模型以执行现有 SQLite 结构迁移，并使用与当前版本一致的表定义。
    from backend.main import Base, Paper, engine as source_engine

    target_engine = create_engine(args.target_url, pool_pre_ping=True)
    assert_target_is_postgresql(target_engine)
    Base.metadata.create_all(target_engine)

    tables = Base.metadata.sorted_tables
    inspector = inspect(target_engine)
    missing_tables = [table.name for table in tables if not inspector.has_table(table.name)]
    if missing_tables:
        raise SystemExit("目标库缺少数据表：" + ", ".join(missing_tables))

    # 目标库必须是全新空库。不会自动清空、覆盖或合并已有数据。
    with target_engine.connect() as connection:
        non_empty = []
        for table in tables:
            if table_row_count(connection, table) > 0:
                non_empty.append(table.name)
    if non_empty:
        raise SystemExit(
            "目标 PostgreSQL 数据库并非空库，已停止以防覆盖。请新建空数据库后重试；非空表："
            + ", ".join(non_empty)
        )

    target_uploads = None
    if args.uploads_source:
        target_uploads = migrate_uploads(Path(args.uploads_source), Path(args.uploads_destination), Paper.__table__)

    copied = {}
    try:
        with source_engine.connect() as source_connection, target_engine.begin() as target_connection:
            source_inspector = inspect(source_engine)
            source_missing = [table.name for table in tables if not source_inspector.has_table(table.name)]
            if source_missing:
                raise RuntimeError("源 SQLite 数据库缺少表：" + ", ".join(source_missing))

            for table in tables:
                source_columns = {column["name"] for column in source_inspector.get_columns(table.name)}
                target_columns = {column["name"] for column in inspector.get_columns(table.name)}
                expected_columns = {column.name for column in table.columns}
                if not expected_columns.issubset(source_columns) or not expected_columns.issubset(target_columns):
                    raise RuntimeError(
                        f"表 {table.name} 的字段与当前版本不一致。"
                        f"需要字段：{sorted(expected_columns)}；源字段：{sorted(source_columns)}；"
                        f"目标字段：{sorted(target_columns)}"
                    )

                total = 0
                result = source_connection.execute(select(table))
                while True:
                    batch = [dict(row) for row in result.mappings().fetchmany(args.batch_size)]
                    if not batch:
                        break
                    if target_uploads and table.name == "papers":
                        destination_dir = target_uploads[1]
                        for row in batch:
                            raw_path = row.get("file_path") or ""
                            if raw_path:
                                row["file_path"] = str(destination_dir / Path(raw_path).name)
                    target_connection.execute(table.insert(), batch)
                    total += len(batch)
                copied[table.name] = total

            # 显式写入主键后，校准 PostgreSQL 自动递增序列，避免后续新增记录发生主键冲突。
            for table in tables:
                if "id" not in table.columns:
                    continue
                quoted_table = target_engine.dialect.identifier_preparer.quote(table.name)
                quoted_id = target_engine.dialect.identifier_preparer.quote("id")
                sequence_table_name = table.name.replace('"', '""')
                target_connection.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                        f"COALESCE(MAX({quoted_id}), 1), MAX({quoted_id}) IS NOT NULL) "
                        f"FROM {quoted_table}"
                    ),
                    {"table_name": sequence_table_name},
                )
    except Exception:
        if target_uploads and target_uploads[1].exists():
            shutil.rmtree(target_uploads[1], ignore_errors=True)
        raise

    print("迁移完成。")
    print("逐表迁移数量：")
    for name, count in copied.items():
        print(f"  {name}: {count}")
    if target_uploads:
        print(f"原始上传文件已复制到：{target_uploads[1]}")
        print("请确认运行中的应用将该目录挂载为 data/uploads。")
    else:
        print("未复制原始上传文件。请确保目标应用仍能从数据库 file_path 指向的位置读取原件。")
    print("请在切换正式流量前核对总记录数、原件可读性和抽样文献详情，并保留源 SQLite 文件作为回滚副本。")


if __name__ == "__main__":
    main()
