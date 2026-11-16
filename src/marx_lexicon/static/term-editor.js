/* 术语工作区。表单值转换为一个文档，稳定 ID 和跨术语引用由服务维护。 */
(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; };
  const uid = prefix => prefix+'-'+crypto.randomUUID().replaceAll('-','').slice(0,12);
  const labels = {terms:'术语主条目',entries:'语言表达',concepts:'概念义项',translations:'译法',attestations:'证据组',works:'作品',editions:'版本',articles:'辨析文章',id:'稳定 ID',title:'标题',summary:'概述',article:'辨析文章',language:'语言',lemma:'原词 / 译名',part_of_speech:'词性',forms:'词形',text:'原文 / 实际词形',kind:'类别',note:'说明',senses:'义项',definition:'定义',usage:'用法',concept_id:'所属概念义项',work_ids:'作品范围',evidence_ids:'证据',status:'审阅状态',labels:'概念的分语言标签',relations:'概念关系',target_id:'相关概念',explanation:'关系说明',source_sense_id:'原文义项',target_sense_id:'目标义项',scope:'适用条件',edition_ids:'原文版本范围',passages:'各版本引文',edition_id:'版本',location:'原始出处',section:'章节',url:'出处链接',printed_page:'印刷书页',manuscript_page:'稿页',precision:'定位精度',speaker:'发言主体',material:'材料类别',mentions:'术语标注',entry_id:'语言表达',sense_id:'义项',start:'起点（Unicode 码点）',end:'终点（不含）',alignments:'对应组',passage_ids:'对应段落',mention_refs:'精确术语对应',passage_id:'段落',mention_index:'术语标注序号（从 0 开始）',level:'对应层级',review_status:'语例状态',external_refs:'外部引用',provider:'提供者',version:'版本',author:'作者',written:'写作年代',original_language:'原文语言',work_id:'作品',citation:'版本书目',translators:'译者',publication_year:'出版年',content:'Markdown 正文'};
  const refs={concept_id:'concepts',target_id:'concepts',work_id:'works',edition_id:'editions',entry_id:'entries',sense_id:'senses',source_sense_id:'senses',target_sense_id:'senses',article:'articles',passage_id:'passages'};
  const manyRefs={work_ids:'works',edition_ids:'editions',evidence_ids:'attestations',passage_ids:'passages'};
  const schemas={};
  const newRecords=new Set();
  let draftOffset=0;
  let meta, catalog={}, revision=0, baseRevision=0, documentValue=null, readers=[], deletions=[], shared=[], external={}, draftId=null, offset=0, unassignedOffset=0;

  async function api(path, body, method='POST') {
    const response=await fetch('/api/'+path,body===undefined?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result=await response.json();
    if(!response.ok){report(result.report||result);throw new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));}
    return result;
  }
  const guard=fn=>async event=>{event?.preventDefault();const button=event?.submitter||event?.currentTarget;if(button?.tagName==='BUTTON')button.disabled=true;try{await fn(event);}catch(error){$('term-result').append(el('p',error.message));}finally{if(button?.tagName==='BUTTON')button.disabled=false;}};
  function report(data) {
    const box=$('term-result');box.replaceChildren(el('h2','操作结果'));
    if(data.valid!==undefined)box.append(el('p',data.valid?'整批校验通过。':'校验未通过，请补全资料或保存编辑草稿。'));
    if(data.local_saved)box.append(el('p',`已保存到本地 · 编辑版本 ${data.revision}${data.dataset_version?' · 数据 '+data.dataset_version:''}`));
    if(data.detail||data.error)box.append(el('p',typeof(data.detail||data.error)==='string'?(data.detail||data.error):JSON.stringify(data.detail||data.error)));
    for(const error of data.errors||data.validation?.errors||[])box.append(el('p',[error.id,error.field,error.message].filter(Boolean).join(' · ')));
    for(const ref of data.references||[])box.append(el('p',`引用：${ref.kind}/${ref.id} · ${ref.field} → ${ref.target}`));
    for(const note of data.notes||[])box.append(el('p',note));
    if(data.counts)box.append(el('p','操作数量：'+JSON.stringify(data.counts)));
    box.focus();
  }
  function collect(){for(const read of readers)read();return documentValue;}
  function catalogItems(kind) {
    const table=new Map((catalog[kind]||[]).map(item=>[item.id,item.label]));
    for(const item of documentValue?.resources[kind]||[])table.set(item.id,item.lemma||item.title||item.note||Object.values(item.labels||{})[0]||item.id);
    if(kind==='senses')for(const entry of documentValue?.resources.entries||[])for(const sense of entry.senses||[])table.set(sense.id,`${entry.lemma} · ${entry.language} · ${sense.definition||'新义项'}`);
    if(kind==='passages')for(const evidence of documentValue?.resources.attestations||[])for(const p of evidence.passages||[])table.set(p.id,`${tableLabel('editions',p.edition_id)} · ${(p.text||'新段落').slice(0,50)}`);
    return [...table].map(([id,label])=>({id,label}));
  }
  function tableLabel(kind,id){return (catalog[kind]||[]).find(item=>item.id===id)?.label||id||'';}
  function selector(parent,key,value,kind,multiple=false) {
    const wrap=el('label',(labels[key]||key)+(multiple?'（可多选）':'')),select=el('select');select.dataset.field=key;select.multiple=multiple;
    if(multiple)select.size=4;else select.append(new Option('请选择 / 不限定',''));
    let items=catalogItems(kind);
    if(key==='concept_id')items=items.filter(item=>documentValue.term.concept_ids.includes(item.id));
    const values=multiple?(value||[]):[value];
    for(const v of values)if(v&&!items.some(item=>item.id===v))items.push({id:v,label:v});
    for(const item of items){const option=new Option(item.label,item.id);option.selected=values.includes(item.id);select.append(option);}
    if(key==='passage_id')select.onchange=()=>{collect();render();};
    wrap.append(select);parent.append(wrap);
    return ()=>multiple?[...select.selectedOptions].map(option=>option.value):select.value;
  }
  function resolved(spec,root){if(spec.$ref)return resolved(root.$defs[spec.$ref.split('/').at(-1)],root);if(spec.anyOf)return {...resolved(spec.anyOf.find(s=>s.type!=='null'),root),nullable:true};return spec;}
  function blank(spec,root,key){spec=resolved(spec,root);if(spec.default!==undefined)return structuredClone(spec.default);if(spec.nullable)return null;if(spec.type==='array')return [];if(spec.type==='object'){const value={};for(const [k,s]of Object.entries(spec.properties||{}))value[k]=k==='id'?uid(spec.title?.toLowerCase()||'record'):blank(s,root,k);return value;}if(spec.enum)return spec.enum[0];return spec.type==='integer'?0:'';}
  function field(parent,original,value,key,root,owner,path=[]) {
    const spec=resolved(original,root);if(value===undefined)value=blank(spec,root,key);
    if(refs[key]){const read=selector(parent,key,value,refs[key]);return ()=>read()|| (spec.nullable?null:'');}
    if(manyRefs[key])return selector(parent,key,value,manyRefs[key],true);
    if(spec.type==='object') {
      const group=el('fieldset');group.append(el('legend',labels[key]||key));parent.append(group);
      if(!spec.properties){const pairs=[];for(const [language,text]of Object.entries(value||{})){const row=el('div',undefined,'mapping-row'),k=el('input'),v=el('input');k.value=language;v.value=text;k.setAttribute('aria-label','标签语言');v.setAttribute('aria-label','概念标签');row.append(k,v);group.append(row);pairs.push([k,v]);}const add=el('button','添加语言标签','secondary');add.type='button';add.onclick=()=>{collect();value={...Object.fromEntries(pairs.map(([k,v])=>[k.value,v.value])),[meta.languages.find(l=>!pairs.some(([k])=>k.value===l))||'und']:''};pairs.length=0;group.remove();const read=field(parent,original,value,key,root,owner);dynamicRead=read;};let dynamicRead=null;group.append(add);return ()=>dynamicRead?dynamicRead():Object.fromEntries(pairs.filter(([k])=>k.value).map(([k,v])=>[k.value,v.value]));}
      const getters={};
      for(const [k,s]of Object.entries(spec.properties)){
        if(k==='id'){const details=el('details');details.append(el('summary','稳定 ID（高级）'));const input=el('input');input.value=value?.id||uid(spec.title?.toLowerCase()||'record');input.readOnly=true;input.dataset.field='id';details.append(input);group.append(details);getters.id=()=>input.value;}
        else if(k==='concept_ids')getters[k]=()=>documentValue.term.concept_ids;
        else getters[k]=field(group,s,value?.[k],k,root,owner,[...path,k]);
      }
      if(spec.title==='Passage'){
        const help=el('button','将引文中选中的文字登记为术语标注','secondary');help.type='button';help.onclick=()=>{
          const input=group.querySelector('[data-field="text"]');if(input.selectionStart===input.selectionEnd){report({detail:'请先在本段引文中选中文字。'});return;}
          const text=input.value,start=Array.from(text.slice(0,input.selectionStart)).length,end=Array.from(text.slice(0,input.selectionEnd)).length,surface=Array.from(text).slice(start,end).join('');
          collect();const passage=findAt(owner,path);const entries=documentValue.resources.entries.filter(e=>[e.lemma,...(e.forms||[]).map(f=>f.text)].includes(surface));
          passage.mentions.push({entry_id:entries.length===1?entries[0].id:'',sense_id:entries.length===1&&entries[0].senses.length===1?entries[0].senses[0].id:'',start,end});render();
        };group.append(help);
      }
      return ()=>({...value,...Object.fromEntries(Object.entries(getters).map(([k,read])=>[k,read()]))});
    }
    if(spec.type==='array'){
      const item=resolved(spec.items,root);
      if(item.type==='string'){const label=el('label',(labels[key]||key)+'（每行一个）'),input=el('textarea');input.rows=2;input.value=(value||[]).join('\n');input.dataset.field=key;label.append(input);parent.append(label);return ()=>input.value.split('\n').map(s=>s.trim()).filter(Boolean);}
      const group=el('fieldset');group.append(el('legend',labels[key]||key));parent.append(group);const getters=[];
      for(const [index,child]of (value||[]).entries()){const row=el('div',undefined,'array-item');group.append(row);getters.push(field(row,spec.items,child,key,root,owner,[...path,index]));const remove=el('button','移除此项','secondary');remove.type='button';remove.onclick=()=>{collect();if(key==='senses'){removeSense(owner,child.id);}else{findAt(owner,path).splice(index,1);}render();};row.append(remove);}
      const add=el('button','添加一项','secondary');add.type='button';add.onclick=()=>{collect();const target=findAt(owner,path);const child=blank(spec.items,root,key);if(key==='senses')child.concept_id=documentValue.term.concept_ids[0]||'';target.push(child);render();};group.append(add);
      return ()=>getters.map(read=>read());
    }
    const label=el('label',labels[key]||key);let input;
    if(key==='language'||key==='original_language'||spec.enum){input=el('select');for(const option of spec.enum||meta.languages)input.append(new Option(option,option));input.value=value??input.options[0]?.value;}
    else if(['summary','definition','usage','note','explanation','text','citation','content'].includes(key)){input=el('textarea');input.rows=key==='content'?9:key==='text'?5:3;input.value=value??'';}
    else{input=el('input');input.type=spec.type==='integer'?'number':'text';input.value=value??'';if(spec.minimum!==undefined)input.min=spec.minimum;}
    input.dataset.field=key;label.append(input);parent.append(label);return ()=>spec.nullable&&input.value===''?null:spec.type==='integer'?Number(input.value):input.value;
  }
  // 数组按钮在 collect 后查找当前对象，避免使用已被新表单值替换的旧数组。
  function findAt(owner,path){let target=owner.id===documentValue.term.id?documentValue.term:Object.values(documentValue.resources).flat().find(r=>r.id===owner.id);for(const part of path)target=target[part];return target;}
  function deleted(kind,id){if(!(catalog[kind]||[]).some(item=>item.id===id))return;if(!deletions.some(d=>d.kind===kind&&d.id===id))deletions.push({kind,id});}
  function removeSense(entry,id){const current=documentValue.resources.entries.find(e=>e.id===entry.id);deleted('senses',id);current.senses=current.senses.filter(s=>s.id!==id);if(!current.senses.length){documentValue.resources.entries=documentValue.resources.entries.filter(e=>e.id!==entry.id);if(!(external[entry.id]||[]).length)deleted('entries',entry.id);}}
  function render(){
    documentValue.resources||={};documentValue.term.concept_ids||=[];for(const kind of ['concepts','entries','translations','attestations','works','editions','articles'])documentValue.resources[kind]||=[];
    readers=[];$('term-workspace').hidden=false;$('workspace-heading').textContent=documentValue.term.title||'新增术语';
    const root=$('term-fields');root.replaceChildren();const read=field(root,schemas.terms,documentValue.term,'术语主条目',schemas.terms,documentValue.term);readers.push(()=>documentValue.term=read());
    const targets={concepts:'concept-fields',entries:'entry-fields',translations:'translation-fields',attestations:'attestation-fields',works:'work-fields',editions:'edition-fields',articles:'article-fields'};
    for(const [kind,target]of Object.entries(targets)){
      const container=$(target);container.replaceChildren();
      for(const record of documentValue.resources[kind]||[]){const wrapper=el('details',undefined,'resource-editor');wrapper.dataset.resource=record.id;wrapper.open=['concepts','entries'].includes(kind)||newRecords.has(record.id);wrapper.append(el('summary',record.lemma||record.title||record.note||Object.values(record.labels||{})[0]||record.id));container.append(wrapper);const read=field(wrapper,schemas[kind],record,kind,schemas[kind],record);readers.push(()=>{const index=documentValue.resources[kind].findIndex(r=>r.id===record.id);if(index>=0)documentValue.resources[kind][index]=read();});
        const remove=el('button',kind==='concepts'?'移出此术语（保留原始资料）':'删除此'+({entries:'语言表达',translations:'译法',attestations:'证据组',works:'作品',editions:'版本',articles:'文章'})[kind],'secondary');remove.type='button';remove.onclick=()=>{collect();if(kind==='concepts'){documentValue.term.concept_ids=documentValue.term.concept_ids.filter(id=>id!==record.id);documentValue.resources.entries.forEach(entry=>entry.senses=entry.senses.filter(s=>s.concept_id!==record.id));documentValue.resources.entries=documentValue.resources.entries.filter(e=>e.senses.length);}else if(kind==='entries'){for(const sense of record.senses)deleted('senses',sense.id);if(!(external[record.id]||[]).length)deleted('entries',record.id);}else deleted(kind,record.id);documentValue.resources[kind]=documentValue.resources[kind].filter(r=>r.id!==record.id);render();};wrapper.append(remove);
        if(kind==='concepts')for(const [step,label]of [[-1,'义项上移'],[1,'义项下移']]){const move=el('button',label,'secondary');move.type='button';const index=documentValue.term.concept_ids.indexOf(record.id);move.disabled=index+step<0||index+step>=documentValue.term.concept_ids.length;move.onclick=()=>{collect();const ids=documentValue.term.concept_ids;[ids[index],ids[index+step]]=[ids[index+step],ids[index]];documentValue.resources.concepts.sort((a,b)=>ids.indexOf(a.id)-ids.indexOf(b.id));render();};wrapper.append(move);}
        if(kind==='articles'){const preview=el('button','预览文章','secondary');preview.type='button';preview.onclick=guard(async()=>{collect();const article=documentValue.resources.articles.find(a=>a.id===record.id);const result=await api('editor/markdown',{content:article.content});const box=el('div',undefined,'prose');box.innerHTML=result.html;wrapper.append(box);});wrapper.append(preview);}
      }
    }
    const usage=$('shared-usage');usage.replaceChildren(el('strong',shared.length?'共享资料：修改会影响以下关联术语':'本术语目前没有跨术语共享记录'));
    for(const item of shared){const p=el('p',`${item.kind} · ${tableLabel(item.kind,item.id)} → `),a=el('a',item.title);a.href='/terms/'+item.term_id;p.append(a);usage.append(p);}
    $('pending-deletions').replaceChildren(...deletions.map(item=>el('p',`明确删除：${item.kind} / ${item.id}`)));
    $('delete-term-draft').hidden=!draftId;$('delete-main-term').disabled=!catalog.terms.some(t=>t.id===documentValue.term.id);
  }
  function newDocument(){return {term:{id:uid('term'),title:'',summary:'',concept_ids:[],article:null},resources:{concepts:[],entries:[],translations:[],attestations:[],works:[],editions:[],articles:[]}};}
  async function openTerm(id){const result=await api('editor/terms/'+encodeURIComponent(id));documentValue=result.document;revision=result.revision;baseRevision=revision;shared=result.shared_usage;external=result.external_sense_ids;deletions=[];draftId=null;render();state();}
  function state(){if(documentValue&&catalog.terms)$('delete-main-term').disabled=!catalog.terms.some(t=>t.id===documentValue.term.id);$('term-state').textContent=`编辑版本 ${revision} · 仅本地保存 · 当前表单基于 ${baseRevision}`;}
  function request(destination='publish'){collect();return {expected_revision:baseRevision,...documentValue,delete:deletions,destination,draft_id:draftId,message:$('term-message').value||'编辑术语'};}
  async function refresh(){
    meta=await api('metadata');$('dataset-summary').textContent=`数据 ${meta.dataset_version} · 模型 ${meta.schema_version} · ${meta.counts.terms} 个术语 / ${meta.counts.entries} 个语言词条 / ${meta.counts.concepts} 个概念`;
    $('dataset-review-summary').textContent=`语例：${meta.review_counts.unreviewed} 组待审阅，${meta.review_counts.reviewed} 组已审阅。项目选用与学术审定分别记录。`;
    const catalogResult=await api('editor/catalog');catalog=catalogResult.catalog;revision=catalogResult.revision;state();
    const result=await api('terms?'+new URLSearchParams({query:$('term-query').value,offset,limit:20}));$('term-list').replaceChildren();
    for(const term of result.results){const button=el('button',term.title,'record-link');button.type='button';button.onclick=guard(()=>openTerm(term.id));$('term-list').append(button);}
    $('term-prev').disabled=offset===0;$('term-next').disabled=offset+20>=result.total;$('term-page').textContent=`${result.total?offset+1:0}—${Math.min(offset+20,result.total)} / ${result.total}`;
    const drafts=await api('editor/records?'+new URLSearchParams({kind:'drafts',offset:draftOffset,limit:20}));$('term-drafts').replaceChildren();
    for(const draft of drafts.records){const button=el('button',draft.title,'record-link');button.type='button';button.onclick=guard(async()=>{if(!draft.term_document){location.href='/manage/advanced?kind=drafts';return;}documentValue={term:structuredClone(draft.term_document.term),resources:structuredClone(draft.term_document.resources)};deletions=structuredClone(draft.term_document.delete||[]);baseRevision=revision;draftId=draft.id;shared=[];external={};if(catalog.terms.some(t=>t.id===documentValue.term.id)){const existing=await api('editor/terms/'+documentValue.term.id);shared=existing.shared_usage;external=existing.external_sense_ids;}$('term-message').value=draft.title;render();report(draft.validation);state();});$('term-drafts').append(button);}
    $('draft-prev').disabled=draftOffset===0;$('draft-next').disabled=draftOffset+20>=drafts.total;$('draft-page').textContent=`${drafts.total?draftOffset+1:0}—${Math.min(draftOffset+20,drafts.total)} / ${drafts.total}`;
    await loadUnassigned();
  }
  async function loadUnassigned(){const result=await api('editor/unassigned?'+new URLSearchParams({offset:unassignedOffset,limit:20}));$('unassigned-list').replaceChildren();for(const item of result.results){const wrap=el('div',undefined,'source');wrap.append(el('p',Object.values(item.concept.labels).join(' / ')),el('p',item.concept.definition));for(const create of [false,true]){const button=el('button',create?'据此新建术语':'加入当前术语','secondary');button.type='button';button.onclick=guard(async()=>{if(create||!documentValue){documentValue=newDocument();documentValue.term.title=item.concept.labels['zh-Hans']||Object.values(item.concept.labels)[0];deletions=[];draftId=null;shared=[];external={};baseRevision=revision;}else collect();if(!documentValue.term.concept_ids.includes(item.concept.id))documentValue.term.concept_ids.push(item.concept.id);const assembled=await api('editor/terms/assemble',{...documentValue.term,concept_ids:documentValue.term.concept_ids.filter(id=>catalog.concepts.some(c=>c.id===id)),article:catalog.articles.some(a=>a.id===documentValue.term.article)?documentValue.term.article:null});for(const [kind,items]of Object.entries(assembled.document.resources)){const existing=documentValue.resources[kind]||=[];for(const record of items){const found=existing.find(item=>item.id===record.id);if(!found)existing.push(record);else if(kind==='entries')for(const sense of record.senses)if(!found.senses.some(s=>s.id===sense.id))found.senses.push(sense);}}shared=assembled.shared_usage;external=assembled.external_sense_ids;render();state();});wrap.append(button);}$('unassigned-list').append(wrap);}$('unassigned-prev').disabled=unassignedOffset===0;$('unassigned-next').disabled=unassignedOffset+20>=result.total;$('unassigned-page').textContent=`${result.total?unassignedOffset+1:0}—${Math.min(unassignedOffset+20,result.total)} / ${result.total}`;}
  async function save(destination){const payload=request(destination);if(destination==='publish'&&deletions.length){const preview=await api('editor/terms/preview',payload);report(preview);if(!preview.valid)return;if(!confirm(`确认执行 ${deletions.length} 项明确删除？未列出的资料保持原样。`))return;}
    const result=await api('editor/terms/save',payload);report(result);revision=result.revision;baseRevision=revision;
    if(destination==='draft')draftId=result.id;else{draftId=null;deletions=[];if(payload.delete.some(d=>d.kind==='terms'&&d.id===documentValue.term.id)){documentValue=null;$('term-workspace').hidden=true;}else await openTerm(documentValue.term.id);}
    await refresh();state();
  }
  function addRecord(kind){if(!documentValue)throw new Error('请先新增或打开术语');collect();const record=blank(schemas[kind],schemas[kind],kind);record.id=kind==='articles'?'articles/'+uid('article')+'.md':uid(kind.replace(/s$/,''));
    if(kind==='concepts'){record.labels={'zh-Hans':documentValue.term.title};documentValue.term.concept_ids.push(record.id);}
    if(kind==='entries'){record.language='de';record.part_of_speech='noun';record.senses=[{id:uid('sense'),definition:'',usage:'',concept_id:documentValue.term.concept_ids[0]||'',work_ids:[],evidence_ids:[],status:'draft'}];}
    if(kind==='attestations'){record.note='';record.passages=[{id:uid('passage'),edition_id:'',text:'',speaker:'马克思',material:'body',location:{section:'',url:'',precision:'quote',printed_page:null,manuscript_page:null,note:''},mentions:[]}];}
    if(kind==='articles'&&!documentValue.term.article)documentValue.term.article=record.id;
    documentValue.resources[kind].push(record);newRecords.add(record.id);render();
  }
  $('new-term').onclick=guard(()=>{documentValue=newDocument();baseRevision=revision;shared=[];external={};deletions=[];draftId=null;render();state();});
  $('term-search').onsubmit=guard(async()=>{offset=0;await refresh();});$('term-prev').onclick=guard(async()=>{offset=Math.max(0,offset-20);await refresh();});$('term-next').onclick=guard(async()=>{offset+=20;await refresh();});
  $('draft-prev').onclick=guard(async()=>{draftOffset=Math.max(0,draftOffset-20);await refresh();});$('draft-next').onclick=guard(async()=>{draftOffset+=20;await refresh();});
  $('unassigned-prev').onclick=guard(async()=>{unassignedOffset=Math.max(0,unassignedOffset-20);await loadUnassigned();});$('unassigned-next').onclick=guard(async()=>{unassignedOffset+=20;await loadUnassigned();});
  for(const [button,kind]of Object.entries({'add-concept':'concepts','add-entry':'entries','add-translation':'translations','add-attestation':'attestations','add-work':'works','add-edition':'editions','add-article':'articles'}))$(button).onclick=guard(()=>addRecord(kind));
  $('preview-term').onclick=guard(async()=>report(await api('editor/terms/preview',request())));$('whole-term-form').onsubmit=guard(()=>save('publish'));$('save-term-draft').onclick=guard(()=>save('draft'));
  $('delete-main-term').onclick=guard(()=>{collect();deleted('terms',documentValue.term.id);render();report({detail:'已将主条目加入明确删除清单。底层资料保留；点击保存整个术语完成。'});});
  $('delete-term-draft').onclick=guard(async()=>{if(!draftId||!confirm('删除当前编辑草稿？'))return;const result=await api('editor/drafts/'+draftId,{expected_revision:revision},'DELETE');report(result);revision=result.revision;baseRevision=revision;draftId=null;render();await refresh();});
  $('download-term').onclick=guard(()=>{collect();const content=JSON.stringify({format:'term-bundle/1',documents:[documentValue]},null,2),link=el('a');link.href=URL.createObjectURL(new Blob([content],{type:'application/json'}));link.download=documentValue.term.id+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);});
  async function importFile(destination){const result=await api('editor/import',{expected_revision:revision,format:$('term-import-format').value,content:$('term-import-content').value,kind:$('term-import-kind').value,mode:$('term-import-mode').value,destination,title:'导入术语资料'});report(result);if(result.revision!==undefined){revision=result.revision;await refresh();}}
  $('term-import-preview').onclick=guard(()=>importFile('preview'));$('term-import-draft').onclick=guard(()=>importFile('draft'));$('term-import-publish').onclick=guard(()=>importFile('publish'));
  $('term-import-file').onchange=guard(async()=>{const file=$('term-import-file').files[0];if(!file)return;$('term-import-content').value=await file.text();const ext=file.name.split('.').at(-1).toLowerCase();$('term-import-format').value=({yml:'yaml',xml:'tbx'})[ext]||ext;});
  guard(async()=>{const kinds=['terms','concepts','entries','translations','attestations','works','editions','articles'];await Promise.all(kinds.map(async kind=>schemas[kind]=(await api('editor/schema?kind='+kind)).schema));await refresh();const id=new URLSearchParams(location.search).get('term_id');if(id)await openTerm(id);})();
})();
