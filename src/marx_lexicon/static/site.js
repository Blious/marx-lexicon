// 服务端位置以 Unicode 码点计数；用 Array.from 保留非 BMP 字符的位置。
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function download(content, name, type) {
  const url = URL.createObjectURL(new Blob([content], {type}));
  const link = element('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
const form = document.querySelector('#translation-form');
if (form) form.addEventListener('submit', async event => {
  event.preventDefault();
  const status = document.querySelector('#translation-status');
  const output = document.querySelector('#translation-result');
  const button = form.querySelector('button');
  const data = Object.fromEntries(new FormData(form));
  const profile = form.querySelector('#profile').selectedOptions[0];
  data.source_language = profile.dataset.source;
  data.target_language = profile.dataset.target;
  for (const key of ['work_id','edition_id','sense_id']) if (!data[key]) data[key] = null;
  data.detail = 'compact';
  button.disabled = true; status.textContent = '正在整理…'; output.replaceChildren();
  try {
    const response = await fetch('/api/translation-reference', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
    status.textContent = `${result.terms.length} 个已识别词条 · 数据 ${result.dataset_version}`;
    const actions = element('div',undefined,'actions');
    const json = element('button','下载 JSON'); json.onclick = () => download(JSON.stringify(result),'translation-reference.json','application/json');
    const md = element('button','下载 Markdown','secondary');
    md.onclick = async () => {
      const response = await fetch('/api/translation-reference?format=markdown',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
      if (response.ok) download(await response.text(),'translation-reference.md','text/markdown');
      else status.textContent = '下载失败，请重新整理参考。';
    };
    actions.append(json,md); output.append(actions);
    const chars = Array.from(data.text);
    const workNames = Object.fromEntries(result.works.map(w => [w.id,w.title]));
    const statusNames = {draft:'工作稿',reviewed:'已审阅',match:'条件匹配',needs_context:'需补充语境',mismatch:'条件不符'};
    const label = value => statusNames[value] || value;
    function scope(id) {
      const value = result.scopes[id];
      return [...value.work_ids.map(id=>workNames[id] || id),...value.edition_ids.map(id=>result.editions[id]?.title || id),result.notes[value.note_ref] || ''].filter(Boolean).join('；') || '未限定作品或版本';
    }
    output.append(element('p',`项目条件：${scope(result.profile.scope_ref)}。${result.notes[result.profile.note_ref]}`,'notice'));
    for (const term of result.terms) {
      const section = element('section',undefined,'sense');
      const heading = element('h2'); const link = element('a',term.lemma); link.href = `/entries/${term.entry_id}`; heading.append(link); section.append(heading);
      for (const match of term.matches) for (const [start,end] of match.spans) {
        section.append(element('span',`${chars.slice(start,end).join('')} [${start},${end})`,'term-hit'));
      }
      for (const issue of term.unresolved) section.append(element('p',issue,'notice'));
      for (const candidate of term.candidates) {
        section.append(element('h3',`${candidate.definition} · ${label(candidate.status)}`));
        section.append(element('p',result.notes[candidate.usage_ref]));
        section.append(element('p',`义项作品范围：${candidate.work_ids.map(id=>workNames[id] || id).join('；') || '未限定'}`));
        section.append(element('code',candidate.sense_id));
        for (const translation of candidate.translations) {
          const box = element('article',undefined,'translation');
          box.append(element('h3',`${translation.text} · ${term.recommendation === translation.id ? '方案建议' : '候选'}`));
          box.append(element('p',`${label(translation.status)} / ${label(translation.applicability)} · ${result.notes[translation.note_ref]}`));
          box.append(element('p',`译法范围：${scope(translation.scope_ref)}`));
          if (translation.target_definition) box.append(element('p',`目标义项：${translation.target_definition}`));
          if (translation.target_usage_ref) box.append(element('p',result.notes[translation.target_usage_ref]));
          if (translation.target_status) box.append(element('p',`目标义项状态：${label(translation.target_status)}`));
          if (translation.target_work_ids) box.append(element('p',`目标义项范围：${translation.target_work_ids.map(id=>workNames[id] || id).join('；') || '未限定'}`));
          for (const choice of translation.profile_choices) box.append(element('p',`项目选择条件：${scope(choice.scope_ref)} · ${label(choice.applicability)} · ${result.notes[choice.note_ref]}`));
          section.append(box);
        }
      }
      output.append(section);
    }
    output.append(element('p',result.note,'notice'));
    const distinctions = element('section'); distinctions.append(element('h2','相关辨析'));
    for (const relation of result.concept_relations) distinctions.append(element('p',`${relation.kind} / ${relation.status}：${relation.explanation}`));
    output.append(distinctions);
    const evidence = element('section'); evidence.append(element('h2','展开完整证据'));
    for (const id of result.read_more.ids) {
      const link = element('a',id); link.href = `/evidence/${id}?dataset_version=${encodeURIComponent(result.dataset_version)}`;
      evidence.append(link,element('br'));
    }
    output.append(evidence);
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});
const exportsForm = document.querySelector('#export-form');
if (exportsForm) {
  exportsForm.addEventListener('change', () => {
    const skos = exportsForm.querySelector('option[value="skos"]');
    skos.disabled = !!exportsForm.elements.profile_id.value;
    if (skos.disabled && exportsForm.elements.format.value === 'skos') exportsForm.elements.format.value = 'json';
  });
  exportsForm.addEventListener('formdata', event => {
    if (!event.formData.get('profile_id')) event.formData.delete('profile_id');
  });
}
