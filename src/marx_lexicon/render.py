"""命令行及导出的人工阅读视图。"""


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def scope_label(scope, result):
    works = {item["id"]: item["title"] for item in result.get("works", [])}
    editions = {item["id"]: item["title"] for item in result.get("editions", [])}
    parts = []
    if scope["work_ids"]:
        parts.append("作品：" + "、".join(works.get(key, key) for key in scope["work_ids"]))
    if scope["edition_ids"]:
        parts.append("原文版本：" + "、".join(editions.get(key, key) for key in scope["edition_ids"]))
    if scope["note"]:
        parts.append(scope["note"].rstrip("。；; "))
    return "；".join(parts) or "未限定作品或版本"


def evidence_markdown(result):
    lines = []
    editions = {item["id"]: item for item in result.get("editions", [])}
    for item in result.get("attestations", []):
        review = {"unreviewed": "待审阅", "reviewed": "已审阅"}[item["review_status"]]
        lines += [f"### {item['id']}", "", item["note"], "",
                  f"整理状态：{review}。", ""]
        for passage in item["passages"]:
            edition = editions[passage["edition_id"]]
            location = passage["location"]
            citation = edition["citation"].rstrip("。.")
            precision = {"page": "书页", "section": "章节", "quote": "短引文"}[location["precision"]]
            lines += [f"**{edition['language']} · {edition['title']}**", "",
                      "> " + passage["text"], "",
                      f"出处：[{location['section']}]({location['url']})；{citation}。",
                      f"定位精度：{precision}；书页：{location.get('printed_page') or '未登记'}；"
                      f"稿页：{location.get('manuscript_page') or '未登记'}。",
                      f"发言主体：{passage['speaker']}。", ""]
            if location["note"]:
                lines += [location["note"], ""]
        for alignment in item["alignments"]:
            lines += [f"对应：{alignment['level']} / {alignment['status']}。{alignment['note']}", ""]
    return lines


def markdown(result):
    lines = []
    if "dataset_version" in result:
        lines += [f"数据版本：{result['dataset_version']}；模型版本：{result['schema_version']}。", ""]
    if "results" in result:
        lines += [f"# 查询：{result['query']}", "", "| ID | 语言 | 词条 | 义项 |",
                  "| --- | --- | --- | --- |"]
        for entry in result["results"]:
            definitions = "；".join(sense["definition"] for sense in entry["senses"])
            lines.append(f"| {cell(entry['id'])} | {entry['language']} | {cell(entry['lemma'])} | {cell(definitions)} |")
    elif "entry" in result:
        entry = result["entry"]
        lines += [f"# {entry['lemma']} · {entry['language']}", "", f"ID：`{entry['id']}`", ""]
        if entry["forms"]:
            lines += ["词形：" + "；".join(f"{form['text']}（{form['kind']}）" for form in entry["forms"]), ""]
        for sense in entry["senses"]:
            lines += [f"## {sense['id']}", "", sense["definition"], "", sense["usage"], ""]
        linked = {item["id"]: item for item in result["linked_entries"]}
        sense_labels = {s["id"]: item["lemma"] for item in linked.values() for s in item["senses"]}
        sense_labels.update({s["id"]: entry["lemma"] for s in entry["senses"]})
        lines += ["## 译法关系", ""]
        for translation in result["translations"]:
            source = sense_labels[translation["source_sense_id"]]
            target = sense_labels[translation["target_sense_id"]]
            lines += [f"- {source} → {target}（`{translation['id']}`）：{translation['note']}",
                      f"  适用范围：{scope_label(translation['scope'], result)}；依据：{', '.join(translation['evidence_ids'])}。"]
        for concept in result["concepts"]:
            lines += ["", f"## 概念：{concept['id']}", "", concept["definition"], ""]
            for relation in concept["relations"]:
                lines += [f"- {relation['kind']} → `{relation['target_id']}`：{relation['explanation']} "
                          f"依据：{', '.join(relation['evidence_ids'])}。"]
        if result["article"]:
            lines += ["", "## 辨析文章", "", result["article"], ""]
        lines += ["", "## 原文与译本证据", "", *evidence_markdown(result)]
    elif "occurrences" in result:
        lines += ["# 翻译术语参考", "", f"方案：`{result['profile_id']}`；"
                  f"方向：{result['source_language']} → {result['target_language']}。", "",
                  "## 待译文本", "", "> " + result["text"].replace("\n", "\n> "), "",
                  "## 术语", ""]
        for occurrence in result["occurrences"]:
            lines += [f"### {occurrence['surface']} · [{occurrence['start']}, {occurrence['end']})", "",
                      f"词条：`{occurrence['entry_id']}`。", ""]
            if occurrence["recommendation"]:
                lines += [f"方案建议：**{occurrence['recommendation']['text']}**。", ""]
            for issue in occurrence["unresolved"]:
                lines += [f"待判断：{issue}", ""]
            for option in occurrence["candidates"]:
                sense = option["sense"]
                lines += [f"义项 `{sense['id']}`：{sense['definition']} {sense['usage']}", ""]
                for translation in option["translations"]:
                    label = "方案选用" if translation["selected_by_profile"] else "备选"
                    if translation["applicability"] == "needs_context":
                        label += "，需补充作品或版本"
                    lines += [f"- {translation['target']['lemma']}（{label}）：{translation['note']} "
                              f"范围：{scope_label(translation['scope'], result)}；证据：{', '.join(translation['evidence_ids'])}。"]
                lines.append("")
            lines.append("")
        lines += [result["note"], "", "## 证据", "", *evidence_markdown(result)]
    elif "rows" in result:
        lines += [f"# {result['profile']['title']}", "", result["profile"]["note"], "",
                  "| 原词 | 义项 | 目标词 | 方案选用 | 范围与说明 | 依据 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for row in result["rows"]:
            lines.append("| " + " | ".join(cell(value) for value in (
                row["source"], row["definition"], row["target"],
                "是" if row["selected_by_profile"] else "备选",
                scope_label(row["scope"], result) + " " + row["note"]
                + " 方案选择范围：" + " / ".join(scope_label(scope, result) for scope in row["selection_scopes"]),
                ", ".join(row["evidence_ids"]))) + " |")
        lines += ["", "## 证据", "", *evidence_markdown(result)]
    else:
        lines += [f"- {key}：{value}" for key, value in result.items()]
    return "\n".join(lines).rstrip() + "\n"
