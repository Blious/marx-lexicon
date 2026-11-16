"""从编辑数据构建可独立查询的 SQLite 发布文件。"""

import json
import sqlite3
from pathlib import Path

from .catalog import forms, load_dataset, normalize
from .models import Dataset


def build_database(root: Path, output: Path) -> dict:
    dataset = load_dataset(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    # 仅重建本工具管理的表；保留 SQLite 文件中的其他表。
    with sqlite3.connect(output) as connection:
        connection.executescript("""
            BEGIN;
            DROP TABLE IF EXISTS documents;
            DROP TABLE IF EXISTS forms;
            DROP TABLE IF EXISTS entries;
            DROP TABLE IF EXISTS metadata;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE entries (id TEXT PRIMARY KEY, language TEXT NOT NULL,
                                  lemma TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE forms (entry_id TEXT NOT NULL, surface TEXT NOT NULL,
                                normalized TEXT NOT NULL, language TEXT NOT NULL);
            CREATE INDEX forms_match ON forms(language, normalized);
            CREATE VIRTUAL TABLE documents USING fts5(entry_id UNINDEXED, text,
                                      tokenize='unicode61 remove_diacritics 0');
        """)
        connection.execute("INSERT INTO metadata VALUES (?, ?)",
                           ("dataset", dataset.model_dump_json()))
        articles = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
                    for p in (root / "articles").rglob("*.md")}
        for entry in dataset.entries:
            article = articles[entry.article] if entry.article else ""
            connection.execute("INSERT INTO entries VALUES (?, ?, ?, ?)",
                (entry.id, entry.language, entry.lemma, entry.model_dump_json()))
            connection.executemany("INSERT INTO forms VALUES (?, ?, ?, ?)",
                [(entry.id, surface, normalize(surface), entry.language)
                 for surface in dict.fromkeys(forms(entry))])
            passages = [passage.text for evidence in dataset.attestations
                        for passage in evidence.passages
                        if any(m.entry_id == entry.id for m in passage.mentions)]
            connection.execute("INSERT INTO documents VALUES (?, ?)",
                (entry.id, "\n".join([*forms(entry), article, *passages,
                    *(s.definition + " " + s.usage for s in entry.senses)])))
        connection.execute("INSERT INTO metadata VALUES (?, ?)",
                           ("articles", json.dumps(articles, ensure_ascii=False)))
    return {"database": str(output), "dataset_version": dataset.manifest.dataset_version,
            "schema_version": dataset.manifest.schema_version,
            "terms": len(dataset.terms),
            "entries": len(dataset.entries), "attestations": len(dataset.attestations)}


def read_dataset(connection: sqlite3.Connection) -> Dataset:
    row = connection.execute("SELECT value FROM metadata WHERE key='dataset'").fetchone()
    return Dataset.model_validate_json(row[0])
