import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .catalog import DataError, load_dataset
from .database import build_database
from .models import Dataset
from .render import markdown
from .service import Lexicon
from .exporting import BASE_URI, export_file


def parser():
    cli = argparse.ArgumentParser(description="马克思多语术语库")
    cli.add_argument("--root", type=Path, default=Path.cwd(), help="仓库根目录，默认当前目录")
    cli.add_argument("--database", type=Path, help="查询数据库，默认 <root>/build/lexicon.sqlite")
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="校验 YAML 及跨文件引用")
    for name in ("serve", "mcp"):
        command = commands.add_parser(name, help="启动网页" if name == "serve" else "启动只读 MCP")
        command.add_argument("--host", default="127.0.0.1")
        command.add_argument("--port", type=int, default=8000 if name == "serve" else 8001)
        command.add_argument("--edit", action="store_true", help="启用本地源文件编辑，保存后手动提交推送")
        if name == "serve":
            command.add_argument("--with-mcp", action="store_true")
        else:
            command.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    build = commands.add_parser("build", help="构建 SQLite 检索库")
    build.add_argument("--output", type=Path, help="输出数据库，默认 <root>/build/lexicon.sqlite")
    lookup = commands.add_parser("lookup", help="查询原词、译词和变体")
    lookup.add_argument("query")
    lookup.add_argument("--language")
    lookup.add_argument("--limit", type=int, default=20)
    read = commands.add_parser("read-entry", help="读取完整词条")
    read.add_argument("entry_id")
    evidence = commands.add_parser("read-evidence", help="按需读取完整引文和版本，可筛选语言")
    evidence.add_argument("ids", nargs="+")
    evidence.add_argument("--language", action="append", dest="languages", help="可重复指定；默认全部语言")
    evidence.add_argument("--dataset-version", help="参考包的数据版本，变化时要求重新查询")
    prepare = commands.add_parser("prepare-translation", help="整理段落的术语参考")
    prepare.add_argument("--text-file", type=Path, required=True)
    prepare.add_argument("--source-language", required=True)
    prepare.add_argument("--target-language", required=True)
    prepare.add_argument("--profile", required=True)
    prepare.add_argument("--work")
    prepare.add_argument("--edition")
    prepare.add_argument("--sense", help="显式指定一个命中词条的义项，其他词条仍照常处理")
    prepare.add_argument("--detail", choices=["compact", "full"],
                         help="JSON 默认 compact，Markdown 默认 full")
    glossary = commands.add_parser("export-glossary", help="导出方案的术语表和出处")
    glossary.add_argument("--profile", required=True)
    export = commands.add_parser("export-dataset", help="导出全库数据、人工视图或标准交换文件")
    export.add_argument("--format", choices=["json", "markdown", "csv", "tbx", "skos"], default="json")
    export.add_argument("--base-uri", default=BASE_URI, help="SKOS 的稳定标识前缀")
    export.add_argument("--output", type=Path, help="输出文件，默认标准输出")
    schema = commands.add_parser("export-schema", help="从模型导出 JSON Schema")
    schema.add_argument("--output", type=Path, help="输出文件，默认标准输出")
    for command in (lookup, read, evidence, prepare, glossary):
        command.add_argument("--format", choices=["json", "markdown", "csv", "tbx"]
                             if command is glossary else ["json", "markdown"], default="json")
        command.add_argument("--output", type=Path, help="输出文件，默认标准输出")
    return cli


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    cli = parser()
    args = cli.parse_args(argv)
    root = args.root.resolve()
    try:
        database = args.database or root / "build/lexicon.sqlite"
        if args.command in ("serve", "mcp"):
            if args.edit and args.host not in ("127.0.0.1", "localhost", "::1"):
                raise ValueError("编辑服务仅支持监听本机回环地址")
            with Lexicon(database):
                pass
            if args.command == "serve":
                import uvicorn
                from .web import create_app
                uvicorn.run(create_app(database, args.with_mcp, root=root, edit=args.edit), host=args.host, port=args.port)
            else:
                from .mcp_server import create_mcp
                editor = None
                if args.edit:
                    from .editing import Editor
                    editor = Editor(root, database)
                server = create_mcp(database, editor=editor)
                server.run(transport=args.transport, **({"host": args.host, "port": args.port,
                           "json_response": True} if args.transport == "streamable-http" else {}))
            return 0
        if args.command in ("export-glossary", "export-dataset"):
            with Lexicon(database) as lexicon:
                exported = export_file(lexicon, args.format, getattr(args, "profile", None),
                                       getattr(args, "base_uri", BASE_URI))
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_bytes(exported.content)
                report = args.output.with_name(args.output.name + ".report.json")
                report.write_text(json.dumps(exported.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"已写入 {args.output}；导出报告 {report}")
            else:
                print(exported.content.decode("utf-8"), end="")
                print(json.dumps(exported.report, ensure_ascii=False), file=sys.stderr)
            return 0
        if args.command == "validate":
            dataset = load_dataset(root)
            result = {"status": "valid", "dataset_version": dataset.manifest.dataset_version,
                      "schema_version": dataset.manifest.schema_version,
                      "entries": len(dataset.entries), "attestations": len(dataset.attestations)}
        elif args.command == "build":
            if (root / "build/editor/transaction.json").exists():
                from .editing import Editor
                Editor(root, args.output or root / "build/lexicon.sqlite")
            result = build_database(root, args.output or root / "build/lexicon.sqlite")
        elif args.command == "export-schema":
            result = Dataset.model_json_schema()
        else:
            with Lexicon(database) as lexicon:
                if args.command == "lookup":
                    result = lexicon.lookup(args.query, args.language, args.limit)
                elif args.command == "read-entry":
                    result = lexicon.read_entry(args.entry_id)
                elif args.command == "read-evidence":
                    result = lexicon.read_evidence(args.ids, args.languages, args.dataset_version)
                elif args.command == "prepare-translation":
                    result = lexicon.prepare_translation(
                        args.text_file.read_text(encoding="utf-8"),
                        source_language=args.source_language, target_language=args.target_language,
                        profile_id=args.profile, work_id=args.work, edition_id=args.edition,
                        sense_id=args.sense,
                        detail=args.detail or ("full" if args.format == "markdown" else "compact"))
                else:
                    result = lexicon.export_glossary(args.profile)
        output_format = getattr(args, "format", "json")
        compact_json = result.get("format") == "translation-brief/1" or args.command == "read-evidence"
        content = (markdown(result) if output_format == "markdown" else
                   (json.dumps(result, ensure_ascii=False, separators=(",", ":")) if compact_json
                    else json.dumps(result, ensure_ascii=False, indent=2)) + "\n")
        destination = getattr(args, "output", None) if args.command != "build" else None
        if destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            print(f"已写入 {destination}")
        else:
            print(content, end="")
        return 0
    except (DataError, ValueError, KeyError, OSError, sqlite3.Error) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    except ModuleNotFoundError as error:
        print(f"缺少可选依赖 {error.name}，请运行 python -m pip install -e \".[web,mcp,export,edit]\"", file=sys.stderr)
        return 1
