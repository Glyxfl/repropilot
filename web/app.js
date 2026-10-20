'use strict';
const paths = {
  grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  clock:'M12 8v5l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  database:'M20 6c0 2-3.6 3-8 3S4 8 4 6s3.6-3 8-3 8 1 8 3z M4 6v12c0 2 3.6 3 8 3s8-1 8-3V6 M4 12c0 2 3.6 3 8 3s8-1 8-3',
  layers:'m12 3 10 6-10 6L2 9z M2 13l10 6 10-6 M2 17l10 6 10-6',
  github:'M9 19c-4 1-4-2-6-2 M9 22v-4c0-1 .2-2 1-2-4 0-7-2-7-6 0-2 1-3 2-4-1-1-1-3 0-4 2 0 3 1 4 2 2-1 4-1 6 0 1-1 2-2 4-2 1 1 1 3 0 4 1 1 2 2 2 4 0 4-3 6-7 6 1 0 1 1 1 2v4',
  flask:'M9 3h6 M10 3v6L4 19c-1 1 0 2 1 2h14c1 0 2-1 1-2L14 9V3 M8 14h8',
  sparkles:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z M20 2v4 M18 4h4',
  lock:'M5 10h14v11H5z M8 10V7a4 4 0 0 1 8 0v3 M12 15v2',
  shield:'m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z m-4 9 3 3 5-6',
  workflow:'M3 3h6v6H3z M15 15h6v6h-6z M15 3h6v6h-6z M9 6h6 M6 9v9h9',
  file:'M5 3h9l5 5v13H5z M14 3v6h5 M8 13h8 M8 17h6',
  chart:'M3 3v18h18 M7 16v-4 M12 16V7 M17 16v-7',
  check:'m5 12 4 4L19 6',
  download:'M12 3v12 m-5-5 5 5 5-5 M4 16v5h16v-5',
  arrow:'M4 12h16 m-6-6 6 6-6 6',
};
function icon(name){return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.file}"/></svg>`;}
function icons(root=document){root.querySelectorAll('[data-icon]').forEach(el=>{el.innerHTML=icon(el.dataset.icon);});}
const $=id=>document.getElementById(id);
const escapeHtml=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const format=value=>Number(value).toLocaleString('en-US');
const labels={awaiting_approval:'等待你确认',queued:'排队中',running:'正在执行',succeeded:'交付完成',failed:'执行中断'};
const stages=[
  ['inspect_dataset','载入研究数据','校验来源与输入指纹'],
  ['validate_design','检查实验设计','计数、重复数与设计矩阵'],
  ['fit_deseq2','执行差异分析','负二项模型 · Wald 检验'],
  ['render_figures','生成研究图表','火山图 · 探索性 PCA'],
  ['write_report','整理分析报告','方法、结果与复现脚本'],
  ['verify_delivery','验收并交付','产物检查 · SHA-256 清单'],
];
const presets={
  standard:'使用 pasilla 公开数据，比较 treated vs untreated，校正测序类型；FDR < 0.05，|log2FC| >= 1，生成火山图、PCA 和可复现报告。',
  strict:'比较 treated vs untreated，校正测序类型，FDR < 0.01，|log2FC| >= 1.5，完成差异分析并生成图表与报告。',
  reverse:'比较 untreated vs treated，校正测序类型，FDR < 0.05，|log2FC| >= 1，生成差异基因结果、火山图和 PCA。',
};
let activeRun=null, runs=[], datasets=[], polling=null, currentView='workspace', lastFingerprint='', toastTimer;

