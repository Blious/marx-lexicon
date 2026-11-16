/* 本地资料管理。记录表单按需加载模型，批次和草稿共用编辑操作。 */
(() => {
  const $ = id => document.getElementById(id);
  const names = {id:'稳定 ID',language:'语言',lemma:'原词 / 词条显示名',part_of_speech:'词性',forms:'词形',senses:'义项',definition:'定义',concept_id:'概念 ID',usage:'用法',work_ids:'作品 ID',evidence_ids:'证据 ID',status:'学术状态',article:'辨析文章路径',text:'文本 / 实际术语',kind:'类别',note:'说明',labels:'分语言标签',relations:'概念关系',target_id:'目标概念 ID',explanation:'关系说明',source_sense_id:'原文义项 ID',target_sense_id:'目标义项 ID',scope:'适用条件',edition_ids:'版本 ID',passages:'各语言引文',alignments:'对应组',external_refs:'外部引用',review_status:'证据审阅状态',edition_id:'版本 ID',location:'出处位置',speaker:'作者 / 说话者',material:'材料类别',mentions:'术语标注',entry_id:'词条 ID',sense_id:'义项 ID',start:'起点（Python Unicode 索引）',end:'终点（不含）',passage_ids:'对应段落 ID',mention_refs:'精确术语对应',level:'对应层级',passage_id:'段落 ID',mention_index:'术语标注序号（从 0 开始）',section:'章节',url:'出处链接',printed_page:'印刷书页',manuscript_page:'稿页',precision:'位置精度',provider:'提供者',version:'版本',title:'标题',author:'作者',written:'写作年代',original_language:'原文语言',work_id:'作品 ID',citation:'版本书目',translators:'译者',publication_year:'出版年',source_language:'原文语言',target_language:'目标语言',dataset_version:'数据版本（发布自动更新）',selections:'项目选择',translation_id:'译法 ID',content:'Markdown 正文'};
  let revision=0, batchRevision=0, loadSequence=0, kind='entries', offset=0, total=0, current=null, schema=null, getter=null, patchGetter=null, queue=[], draftId=null, draftNotes=[];
  const schemas = new Map();
  const node = (tag, text, cls) => {const el=document.createElement(tag); if(text!==undefined) el.textContent=text; if(cls) el.className=cls; return el;};
  const uid = prefix => prefix+'-'+crypto.randomUUID().replaceAll('-','').slice(0,12);
  async function api(path, body, method='POST') {
    const res=await fetch('/api/editor/'+path,body===undefined?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await res.json();
    if(!res.ok) {show(data.report || data); throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));}
    return data;
  }
  function status(data) {if(data.revision!==undefined) revision=data.revision; $('editing-status').textContent='编辑版本 '+revision+' · 仅本地保存，可手动提交推送';}
  function show(data) {
    const box=$('edit-result'); box.replaceChildren(node('h2','操作结果'));
    if(data.valid!==undefined) box.append(node('p',data.valid?'整批校验通过。':'整批校验未通过，可修订或保存为编辑草稿。'));
    if(data.local_saved) box.append(node('p','已保存到本地 · 编辑版本 '+data.revision+(data.dataset_version?' · 数据 '+data.dataset_version:'')));
    if(data.detail || data.error) box.append(node('p',typeof(data.detail||data.error)==='string'?(data.detail||data.error):JSON.stringify(data.detail||data.error)));
    for(const error of data.errors || data.validation?.errors || []) box.append(node('p',[error.id,error.field,error.message].filter(Boolean).join(' · ')));
    for(const ref of data.references || []) box.append(node('p','引用：'+ref.kind+' / '+ref.id+' / '+ref.field+' → '+ref.target));
    for(const note of data.notes || []) box.append(node('p',note));
    if(data.counts) box.append(node('p','操作数量：'+JSON.stringify(data.counts)));
    if(data.import_notes_id) box.append(node('p','导入说明已保留：'+data.import_notes_id+'，可从导入注记目录查阅。'));
    if(data.operations) {const details=node('details');details.append(node('summary','查看导入操作（含生成的稳定 ID）'),node('pre',JSON.stringify(data.operations,null,2)));box.append(details);}
    box.focus();
  }
  const guard = fn => async e => {e?.preventDefault();try {await fn(e);} catch(error){if(!$('edit-result').textContent.includes(error.message)) $('edit-result').append(node('p',error.message));}};
  async function getSchema(value) {if(!schemas.has(value)) schemas.set(value,(await api('schema?kind='+value)).schema);return schemas.get(value);}
  function resolved(spec, root) {
    if(spec.$ref) return root.$defs[spec.$ref.split('/').at(-1)];
    if(spec.anyOf) return {...spec.anyOf.find(s=>s.type!=='null'),nullable:true};
    return spec;
  }
  function field(parent, original, value, key, root, disabled=false) {
    const spec=resolved(original,root), label=names[key]||key;
    if(value===undefined) value=structuredClone(spec.default);
    if(spec.type==='object') {
      const group=node('fieldset'), legend=node('legend',label); group.append(legend);parent.append(group);
      if(spec.properties) {
        const readers={};
        for(const [k,s] of Object.entries(spec.properties)) readers[k]=field(group,s,value?.[k],k,root,disabled || (k==='id' && current?.action==='update'));
        return () => Object.fromEntries(Object.entries(readers).map(([k,read])=>[k,read()]));
      }
      const entries=[];
      const add=(k='',v='')=>{const row=node('div',undefined,'mapping-row');const lang=node('input');lang.value=k;lang.placeholder='语言，如 zh-Hans';lang.setAttribute('aria-label',label+'的语言');const text=node('input');text.value=v;text.setAttribute('aria-label',label+'的值');const remove=node('button','移除','secondary');remove.type='button';remove.onclick=()=>{entries.splice(entries.indexOf(pair),1);row.remove();};const pair={lang,text};entries.push(pair);row.append(lang,text,remove);group.append(row);};
      for(const [k,v] of Object.entries(value||{})) add(k,v);
      const button=node('button','添加标签','secondary');button.type='button';button.onclick=()=>add();group.append(button);
      return ()=>Object.fromEntries(entries.filter(e=>e.lang.value).map(e=>[e.lang.value,e.text.value]));
    }
    if(spec.type==='array') {
      const item=resolved(spec.items,root);
      if(item.type==='string') {const wrap=node('label',label+'（每行一个）'), input=node('textarea');input.rows=2;input.value=(value||[]).join('\n');wrap.append(input);parent.append(wrap);return ()=>input.value.split('\n').map(s=>s.trim()).filter(Boolean);}
      const group=node('fieldset');group.append(node('legend',label));parent.append(group);const readers=[];
      const add=(v)=>{const row=node('div',undefined,'array-item');const read=field(row,spec.items,v,key,root);const remove=node('button','移除此项','secondary');remove.type='button';remove.onclick=()=>{readers.splice(readers.indexOf(read),1);row.remove();};readers.push(read);row.append(remove);group.insertBefore(row,button);};
      const button=node('button','添加一项','secondary');button.type='button';button.onclick=()=>add(undefined);group.append(button);for(const v of value||[]) add(v);
      return ()=>readers.map(read=>read());
    }
    const wrap=node('label',label);let input;
    if(spec.enum) {input=node('select');for(const option of spec.enum){const el=node('option',option);el.value=option;input.append(el);}input.value=value??spec.enum[0];}
    else if(['definition','usage','text','note','explanation','content','citation'].includes(key)) {input=node('textarea');input.rows=key==='content'?10:3;input.value=value??'';}
    else {input=node('input');input.type=spec.type==='integer'?'number':'text';input.value=value??(key==='id'?uid('record'):'');if(spec.minimum!==undefined) input.min=spec.minimum;}
    input.disabled=disabled; input.dataset.field=key; wrap.append(input);parent.append(wrap);
    return ()=>spec.nullable && input.value===''?null:spec.type==='integer'?Number(input.value):input.value;
  }
  async function editRecord(record, action='update') {
    draftId=null;draftNotes=[];$('publish-draft').hidden=true;$('delete-draft').hidden=true;
    current={kind,id:record?.id,action,revision};schema=await getSchema(kind);
    $('record-form').hidden=false;
    $('record-heading').textContent=(action==='create'?'新增':'编辑')+' · '+(record?.lemma||record?.title||record?.id||names[kind]||kind);
    $('record-fields').replaceChildren();getter=field($('record-fields'),schema,record,'资源',schema);
    $('record-references').hidden=action==='create';$('review-record').hidden=action==='create'||kind==='articles';
    $('article-preview').replaceChildren();
    if(kind==='articles') {const button=node('button','预览 Markdown','secondary');button.type='button';button.onclick=guard(async()=>{const result=await api('markdown',{content:getter().content});$('article-preview').innerHTML=result.html;});$('record-fields').append(button);}
  }
  function operation() {if(!getter||!current) throw new Error('请先选择或新增资源');const values=getter();if(!values.id) throw new Error('请填写稳定 ID');return {kind:current.kind,id:values.id,action:current.action,values};}
  function batch(operations=queue) {if(!operations.length) throw new Error('编辑批次为空');return {expected_revision:operations===queue?batchRevision:current?.revision??revision,operations,message:$('batch-title').value||'更新术语资料'};}
  function addQueue(op, base=revision) {if(!queue.length)batchRevision=base;const index=queue.findIndex(o=>o.kind===op.kind&&o.id===op.id);if(index<0) queue.push(op);else queue[index]=op;renderQueue();}
  function renderQueue() {$('batch-list').replaceChildren();queue.forEach((op,index)=>{const row=node('div',undefined,'queue-row');const detail=node('details');detail.append(node('summary',op.action+' · '+op.kind+' / '+op.id),node('pre',JSON.stringify(op.values,null,2)));const edit=node('button','编辑','secondary');edit.type='button';edit.disabled=!['create','update'].includes(op.action);edit.onclick=guard(async()=>{const savedDraft=draftId, notes=draftNotes;kind=op.kind;$('resource-kind').value=kind;await loadRecords();let value=op.values;if(op.action==='update'){const existing=await api('records?'+new URLSearchParams({kind,identifiers:op.id}));value={...existing.records[0],...value};}await editRecord({...value,id:op.id},op.action);current.revision=batchRevision;draftId=savedDraft;draftNotes=notes;$('publish-draft').hidden=!draftId;$('delete-draft').hidden=!draftId;$('record-heading').scrollIntoView({block:'start'});});const remove=node('button','移除','secondary');remove.type='button';remove.onclick=()=>{queue.splice(index,1);renderQueue();};row.append(detail,edit,remove);$('batch-list').append(row);});}
  const selected=()=>Array.from($('record-list').querySelectorAll('input:checked')).map(el=>el.value);
  async function loadRecords() {
    kind=$('resource-kind').value;
    const sequence=++loadSequence;$('record-list').replaceChildren(node('p','正在读取…'));$('new-record').disabled=true;
    const result=await api('records?'+new URLSearchParams({kind,offset,limit:20}));if(sequence!==loadSequence)return;
    const meta=await fetch('/api/metadata').then(response=>response.json());if(sequence!==loadSequence)return;
    $('dataset-summary').textContent=`数据 ${meta.dataset_version} · 模型 ${meta.schema_version} · ${meta.counts.entries} 个词条 / ${meta.counts.concepts} 个概念`;
    $('dataset-review-summary').textContent=`语例：${meta.review_counts.unreviewed} 组待审阅，${meta.review_counts.reviewed} 组已审阅。项目选用与学术审定分别记录。`;
    status(result);total=result.total;
    $('record-list').replaceChildren();$('select-page').checked=false;
    for(const record of result.records) {const row=node('div',undefined,'record-row');const check=node('input');check.type='checkbox';check.value=record.id;check.setAttribute('aria-label','选择 '+record.id);const button=node('button',record.lemma||record.title||record.id,'record-link');button.type='button';button.onclick=guard(async()=>{if(kind==='import_notes')show(record);else if(kind==='drafts'){queue=record.operations;batchRevision=revision;draftId=record.id;draftNotes=record.notes;$('batch-title').value=record.title;renderQueue();$('publish-draft').hidden=false;$('delete-draft').hidden=false;show(record.validation);}else await editRecord(record);});row.append(check,button);$('record-list').append(row);}
    $('records-page').textContent=(total?offset+1:0)+'—'+Math.min(offset+20,total)+' / '+total;$('records-prev').disabled=offset===0;$('records-next').disabled=offset+20>=total;
    const auxiliary=['drafts','import_notes'].includes(kind);$('new-record').disabled=auxiliary;$('bulk-delete').disabled=auxiliary;$('bulk-patch').disabled=auxiliary;
  }
  async function apply(operations=queue) {
    const request=batch(operations), deletes=operations.filter(o=>o.action==='delete');
    if(deletes.length) {const report=await api('preview',request);show(report);if(!report.valid)return;if(!confirm('确认删除 '+deletes.length+' 个资源？引用已按本批次最终状态检查。'))return;}
    const result=await api('apply',request);
    if(current) {const saved=operations.find(op=>op.kind===current.kind&&op.id===getter().id);if(saved){if(saved.action==='delete'){$('record-form').hidden=true;current=null;getter=null;}else{current.action='update';current.revision=result.revision;current.id=saved.id;$('record-fields').querySelector('[data-field="id"]').disabled=true;$('record-references').hidden=false;$('review-record').hidden=current.kind==='articles';}}}
    show(result);status(result);queue=[];draftId=null;renderQueue();await loadRecords();
  }
  async function doImport(destination) {
    const request={expected_revision:revision,format:$('import-format').value,content:$('import-content').value,kind:['drafts','import_notes'].includes(kind)?'entries':kind,mode:$('import-mode').value,columns:JSON.parse($('import-columns').value||'{}'),destination,title:$('batch-title').value||'导入资料',include_operations:true};
    const result=await api('import',request);show(result);
    if(destination==='preview') {queue=result.operations;batchRevision=revision;draftId=null;draftNotes=result.notes||[];$('publish-draft').hidden=true;$('delete-draft').hidden=true;renderQueue();}
    else {status(result);await loadRecords();if(destination==='draft'){draftId=result.id;$('publish-draft').hidden=false;$('delete-draft').hidden=false;}else{queue=[];renderQueue();}}
  }
  $('resource-kind').onchange=guard(async()=>{offset=0;$('patch-panel').hidden=true;await loadRecords();});
  $('reload-records').onclick=guard(loadRecords);
  $('records-prev').onclick=guard(async()=>{offset=Math.max(0,offset-20);await loadRecords();});$('records-next').onclick=guard(async()=>{offset+=20;await loadRecords();});
  $('select-page').onchange=()=>{$('record-list').querySelectorAll('input').forEach(el=>el.checked=$('select-page').checked);};
  $('new-record').onclick=guard(()=>editRecord({id:kind==='articles'?'articles/'+uid('article')+'.md':uid(kind.replace(/s$/,''))},'create'));
  $('record-form').onsubmit=guard(()=>apply([operation()]));$('queue-record').onclick=guard(()=>addQueue(operation(),current.revision));
  $('record-references').onclick=guard(async()=>{const result=await api('references/'+current.kind+'/'+encodeURIComponent(current.id));show(result);addQueue({kind:current.kind,id:current.id,action:'delete',values:{}},current.revision);});
  $('review-record').onclick=guard(async()=>{if(confirm('确认已人工审阅此资源及其义项、对应或关系？'))await apply([{kind:current.kind,id:current.id,action:'review',values:{}}]);});
  $('bulk-delete').onclick=guard(()=>{if(!selected().length)throw new Error('请先勾选资源');for(const id of selected())addQueue({kind,id,action:'delete',values:{}});});
  $('bulk-patch').onclick=guard(async()=>{if(!selected().length)throw new Error('请先勾选资源');schema=await getSchema(kind);$('patch-field').replaceChildren();for(const key of Object.keys(schema.properties).filter(k=>k!=='id')){const option=node('option',names[key]||key);option.value=key;$('patch-field').append(option);}$('patch-panel').hidden=false;renderPatch();});
  function renderPatch() {$('patch-value').replaceChildren();const key=$('patch-field').value;patchGetter=field($('patch-value'),schema.properties[key],undefined,key,schema);}
  $('patch-field').onchange=renderPatch;$('queue-patch').onclick=guard(()=>{for(const id of selected())addQueue({kind,id,action:'update',values:{[$('patch-field').value]:patchGetter()}});});
  $('clear-batch').onclick=()=>{queue=[];draftId=null;draftNotes=[];renderQueue();$('publish-draft').hidden=true;$('delete-draft').hidden=true;};
  $('preview-batch').onclick=guard(async()=>show(await api('preview',batch())));$('apply-batch').onclick=guard(()=>apply());
  $('save-draft').onclick=guard(async()=>{if(!queue.length && getter)addQueue(operation(),current.revision);const result=await api('drafts',{expected_revision:batchRevision,id:draftId,title:$('batch-title').value,operations:queue,notes:draftNotes});show(result);status(result);batchRevision=revision;draftId=result.id;$('publish-draft').hidden=false;$('delete-draft').hidden=false;await loadRecords();});
  $('publish-draft').onclick=guard(async()=>{const result=await api('drafts/'+draftId+'/publish',{expected_revision:revision});show(result);status(result);queue=[];draftId=null;current=null;getter=null;$('record-form').hidden=true;renderQueue();$('publish-draft').hidden=true;$('delete-draft').hidden=true;await loadRecords();});
  $('delete-draft').onclick=guard(async()=>{if(!confirm('确认删除当前编辑草稿？'))return;const result=await api('drafts/'+draftId,{expected_revision:revision},'DELETE');show(result);status(result);draftId=null;queue=[];renderQueue();$('publish-draft').hidden=true;$('delete-draft').hidden=true;await loadRecords();});
  $('import-file').onchange=guard(async()=>{const file=$('import-file').files[0];if(!file)return;$('import-content').value=await file.text();const extension=file.name.split('.').at(-1).toLowerCase();$('import-format').value=({yml:'yaml',xml:'tbx'})[extension]||extension;});
  $('preview-import').onclick=guard(()=>doImport('preview'));$('draft-import').onclick=guard(()=>doImport('draft'));$('apply-import').onclick=guard(()=>doImport('publish'));
  guard(async()=>{const params=new URLSearchParams(location.search);if(params.has('kind'))$('resource-kind').value=params.get('kind');await loadRecords();if(params.has('id')){const result=await api('records?'+new URLSearchParams({kind,identifiers:params.get('id')}));await editRecord(result.records[0]);}})();
})();
