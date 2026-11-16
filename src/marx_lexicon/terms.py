"""术语聚合视图。归组只影响组织方式，不推导语言间等价关系。"""

from .catalog import forms, normalize


class TermQueries:
    def term_members(self, term):
        return [(entry, sense) for entry in self.dataset.entries for sense in entry.senses
                if sense.concept_id in term.concept_ids]

    def term_summary(self, identifier):
        term = self.terms[identifier]
        labels = {}
        for entry, sense in self.term_members(term):
            values = labels.setdefault(entry.language, [])
            if entry.lemma not in values:
                values.append(entry.lemma)
        return {**term.model_dump(mode="json"), "labels": labels}

    def list_terms(self, query=None, language=None, work_id=None, concept_id=None, offset=0, limit=20):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset 须非负，limit 须在 1—100 之间")
        if language and language not in self.dataset.manifest.languages:
            raise ValueError("未登记的语言：" + language)
        if work_id and work_id not in {w.id for w in self.dataset.works}:
            raise KeyError(work_id)
        if concept_id and concept_id not in self.concepts:
            raise KeyError(concept_id)
        query = normalize(query or "")
        found = []
        for term in self.dataset.terms:
            if concept_id and concept_id not in term.concept_ids:
                continue
            members = [(e, s) for e, s in self.term_members(term) if (not language or e.language == language)
                       and (not work_id or not s.work_ids or work_id in s.work_ids)
                       and (not concept_id or s.concept_id == concept_id)]
            if not members and (language or work_id):
                continue
            rank, matched = 4, []
            if query:
                for entry, sense in members:
                    for surface in forms(entry):
                        value = normalize(surface)
                        if query in value:
                            rank = min(rank, 0 if value == query else 1 if value.startswith(query) else 2)
                            match = {"entry_id": entry.id, "language": entry.language, "text": surface}
                            if match not in matched:
                                matched.append(match)
                concept_text = " ".join(self.concepts[c].definition for c in term.concept_ids
                                        if not concept_id or c == concept_id)
                descriptions = " ".join([term.title, term.summary, concept_text,
                    *(s.definition + " " + s.usage for _, s in members)])
                evidence_ids = {identifier for _, s in members for identifier in s.evidence_ids}
                quotes = " ".join(p.text for identifier in evidence_ids for p in self.evidence[identifier].passages
                                  if (not language or self.editions[p.edition_id].language == language)
                                  and any(m.sense_id == s.id for _, s in members for m in p.mentions))
                article = self.articles.get(term.article, "")
                if query in normalize(descriptions + " " + quotes + " " + article):
                    rank = min(rank, 3)
                if rank == 4:
                    continue
            found.append((rank, normalize(term.title), term.id, matched))
        found.sort()
        return {**self.version(), "query": query, "total": len(found), "offset": offset, "limit": limit,
                "results": [{**self.term_summary(identifier), "matches": matched}
                            for _, _, identifier, matched in found[offset:offset + limit]]}

    def list_unassigned(self, offset=0, limit=20):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset 须非负，limit 须在 1—100 之间")
        assigned = {c for term in self.dataset.terms for c in term.concept_ids}
        concepts = [c for c in self.dataset.concepts if c.id not in assigned]
        return {**self.version(), "total": len(concepts), "offset": offset, "limit": limit,
                "results": [{"concept": c.model_dump(mode="json"), "expressions": [
                    {"entry_id": e.id, "sense_id": s.id, "language": e.language, "lemma": e.lemma}
                    for e in self.dataset.entries for s in e.senses if s.concept_id == c.id]}
                    for c in concepts[offset:offset + limit]]}

    def terms_for(self, kind, identifier):
        return [self.term_summary(t.id) for t in self.dataset.terms
                if identifier in self.term_resource_ids(t)[kind]]

    def term_resource_ids(self, term):
        members = self.term_members(term)
        senses = {s.id for _, s in members}
        translations = [t for t in self.dataset.translations
                        if t.source_sense_id in senses or t.target_sense_id in senses]
        concepts = [self.concepts[c] for c in term.concept_ids]
        evidence = {i for _, s in members for i in s.evidence_ids}
        evidence.update(i for c in concepts for i in c.evidence_ids)
        evidence.update(i for c in concepts for r in c.relations for i in r.evidence_ids)
        evidence.update(i for t in translations for i in t.evidence_ids)
        editions = {p.edition_id for i in evidence for p in self.evidence[i].passages}
        editions.update(i for t in translations for i in t.scope.edition_ids)
        works = {self.editions[i].work_id for i in editions}
        works.update(i for _, s in members for i in s.work_ids)
        works.update(i for t in translations for i in t.scope.work_ids)
        articles = {e.article for e, _ in members if e.article}
        if term.article:
            articles.add(term.article)
        return {"terms": {term.id}, "entries": {e.id for e, _ in members}, "senses": senses,
                "concepts": set(term.concept_ids), "translations": {t.id for t in translations},
                "attestations": evidence, "works": works, "editions": editions, "articles": articles}

    def read_term(self, term_id, detail="compact"):
        if detail not in ("compact", "full"):
            raise ValueError("detail 须为 compact 或 full")
        term = self.terms[term_id]
        ids = self.term_resource_ids(term)
        members = self.term_members(term)
        sections = []
        for cid in term.concept_ids:
            sections.append({"concept": self.concepts[cid].model_dump(mode="json"), "expressions": [
                {"entry_id": e.id, "language": e.language, "lemma": e.lemma, "part_of_speech": e.part_of_speech,
                 "forms": [f.model_dump(mode="json") for f in e.forms], "sense": s.model_dump(mode="json")}
                for e, s in members if s.concept_id == cid]})
        related = set()
        for concept in self.dataset.concepts:
            for relation in concept.relations:
                if concept.id in ids["concepts"] or relation.target_id in ids["concepts"]:
                    related.update(t.id for t in self.dataset.terms
                                   if t.id != term.id and (concept.id in t.concept_ids or relation.target_id in t.concept_ids))
        translations = [self.translations[i].model_dump(mode="json") for i in sorted(ids["translations"])]
        endpoints = {identifier for t in translations for identifier in (t["source_sense_id"], t["target_sense_id"])}
        result = {**self.version(), "term": self.term_summary(term_id), "sections": sections,
                  "translations": translations, "endpoints": {identifier: {
                      "entry_id": self.senses[identifier][0].id, "language": self.senses[identifier][0].language,
                      "lemma": self.senses[identifier][0].lemma} for identifier in sorted(endpoints)},
                  "project_choices": [{"profile_id": p.id, "title": p.title, "profile_scope": p.scope.model_dump(mode="json"),
                      **s.model_dump(mode="json")} for p in self.dataset.profiles for s in p.selections
                      if s.translation_id in ids["translations"]],
                  "related_terms": [self.term_summary(i) for i in sorted(related)],
                  "evidence_ids": sorted(ids["attestations"]), "article_paths": sorted(ids["articles"])}
        # 实际版本用词保留在精简视图，整段引文仅在完整视图展开。
        result["witnesses"] = [{"evidence_id": aid, "edition_id": p.edition_id,
            "language": self.editions[p.edition_id].language, "sense_id": m.sense_id,
            "text": p.text[m.start:m.end]} for aid in sorted(ids["attestations"])
            for p in self.evidence[aid].passages for m in p.mentions if m.sense_id in ids["senses"]]
        if detail == "full":
            result.update(self.evidence_bundle(ids["attestations"]))
            result["articles"] = {name: self.articles[name] for name in sorted(ids["articles"])}
        return result