async function api(path,body){
  const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await response.json().catch(()=>({detail:'服务返回了无效响应'}));
  if(!response.ok){const message=Array.isArray(data.detail)?data.detail.map(x=>`${x.loc?.slice(-1)}: ${x.msg}`).join('；'):data.detail;throw new Error(message||'请求失败');}
  return data;
}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,8500);}
function busy(button,state,label){button.disabled=state;button.setAttribute('aria-busy',String(state));if(label)button.innerHTML=label;}
function setView(view){currentView=view;document.querySelectorAll('.view').forEach(v=>v.hidden=v.id!==`${view}-view`);document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));$('page-title').textContent={workspace:'分析工作台',history:'执行记录',datasets:'数据与来源',about:'工作原理'}[view];if(view==='history')renderHistory();window.scrollTo({top:0});}
function renderStages(run){
  const states={};(run?.events||[]).forEach(e=>states[e.tool]=e.status);
  $('pipeline-stages').innerHTML=stages.map(([tool,title,subtitle],index)=>{
    const state=states[tool]||'pending';const mark=['complete','cached'].includes(state)?'✓':state==='failed'?'!':String(index+1).padStart(2,'0');
    return `<div class="stage ${state}"><span class="stage-index">${mark}</span><div><strong>${title}</strong><small>${state==='cached'?'已验证并复用检查点':subtitle}</small></div></div>`;
  }).join('');
  const complete=stages.filter(([tool])=>['complete','cached'].includes(states[tool])).length;
  $('stage-counter').textContent=run?`${complete} / 6 DONE`:'6 TOOLS';
}
function renderDataset(){
  const data=datasets.find(d=>d.id===$('dataset').value);if(!data)return;
  $('dataset-name').textContent=data.name;$('dataset-description').textContent=data.id==='pasilla'?'果蝇 RNA-seq · GSE18508':'固定种子 42 · 合成负二项计数';
  $('dataset-samples').textContent=data.samples;$('dataset-genes').textContent=format(data.genes);$('data-kind').textContent=data.kind.toUpperCase();
  $('reference-n').textContent='4';$('treatment-n').textContent=data.id==='pasilla'?'3':'4';
  $('dataset-source').href=data.id==='pasilla'?data.source:'https://github.com/Glyxfl/repropilot/blob/main/repropilot/datasets.py';
}
function traceHtml(events){
  return `<details class="trace-details" ${activeRun?.status==='failed'?'open':''}><summary><span>查看工具调用与执行证据</span><span>${events.length} EVENTS ↓</span></summary><ol class="trace-events">${events.map(e=>`<li><div class="event-head"><span>${escapeHtml(e.tool)} / ${escapeHtml(e.status)}</span><time>${new Date(e.time).toLocaleTimeString('zh-CN',{hour12:false})}</time></div><p>${escapeHtml(e.message)}</p>${Object.keys(e.details).length?`<details><summary>结构化证据</summary><pre>${escapeHtml(JSON.stringify(e.details,null,2))}</pre></details>`:''}</li>`).join('')}</ol></details>`;
}
function renderRun(run){
  activeRun=run;localStorage.setItem('repropilot:last-run',run.id);renderStages(run);$('run-panel').hidden=false;$('welcome-panel').hidden=true;
  const plan=run.plan;const ready=run.status==='awaiting_approval';const failed=run.status==='failed';const success=run.status==='succeeded';
  const latest=run.events.at(-1);
  $('run-panel').innerHTML=`<div class="run-heading"><div><span class="run-id">RUN / ${run.id} · ${run.planner==='live'?'真实大模型规划':'离线规则规划'}</span><h2>${ready?'分析方案已就绪，确认后开始执行':success?'这次研究，已完成交付':failed?'执行已暂停，检查点为你保留':'正在把方案变成结果'}</h2></div><span class="state-badge ${run.status}">${success?'✓ ':''}${labels[run.status]||run.status}</span></div><p class="plan-description">${escapeHtml(plan.rationale)}</p><dl class="plan-parameters"><div><dt>比较方向</dt><dd>${escapeHtml(plan.treatment)} / ${escapeHtml(plan.reference)}</dd></div><div><dt>模型设计</dt><dd>${plan.adjust_library_type?'~ library_type + condition':'~ condition'}</dd></div><div><dt>显著性与效应阈值</dt><dd>padj &lt; ${plan.alpha} · |log2FC| ≥ ${plan.lfc_threshold}</dd></div><div><dt>低表达过滤</dt><dd>count ≥ ${plan.min_count} in ≥ ${plan.min_samples} samples</dd></div></dl>${ready?`<div class="plan-approval"><p>确认分组方向、测序类型协变量和阈值。方案冻结后，6 个工具将自动执行。${run.recovery_demo?'本次开启首次图表失败演练。':''}</p><button class="button primary large" id="approve-run">${icon('check')}确认并执行</button></div>`:''}${failed?`<div class="run-error">${escapeHtml(run.error)}</div><button class="button primary" id="retry-run">${icon('arrow')}从检查点重试</button>`:''}${['queued','running'].includes(run.status)?`<div class="run-progress">${escapeHtml(latest?.message||'任务正在排队…')}<br><span class="run-id">工具在独立进程执行；刷新页面后仍可继续查看。</span></div>`:''}${success?`<div class="run-actions"><a class="button primary" href="/api/runs/${run.id}/artifacts/delivery.zip">${icon('download')}下载完整交付包</a><a class="button secondary" href="/api/runs/${run.id}/artifacts/report.html" target="_blank" rel="noreferrer">打开分析报告 ↗</a><a class="button secondary" href="/api/runs/${run.id}/artifacts/manifest.json" target="_blank" rel="noreferrer">产物校验清单 ↗</a></div>`:''}${traceHtml(run.events)}`;
  if(ready)$('approve-run').onclick=()=>runAction('approve',{plan_hash:run.plan_hash},'approve-run');
  if(failed)$('retry-run').onclick=()=>runAction('retry',{},'retry-run');
  if(success)renderResults(run);else $('results-panel').hidden=true;
}
function renderResults(run){
  const s=run.summary;if(!s)return;const base=`/api/runs/${run.id}/artifacts/`;
  $('results-panel').hidden=false;$('results-panel').innerHTML=`<div class="results-header"><h2>结果已到达。</h2><span>${format(s.samples)} SAMPLES · ${s.elapsed_seconds}s（本次尝试）</span></div><div class="metric-grid"><div class="metric card"><span>达到阈值的差异基因</span><strong>${format(s.significant)}</strong><small>${format(s.tested_genes)} 个非 NA padj</small></div><div class="metric card up"><span>上调基因</span><strong>${format(s.up)}</strong><small>相对 ${escapeHtml(run.plan.reference)}</small></div><div class="metric card down"><span>下调基因</span><strong>${format(s.down)}</strong><small>|log2FC| ≥ ${run.plan.lfc_threshold}</small></div></div><div class="chart-grid"><section class="chart-card card"><div class="card-heading"><h3>差异表达 / Volcano</h3><a href="${base}volcano.svg" target="_blank" rel="noreferrer">SVG ↗</a></div><a href="${base}volcano.png" target="_blank" rel="noreferrer"><img src="${base}volcano.png" alt="基于本次 PyDESeq2 计算结果生成的火山图"></a><div class="chart-caption">纵轴为 −log10(padj)；横轴为未经收缩的 log2FC。仅显示非缺失 padj。</div></section><section class="chart-card card"><div class="card-heading"><h3>样本关系 / PCA</h3><a href="${base}pca.svg" target="_blank" rel="noreferrer">SVG ↗</a></div><a href="${base}pca.png" target="_blank" rel="noreferrer"><img src="${base}pca.png" alt="基于归一化计数生成的探索性 PCA 图"></a><div class="chart-caption">log2(norm + 1)，最多 500 个高变基因。PC1 ${(s.pca_pc1*100).toFixed(1)}% · PC2 ${(s.pca_pc2*100).toFixed(1)}%。</div></section></div><section class="results-table card"><div class="card-heading"><h3>差异基因 · 前 ${s.top_genes.length} 项</h3><a class="text-link" href="${base}differential_expression.csv">完整 CSV ↓</a></div><table><thead><tr><th>GENE ID</th><th>LOG2 FOLD CHANGE</th><th>ADJUSTED P</th><th>DIRECTION</th></tr></thead><tbody>${s.top_genes.map(g=>`<tr><td>${escapeHtml(g.gene)}</td><td>${g.log2FoldChange.toFixed(3)}</td><td>${Number(g.padj).toExponential(2)}</td><td class="${g.log2FoldChange>0?'up':'down'}">${g.log2FoldChange>0?'↑ UP':'↓ DOWN'}</td></tr>`).join('')||'<tr><td colspan="4">当前阈值下没有显著基因。</td></tr>'}</tbody></table></section><p class="result-note">保留 ${format(s.retained_genes)} / ${format(s.input_genes)} 个基因；${format(s.na_padj)} 个 NA padj 未计入差异基因。${run.plan.dataset_id==='synthetic'?'本次使用合成数据，仅验证软件行为，不代表生物学发现。':'结果反映当前实验与方法下的统计关联，机制解释需要进一步验证。'}</p>`;
}
async function runAction(action,body,buttonId){
  const button=$(buttonId);busy(button,true);
  try{const run=await api(`/api/runs/${activeRun.id}/${action}`,body);lastFingerprint='';renderRun(run);await refreshHistory();schedulePoll();}
  catch(e){toast(e.message);busy(button,false);}
}
function schedulePoll(){clearTimeout(polling);if(activeRun&&['queued','running'].includes(activeRun.status))polling=setTimeout(pollRun,1500);}
async function pollRun(){
  const id=activeRun?.id;if(!id)return;
  try{const run=await api(`/api/runs/${id}`);if(activeRun?.id!==id)return;const fingerprint=`${run.status}/${run.events.length}`;if(fingerprint!==lastFingerprint){const opened=$('run-panel').querySelector('.trace-details')?.open;renderRun(run);if(opened)$('run-panel').querySelector('.trace-details').open=true;lastFingerprint=fingerprint;if(['succeeded','failed'].includes(run.status))await refreshHistory();}schedulePoll();}
  catch(e){toast('暂时无法读取执行状态。正在重试。');if(activeRun?.id===id)polling=setTimeout(pollRun,5000);}
}
async function selectRun(id){clearTimeout(polling);try{const run=await api(`/api/runs/${id}`);lastFingerprint='';renderRun(run);$('dataset').value=run.plan.dataset_id;renderDataset();setView('workspace');$('run-panel').scrollIntoView({behavior:'smooth',block:'start'});schedulePoll();}catch(e){toast(e.message);}}
async function refreshHistory(){
  runs=await api('/api/runs');$('history-count').textContent=runs.length;
  $('recent-runs').innerHTML=runs.slice(0,4).map(r=>`<button class="recent-item" data-run="${r.id}" title="${escapeHtml(r.prompt)}"><span class="run-dot ${r.status}"></span><span>${escapeHtml(r.prompt)}</span></button>`).join('')||'<p class="muted small empty-recent">你的第一个研究任务<br>从右侧开始。</p>';
  $('recent-runs').querySelectorAll('[data-run]').forEach(b=>b.onclick=()=>selectRun(b.dataset.run));
  if(currentView==='history')renderHistory();
}
function renderHistory(){
  $('history-list').innerHTML=runs.map(r=>`<button class="history-row" data-run="${r.id}"><div><span class="run-id">${r.id} · ${r.planner==='live'?'MODEL PLANNER':'OFFLINE PLANNER'}</span><h3>${escapeHtml(r.prompt)}</h3><small>${new Date(r.created_at).toLocaleString('zh-CN',{hour12:false})}</small></div><span class="state-badge ${r.status}">${labels[r.status]}</span></button>`).join('')||`<div class="card empty-state">${icon('clock')}<p>还没有执行记录。创建一个研究任务开始。</p><button class="button primary" id="first-task">开始第一个任务 ↗</button></div>`;
  $('history-list').querySelectorAll('[data-run]').forEach(b=>b.onclick=()=>selectRun(b.dataset.run));if($('first-task'))$('first-task').onclick=()=>setView('workspace');
}
function renderCatalog(){
  $('datasets-list').innerHTML=datasets.map(d=>`<section class="card dataset-detail"><span class="tiny-badge">${d.kind.toUpperCase()} / ${d.accession}</span><h2>${escapeHtml(d.name)}</h2><p>${escapeHtml(d.description)}</p><dl><div><dt>SAMPLES</dt><dd>${d.samples}</dd></div><div><dt>GENES</dt><dd>${format(d.genes)}</dd></div><div><dt>ORGANISM</dt><dd style="font-size:12px">${escapeHtml(d.organism)}</dd></div><div><dt>CONDITIONS</dt><dd style="font-size:12px">treated / untreated</dd></div></dl>${d.kind==='public'?`<a class="text-link" href="${d.source}" target="_blank" rel="noreferrer">Bioconductor 来源与引用 ↗</a>`:'<span class="muted small">固定种子 42 · 本地生成 · 不代表生物学发现</span>'}</section>`).join('');
}
async function createRun(){
  const button=$('create-run');busy(button,true,`${icon('sparkles')}正在生成方案…`);
  try{const run=await api('/api/runs',{prompt:$('task-prompt').value,dataset_id:$('dataset').value,planner:$('planner').value,recovery_demo:$('recovery-demo').checked});clearTimeout(polling);lastFingerprint='';renderRun(run);await refreshHistory();$('run-panel').scrollIntoView({behavior:'smooth',block:'start'});}
  catch(e){toast(e.message);}finally{busy(button,false,`${icon('sparkles')}<span>生成分析方案</span><span>↗</span>`);}
}
async function init(){
  icons();renderStages();document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>setView(b.dataset.view));
  document.querySelectorAll('[data-preset]').forEach(b=>b.onclick=()=>{$('task-prompt').value=presets[b.dataset.preset];$('task-prompt').focus();});
  $('create-run').onclick=createRun;$('dataset').onchange=renderDataset;
  try{datasets=await api('/api/datasets');renderDataset();renderCatalog();await refreshHistory();const health=await api('/api/health');if(health.live_model_configured)$('planner').options[1].textContent='真实大模型 · 已配置';
    const saved=localStorage.getItem('repropilot:last-run');if(saved&&runs.some(r=>r.id===saved)){renderRun(await api(`/api/runs/${saved}`));schedulePoll();}}
  catch(e){toast(`连接本地服务失败：${e.message}`);}
}
init();
