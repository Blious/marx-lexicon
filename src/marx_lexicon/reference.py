"""把翻译参考整理成去重、可按需展开的程序视图。"""

import json


def project_evidence(bundle, languages):
    """保留请求语言的段落，并同步投影对应关系，维持原术语标注索引。"""
    editions = {edition["id"]: edition for edition in bundle["editions"]}
    attestations = []
    used_editions = set()
    for original in bundle["attestations"]:
        passages = [p for p in original["passages"] if editions[p["edition_id"]]["language"] in languages]
        if not passages:
            continue
        passage_ids = {p["id"] for p in passages}
        alignments = []
        for alignment in original["alignments"]:
            selected = [identifier for identifier in alignment["passage_ids"] if identifier in passage_ids]
            refs = [ref for ref in alignment["mention_refs"] if ref["passage_id"] in passage_ids]
            if len(selected) < 2 or (alignment["level"] == "term" and len(refs) < 2):
                continue
            alignments.append({**alignment, "passage_ids": selected, "mention_refs": refs})
        attestations.append({**original, "passages": passages, "alignments": alignments})
        used_editions.update(p["edition_id"] for p in passages)
    selected_editions = [e for e in bundle["editions"] if e["id"] in used_editions]
    used_works = {e["work_id"] for e in selected_editions}
    return {"attestations": attestations, "editions": selected_editions,
            "works": [work for work in bundle["works"] if work["id"] in used_works]}


def compact_reference(full):
    """只整理和去重既有信息，不概括改写定义、限制或判断结果。"""
    notes = {}
    note_ids = {}
    scopes = {}
    scope_ids = {}

    def note_ref(text):
        if text not in note_ids:
            identifier = f"n{len(notes) + 1}"
            note_ids[text] = identifier
            notes[identifier] = text
        return note_ids[text]

    def scope_ref(scope):
        key = json.dumps(scope, ensure_ascii=False, sort_keys=True)
        if key not in scope_ids:
            identifier = f"r{len(scopes) + 1}"
            scope_ids[key] = identifier
            value = {"work_ids": scope["work_ids"], "edition_ids": scope["edition_ids"]}
            if scope["note"]:
                value["note_ref"] = note_ref(scope["note"])
            scopes[identifier] = value
        return scope_ids[key]

    terms = {}
    for occurrence in full["occurrences"]:
        entry_id = occurrence["entry_id"]
        if entry_id not in terms:
            candidates = []
            for option in occurrence["candidates"]:
                sense = option["sense"]
                candidate = {"sense_id": sense["id"], "concept_id": sense["concept_id"],
                             "definition": sense["definition"], "usage_ref": note_ref(sense["usage"]),
                             "work_ids": sense["work_ids"], "status": sense["status"],
                             "evidence_ids": sense["evidence_ids"], "translations": []}
                for item in option["translations"]:
                    target = item["target_sense"]
                    translation = {
                        "id": item["id"], "text": item["target"]["lemma"],
                        "target_sense_id": target["id"],
                        "scope_ref": scope_ref(item["scope"]),
                        "note_ref": note_ref(item["note"]), "status": item["status"],
                        "applicability": item["applicability"],
                        "selected_by_profile": item["selected_by_profile"],
                        "profile_choices": [{"scope_ref": scope_ref(choice["scope"]),
                                             "applicability": choice["applicability"],
                                             "note_ref": note_ref(choice["note"])}
                                            for choice in item["profile_choices"]],
                        "evidence_ids": item["evidence_ids"],
                    }
                    # 目标义项这些字段相同时继承源义项；差异必须逐字保留。
                    for field in ("definition", "usage", "concept_id", "work_ids", "evidence_ids"):
                        if target[field] != sense[field]:
                            if field == "usage":
                                translation["target_usage_ref"] = note_ref(target[field])
                            else:
                                translation["target_" + field] = target[field]
                    if target["status"] != sense["status"]:
                        translation["target_status"] = target["status"]
                    candidate["translations"].append(translation)
                candidates.append(candidate)
            terms[entry_id] = {"entry_id": entry_id, "lemma": occurrence["lemma"], "matches": [],
                               "candidates": candidates,
                               "recommendation": (occurrence["recommendation"]["translation_id"]
                                                  if occurrence["recommendation"] else None),
                               "unresolved": occurrence["unresolved"]}
        matches = terms[entry_id]["matches"]
        match = next((item for item in matches if item["surface"] == occurrence["surface"]), None)
        if match is None:
            match = {"surface": occurrence["surface"], "spans": []}
            matches.append(match)
        match["spans"].append([occurrence["start"], occurrence["end"]])

    languages = list(dict.fromkeys([full["source_language"], full["target_language"]]))
    bundle = project_evidence(full, languages)
    evidence = {}
    for item in bundle["attestations"]:
        passages = []
        has_term_alignment = any(a["level"] == "term" for a in item["alignments"])
        for passage in item["passages"]:
            location = {key: value for key, value in passage["location"].items()
                        if value is not None and key not in ("note", "url")}
            if passage["location"]["note"]:
                location["note_ref"] = note_ref(passage["location"]["note"])
            terms_in_passage = []
            for index, mention in enumerate(passage["mentions"]):
                observed = {"sense_id": mention["sense_id"],
                            "form": passage["text"][mention["start"]:mention["end"]]}
                if has_term_alignment:
                    observed["mention_index"] = index
                terms_in_passage.append(observed)
            summary = {
                "edition_id": passage["edition_id"], "location": location,
                "speaker": passage["speaker"], "material": passage["material"],
                "terms": terms_in_passage,
            }
            if has_term_alignment:
                summary["id"] = passage["id"]
            passages.append(summary)
        evidence[item["id"]] = {
            "note_ref": note_ref(item["note"]), "review_status": item["review_status"],
            "passages": passages,
            "alignment_summaries": [{"level": alignment["level"], "status": alignment["status"],
                                     "note_ref": note_ref(alignment["note"])}
                                    for alignment in item["alignments"]],
        }
        if has_term_alignment:
            evidence[item["id"]]["term_alignments"] = [
                {**{key: value for key, value in alignment.items() if key != "note"},
                 "note_ref": note_ref(alignment["note"])}
                for alignment in item["alignments"] if alignment["level"] == "term"]
        if item["external_refs"]:
            evidence[item["id"]]["external_refs"] = item["external_refs"]
    editions = {}
    for edition in bundle["editions"]:
        editions[edition["id"]] = {key: edition[key] for key in ("title", "language", "work_id", "kind")}
        if edition["note"]:
            editions[edition["id"]]["note_ref"] = note_ref(edition["note"])
    profile = full["profile"]
    return {
        "format": "translation-brief/1", "schema_version": full["schema_version"],
        "dataset_version": full["dataset_version"], "source_language": full["source_language"],
        "target_language": full["target_language"],
        "context": {"work_id": full["work_id"], "edition_id": full["edition_id"],
                    "input_characters": len(full["text"])},
        "profile": {"id": profile["id"], "scope_ref": scope_ref(profile["scope"]),
                    "note_ref": note_ref(profile["note"])},
        "terms": list(terms.values()), "concept_relations": full["concept_relations"],
        "scopes": scopes, "notes": notes, "evidence": evidence,
        "editions": editions, "works": bundle["works"],
        "read_more": {"command": "read-evidence", "ids": list(evidence),
                      "languages": languages, "dataset_version": full["dataset_version"]},
        "note": full["note"] + " form 为实际出现形式，不等于整句引文或逐词等价；引文、完整对应和书目信息按 read_more 读取。",
    }
