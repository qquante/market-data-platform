import {installExplorer} from '/ui/explorer.js';
import {installResearchViews} from '/ui/research_views.js';
const $ = id => document.getElementById(id);
let publicRead = false;
let key = '', activeTab = 'operations', operationsBusy = false, researchBusy = false, trajectoryBusy = false;
let lastOperation = null, positionCursor = null;
let strategyOffset = 0, strategyRequest = 0, selectedStrategyId = null;
const controllers = new Set(), previousResources = new Map();
const labels = {ready:'Pronto',running:'Em execução',queued:'Na fila',failed:'Falhou',partial:'Parcial',quarantined:'Quarentena',scheduled:'Agendado',retry:'Nova tentativa',inflight:'Consultando',awaiting_delivery:'Aguardando entrega',waiting_tracking:'Aguardando token',closed:'Fechada',open:'Aberta',entry_pending:'Entrada pendente',entry_expired:'Entrada vencida',upcross:'Subida',downcross:'Queda',reentry:'Reentrada',waiting:'Aguardando'};
const state = v => labels[v] || v || 'Indisponível';
const fmt = v => v === null || v === undefined ? '—' : Number.isFinite(Number(v)) ? Number(v).toLocaleString('pt-BR',{maximumFractionDigits:2}) : String(v);
const bytes = v => v === null || v === undefined ? 'indisponível' : (Number(v)/1048576).toFixed(1)+' MiB';
const utc = v => v ? new Date(v).toISOString().replace('T',' ').replace('Z',' UTC') : '—';
const age = v => v === null || v === undefined ? 'sem amostra' : Number(v)<60 ? fmt(v)+' s' : fmt(Number(v)/60)+' min';
function message(id,text,error=false){$(id).textContent=text;$(id).classList.toggle('error',error);}
function pretty(id,value){$(id).hidden=false;$(id).textContent=JSON.stringify(value,null,2);}
function button(text,action){const b=document.createElement('button');b.type='button';b.textContent=text;b.addEventListener('click',action);return b;}
function table(id,headers,rows){
  const t=$(id);t.replaceChildren();const head=t.createTHead().insertRow();
  headers.forEach(text=>{const th=document.createElement('th');th.scope='col';th.textContent=text;head.append(th);});
  const body=t.createTBody();
  if(!rows.length){const cell=body.insertRow().insertCell();cell.colSpan=headers.length;cell.textContent='Nenhum registro disponível nesta consulta.';}
  for(const cells of rows){const tr=body.insertRow();for(const value of cells){const td=tr.insertCell();if(value instanceof Node)td.append(value);else td.textContent=value ?? '—';}}
}
async function api(path,{method='GET',body,raw=false,binary=false}={}){
  if(!key && (!publicRead || !['GET','HEAD'].includes(method)))throw Error('Autorize as alterações com a chave da API.');
  if(!path.startsWith('/v1/'))throw Error('Rota fora da API local.');
  const controller=new AbortController();controllers.add(controller);
  const timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch(path,{method,headers:{...(key?{Authorization:'Bearer '+key}:{}),...(body?{'Content-Type':'application/json'}:{})},
      body:body?JSON.stringify(body):undefined,credentials:'omit',cache:'no-store',signal:controller.signal});
    const payload=binary?new Uint8Array(await response.arrayBuffer()):await response.text();
    const text=binary?null:payload;
    if(payload.length>16*1024*1024)throw Error('Resposta acima do limite do painel.');
    if(!response.ok){
      let reason;try{reason=JSON.parse(binary?new TextDecoder().decode(payload):text).detail;}catch{reason='Resposta indisponível';}
      const error=Error('HTTP '+response.status+' · '+(typeof reason==='string'?reason:JSON.stringify(reason)));error.status=response.status;throw error;
    }
    return binary?{bytes:payload,hash:response.headers.get('X-Content-SHA256')}:raw?{text,hash:response.headers.get('X-Content-SHA256')}:JSON.parse(text);
  }finally{clearTimeout(timeout);controllers.delete(controller);}
}
async function download(path,prefix){
  try{
    const result=await api(path,{raw:true});const raw=new TextEncoder().encode(result.text);
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',raw))).map(x=>x.toString(16).padStart(2,'0')).join('');
    if(hash!==result.hash)throw Error('Hash do artefato não confere.');
    const url=URL.createObjectURL(new Blob([raw],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download=prefix+'-'+hash+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    message('research-message','Artefato baixado com SHA-256 verificado.');
  }catch(e){message('research-message',e.message,true);}
}
function metric(title,value,note,warn=false){
  const div=document.createElement('article');div.className='metric'+(warn?' warn':'');
  for(const [tag,text,cls] of [['span',title,'label'],['strong',value,''],['span',note,'note']]){
    const el=document.createElement(tag);el.textContent=text;el.className=cls;div.append(el);
  }return div;
}
function resourceRow(name,r){
  if(!r || !r.available)return [name,'indisponível','indisponível','indisponível'];
  let cpu='amostrando';const before=previousResources.get(name);
  if(before && r.cpu_usage_usec!==null && before.cpu_usage_usec!==null){
    const dt=(Date.parse(r.sampled_at)-Date.parse(before.sampled_at))/1000;
    if(dt>0)cpu=fmt(Math.max(0,r.cpu_usage_usec-before.cpu_usage_usec)/1e6/dt*100)+'%';
  }
  previousResources.set(name,r);
  return [name,bytes(r.memory_bytes),bytes(r.memory_limit_bytes),cpu];
}
async function refreshOperations(){
  if(operationsBusy || (!key && !publicRead))return;
  operationsBusy=true;
  try{
    const s=await api('/v1/status'), c=s.collector||{}, p=s.pipeline||{}, tr=c.tracking||{};
    explorer.overview(s);
    lastOperation=new Date().toISOString();
    $('operation-stamp').textContent='Amostra operacional: '+utc(lastOperation)+' · atualização a cada 2 s enquanto esta área estiver visível.';
    $('metrics').replaceChildren(
      metric('Fonte GMGN',c.source_current?'Atual':c.cooldown_until>Date.now()/1000?'Em pausa':'Atrasada','Processo: '+(c.process_alive?'ativo':'indisponível')+' · heartbeat '+age(c.heartbeat_age_seconds),!c.source_current),
      metric('Cobertura individual',fmt(tr.fresh_accepted)+' / '+fmt(tr.active),'Info aceito dentro da frequência de cada token',!tr.current),
      metric('Fila de recepção',fmt(c.pending?.count),'Guardado no coletor: '+bytes(c.pending?.bytes),Number(c.pending?.count)>0),
      metric('Admissão',p.accepting?'Liberada':'Pausada','Livre no volume: '+bytes(p.storage?.free_bytes),!p.accepting));
    table('queues',['Destino','Pendentes','Idade mais antiga','Falhas'],[
      ['Coletor → API',fmt(c.pending?.count),age(c.pending?.oldest_seconds),fmt(c.pending?.retrying)],
      ...Object.entries(p.queues||{}).map(([n,q])=>[n,fmt(q.pending),age(q.oldest_seconds),fmt(q.errors)])]);
    const workerRows=(s.workers||[]).map(w=>({...w,details:typeof w.details==='string'?JSON.parse(w.details):w.details}));
    const worker=workerRows.find(w=>w.worker_name==='data-worker');
    table('resources',['Serviço','RAM atual','Limite de RAM','CPU · 100% = 1 núcleo'],[
      resourceRow('API',s.api_resources),resourceRow('Worker',worker?.details?.resources)]);
    table('sources',['Consulta','Estado','Última resposta','Intervalo entre slots','Tentativas','Falhas'],Object.entries(c.jobs||{}).map(([n,j])=>[
      n,state(j.state),age(j.success_age_seconds),age(j.cadence_seconds),fmt(j.attempts),fmt(j.failures)]));
    table('workers',['Etapa','Estado','Heartbeat'],workerRows.map(w=>[w.worker_name,state(w.state),utc(w.heartbeat_at)]));
    const registered=(s.token_registry?.counts||[]).filter(r=>r.source_kind==='gmgn'&&r.chain===c.chain).reduce((n,r)=>n+Number(r.count),0);
    $('tracking-summary').textContent=fmt(registered)+' cadastrados · '+fmt(tr.active)+' na agenda, sem teto de tokens · '+fmt(tr.pending_first)+' sem primeira consulta · '+fmt(tr.overdue)+' devidos · '+fmt(tr.pending_registry)+' aguardando registro. Alvo recente: '+age(tr.refresh_target_seconds)+'; fora das listas: '+age(tr.cold_refresh_seconds)+'. Atraso por quota permanece visível. Importação do cadastro: '+(tr.registry_sync?.state||'aguardando')+'.';
    $('quota-summary').textContent='Orçamento desta base: '+fmt(c.quota?.local_weight_per_second)+' pesos/s · quota compartilhada: '+(c.quota?.shared_gate?'conectada':'desconectada')+'. A lista abaixo mostra até 20 jobs devidos; não limita o universo.';
    table('family-coverage',['Família','Jobs','Sem primeira resposta','Aceitos recentes','Devidos'],(c.families?.coverage||[]).map(f=>[f.endpoint,fmt(f.jobs),fmt(f.pending_first),fmt(f.fresh_accepted),fmt(f.overdue)]));
    table('tracking',['Token','Estado','Recebido há'],(tr.items||[]).slice(0,20).map(t=>[
      button(t.token_address,()=>{$('token').value=t.token_address;showTab('trajectory');}),state(t.state),
      age(t.last_received_at?Date.now()/1000-t.last_received_at:null)]));
    const reader=s.trajectory_reader||{};
    $('reader-summary').textContent='Consultas em processamento/espera: '+fmt(reader.admitted)+' / '+fmt(reader.max_admitted)+' · cache: '+bytes(reader.cache_bytes)+' · respostas reutilizadas: '+fmt(reader.hits)+'.';
    table('families',['Família','Token','Recebido há','Evidência'],(c.families?.items||[]).map(f=>[
      f.endpoint,f.token_address,age(f.received_age_seconds),f.last_capture_id?button('Abrir',async()=>{
        try{const cutoff=(await api('/v1/clock')).knowledge_cutoff;
          const route=f.endpoint==='token_kline'?'/v1/candles/':'/v1/holders/';
          pretty('family-detail',await api(route+encodeURIComponent(f.last_capture_id)+'?'+new URLSearchParams({source_kind:f.source_kind,knowledge_cutoff:cutoff})));
          await explorer.openFamily(f.last_capture_id,f.endpoint,f.source_kind,cutoff);
        }catch(e){message('global-message',e.message,true);}
      }):'Aguardando primeira captura']));
    const failures={provider_cooldown_until:c.cooldown_until?utc(c.cooldown_until*1000):null,
      sources:Object.fromEntries(Object.entries(c.jobs||{}).filter(([,j])=>j.last_error).map(([n,j])=>[n,j.last_error])),
      recovery:c.recovery||null,delivery:c.delivery?.last_error||null,analytics:s.analytics_failures,
      stages:workerRows.filter(w=>w.details?.last_error).map(w=>({stage:w.worker_name,error:w.details.last_error}))};
    $('failures').textContent=JSON.stringify(failures,null,2);
    message('global-message','Conectado à base local. As consultas do painel não chamam a GMGN.');
  }catch(e){
    message('global-message',e.message,true);
    $('operation-stamp').textContent='Atualização falhou. Última amostra: '+utc(lastOperation)+'. Indicadores anteriores podem estar desatualizados.';
    $('metrics').querySelectorAll('.metric').forEach(x=>x.classList.add('warn'));
  }finally{operationsBusy=false;}
}
function svg(tag,attrs,text){
  const el=document.createElementNS('http://www.w3.org/2000/svg',tag);
  for(const [k,v] of Object.entries(attrs))el.setAttribute(k,String(v));
  if(text!==undefined)el.textContent=text;
  return el;
}
function drawCurve(series){
  const chart=$('curve');chart.replaceChildren();
  const valid=series.points.filter(p=>p.state==='available' && p.same_timestamp_count===1 && Number.isFinite(Number(p.value_text)));
  if(!valid.length){chart.append(svg('text',{x:50,y:150,class:'chart-label'},'Sem observações elegíveis no período.'));return;}
  const start=Date.parse(series.start),end=Date.parse(series.end);
  const values=valid.map(p=>Number(p.value_text)),low=Math.min(...values),high=Math.max(...values),pad=(high-low)*.12 || Math.max(1,high*.05);
  const yMin=Math.max(0,low-pad),yMax=high+pad;
  const x=at=>65+(Date.parse(at)-start)/(end-start)*905;
  const y=v=>275-(Number(v)-yMin)/(yMax-yMin)*240;
  for(const gap of series.gaps||[])chart.append(svg('rect',{x:x(gap.start),y:30,width:Math.max(0,x(gap.end)-x(gap.start)),height:245,class:'gap'}));
  for(let n=0;n<=4;n++){const value=yMin+(yMax-yMin)*n/4;chart.append(svg('line',{x1:65,y1:y(value),x2:975,y2:y(value),class:'axis'}),svg('text',{x:5,y:y(value)+4,class:'chart-label'},fmt(value)));}
  chart.append(svg('text',{x:65,y:304,class:'chart-label'},utc(series.start).slice(11,19)),svg('text',{x:885,y:304,class:'chart-label'},utc(series.end).slice(11,19)));
  let segments=[],segment=[];
  for(const p of series.points){
    if(p.state!=='available' || p.same_timestamp_count!==1 || !Number.isFinite(Number(p.value_text))){if(segment.length)segments.push(segment);segment=[];continue;}
    if(p.break_before && segment.length){segments.push(segment);segment=[];}
    segment.push(p);
  }
  if(segment.length)segments.push(segment);
  for(const part of segments)chart.append(svg('polyline',{points:part.map(p=>x(p.observed_at)+','+y(p.value_text)).join(' '),class:'curve-line'}));
  for(const p of valid){
    const circle=svg('circle',{cx:x(p.observed_at),cy:y(p.value_text),r:3,class:'curve-point',tabindex:0,role:'button','aria-label':utc(p.observed_at)+' · '+p.value_text+' USD'});
    circle.append(svg('title',{},utc(p.observed_at)+' · '+p.value_text+' USD'));
    const inspect=()=>{const d=$('point-detail');d.replaceChildren();
      const text=document.createElement('p');text.textContent=utc(p.observed_at)+' · '+p.value_text+' USD · disponível em '+utc(p.available_at);d.append(text);
      d.append(button('Ver captura e proveniência',async()=>{try{const data=await api('/v1/captures/'+encodeURIComponent(p.capture_id));const pre=document.createElement('pre');pre.textContent=JSON.stringify(data,null,2);d.append(pre);}catch(e){message('trajectory-message',e.message,true);}}));
    };
    circle.addEventListener('click',inspect);circle.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();inspect();}});chart.append(circle);
  }
}
async function loadHolders(params){
  const list=await api('/v1/holders?'+new URLSearchParams({...params,limit:20}));
  table('holders',['Recebida','Qualidade','Linhas'],list.items.map(h=>[button(utc(h.received_at),async()=>{
    try{pretty('holder-detail',await api('/v1/holders/'+h.capture_id+'?'+new URLSearchParams({source_kind:params.source_kind,knowledge_cutoff:params.knowledge_cutoff})));}catch(e){message('trajectory-message',e.message,true);}
  }),state(h.state),fmt(h.accepted_rows)+' / '+fmt(h.returned_rows)]));
  if(list.has_more){const row=$('holders').tBodies[0].insertRow(),cell=row.insertCell();cell.colSpan=3;cell.append(button('Próxima página de snapshots',()=>loadHolders({...params,after_seq:list.next_after_seq}).catch(e=>message('trajectory-message',e.message,true))));}
}
async function loadPositions(before){
  const p=await api('/v1/paper/positions?'+new URLSearchParams({limit:20,...(before?{before_number:before}:{})}));
  table('positions',['Posição','Estado','Regra'],p.items.map(v=>[button(String(v.position_number),async()=>{
    try{pretty('decision-detail',await api('/v1/paper/positions/'+v.position_id+'/events?limit=100'));}catch(e){message('trajectory-message',e.message,true);}
  }),state(v.status),v.rule_id]));
  positionCursor=p.next_before_number;$('more-positions').hidden=!p.has_more;
}
async function trajectory(event){
  event?.preventDefault();if(trajectoryBusy)return;trajectoryBusy=true;
  const submit=$('trajectory-form').querySelector('[type=submit]');submit.disabled=true;
  try{
    message('trajectory-message','Consultando evidência persistida…');
    if(!$('cutoff').value)$('cutoff').value=(await api('/v1/clock')).knowledge_cutoff;
    const cutoff=$('cutoff').value,start=$('day').value+'T00:00:00Z';
    const stop=Math.min(Date.parse(start)+86400000,Date.parse(cutoff));
    if(!Number.isFinite(stop)||stop<=Date.parse(start))throw Error('O dia precisa começar antes do corte UTC.');
    const common={source_kind:$('source').value,chain:'sol',token:$('token').value.trim(),start,end:new Date(stop).toISOString(),knowledge_cutoff:cutoff};
    const series=await api('/v1/trajectory/crossings?'+new URLSearchParams({...common,method:$('method').value,max_points:2000,max_events:1000}));
    drawCurve(series);
    $('curve-count').textContent=fmt(series.coverage.eligible_points)+' pontos elegíveis · '+fmt(series.gaps.length)+' lacunas';
    $('curve-summary').textContent=series.method+' · corte '+utc(cutoff)+' · números arredondados apenas no desenho; o detalhe preserva o valor original.';
    $('point-detail').replaceChildren();
    table('crossings',['Movimento','Nível USD','Intervalo UTC'],series.crossings.events.map(e=>[state(e.kind),e.level_text ?? e.level_usd ?? e.level,
      (e.interval_start?utc(e.interval_start):utc(e.from_at ?? e.start_at))+' → '+(e.interval_end?utc(e.interval_end):utc(e.to_at ?? e.end_at))]));
    await loadHolders(common);
    await loadPositions();
    message('trajectory-message','Consulta completa. As posições paper mostram o estado atual da base, fora do corte histórico da curva.');
  }catch(e){message('trajectory-message',e.message,true);}
  finally{trajectoryBusy=false;submit.disabled=false;}
}
async function loadStrategies(offset=0){
  const request=++strategyRequest;
  try{
    const params={offset,limit:20,query:$('strategy-query').value.trim()};
    if($('strategy-source').value)params.source=$('strategy-source').value;
    if($('strategy-target').value)params.first_target=$('strategy-target').value;
    const result=await api('/v1/research/strategies?'+new URLSearchParams(params));
    if(request!==strategyRequest)return;
    strategyOffset=offset;
    table('legacy-strategies',['Estratégia / run','Horizonte / idade','Primeira parcial','Stop loss','Calibração','Definição'],result.items.map(r=>[
      r.label+' · '+r.pair_id+' · run '+r.run_id.slice(0,8),age(r.horizon_seconds)+' / T+'+r.target_age_seconds+'s',
      '+'+fmt(r.first_target_gain_percent)+'% → vende '+fmt(r.first_sell_percent)+'%',r.stop_loss_percent===null?'Sem SL':fmt(r.stop_loss_percent)+'%',
      r.calibration==='complete'?'Preservada':'Incompleta no arquivo',button('Ver estratégia',()=>inspectStrategy(r.strategy_id))]));
    $('strategy-count').textContent=result.total?(offset+1)+'–'+Math.min(offset+20,result.total)+' de '+result.total+' definições · '+result.catalog_total+' no catálogo.':'Nenhuma estratégia corresponde ao filtro.';
    $('strategy-prev').disabled=offset===0;$('strategy-next').disabled=!result.has_more;
  }catch(e){if(request===strategyRequest)message('strategy-count',e.message,true);}
}
async function inspectStrategy(id){
  try{
    const r=await api('/v1/research/strategies/'+encodeURIComponent(id)),d=r.definition,e=d.entry_rule,x=d.exit_policy;
    selectedStrategyId=id;$('strategy-detail').hidden=false;$('strategy-title').textContent=r.label;
    $('strategy-identity').textContent='Robinhood · run '+d.run_id+' · par '+d.pair_id+' · horizonte '+age(d.horizon_seconds)+' · idade T+'+d.target_age_seconds+'s';
    message('strategy-availability','Importada para consulta. Adaptação ao comparador pendente · calibração '+(r.compatibility.calibration==='complete'?'preservada.':'incompleta no arquivo de origem.'));
    $('strategy-blockers').replaceChildren(...r.compatibility.reasons.map(reason=>{const li=document.createElement('li');li.textContent=reason.message;return li;}));
    $('strategy-entry-summary').textContent='Corte do score: '+e.score_cutoff+'. Direção e peso compõem o score; não são limites individuais. Normalização: '+(e.normalization||'não preservada neste arquivo')+'.';
    table('strategy-conditions',['Critério','Direção','Peso','Média de treino','Desvio de treino','Transformação'],e.conditions.map(c=>[
      (c.label||c.key)+' ('+c.key+')',c.direction===1?'Maior favorece':'Menor favorece',String(c.weight),c.mean===undefined?'Ausente':String(c.mean),c.std===undefined?'Ausente':String(c.std),c.transform||'Ausente']));
    table('strategy-exits',['Parcial','Ganho alvo','Venda da posição inicial'],x.targets_gain_percent.map((target,i)=>[i+1,'+'+fmt(target)+'%',fmt(x.sell_percent[i])+'%']));
    $('strategy-exit-summary').textContent='Stop loss: '+(x.stop_loss_percent===null?'sem SL':fmt(x.stop_loss_percent)+'%')+' sobre o saldo restante. Moonbag: '+fmt(x.moonbag_percent)+'%. Saldo encerrado na primeira cotação observável a partir de '+age(d.horizon_seconds)+'. Não há garantia de fill.';
    $('strategy-provenance').textContent='Fonte: '+r.source.path+' · snapshot '+utc(r.historical_evidence.snapshot_at)+' · SHA-256 '+r.source.sha256;
    const historicalRows=[];
    function historyFields(value,prefix=''){for(const [key,val] of Object.entries(value||{})){const path=prefix?prefix+'.'+key:key;if(val&&typeof val==='object'&&!Array.isArray(val))historyFields(val,path);else historicalRows.push([path,val===null?'Desconhecido':typeof val==='object'?JSON.stringify(val):String(val)]);}}
    historyFields(r.historical_evidence.metrics);table('strategy-history',['Métrica histórica','Valor preservado'],historicalRows);
    $('strategy-json').textContent=JSON.stringify(r,null,2);
    $('strategy-detail').scrollIntoView({behavior:'smooth',block:'start'});
  }catch(e){message('research-message',e.message,true);}
}
async function refreshResearch(){
  if(researchBusy || (!key && !publicRead))return;researchBusy=true;
  try{
    await loadStrategies(strategyOffset);
    const jobs=await api('/v1/research/jobs');
    table('research-jobs',['Coorte','Estado','Membros','Ações'],jobs.items.map(j=>{
      const actions=document.createElement('span');actions.append(button('Detalhes',()=>researchViews.openCohort(j)));
      if(j.state==='ready')actions.append(button('Manifesto',()=>download('/v1/research/jobs/'+j.job_id+'/bundle','coorte')));
      return [j.job_id.slice(0,12),state(j.state),fmt(j.completed_count)+' / '+fmt(j.member_count),actions];
    }));
    const runs=await api('/v1/research/experiments');
    table('experiments',['Experimento','Estado','Tentativas','Ações'],runs.items.map(r=>{
      const actions=document.createElement('span');actions.append(button('Detalhes',async()=>{
        try{if(r.state==='ready')researchViews.openExperiment(await api('/v1/research/experiments/'+r.experiment_id+'/result'));else pretty('experiment-detail',r);}catch(e){message('research-message',e.message,true);}
      }));
      if(r.state==='ready')actions.append(button('Resultado',()=>download('/v1/research/experiments/'+r.experiment_id+'/result','experimento')));
      return [r.experiment_id.slice(0,12),state(r.state),fmt(r.attempts),actions];
    }));
    message('research-message','Registros atualizados. Falhas e casos sem desfecho permanecem visíveis.');
  }catch(e){message('research-message',e.message,true);}
  finally{researchBusy=false;}
}
function showTab(name,refresh=true){
  if(!['operations','tokens','data','trajectory','research','paper'].includes(name))return;
  activeTab=name;history.replaceState(null,'','#'+name);
  for(const page of document.querySelectorAll('.page'))page.hidden=page.id!==name;
  for(const tab of document.querySelectorAll('.tab')){const selected=tab.dataset.tab===name;tab.classList.toggle('active',selected);if(selected)tab.setAttribute('aria-current','page');else tab.removeAttribute('aria-current');}
  if(refresh){if(name==='research')refreshResearch();if(name==='operations')refreshOperations();explorer.refresh(name);}
}
function updateAccess(){
  const connected=Boolean(key)||publicRead;
  $('connect-form').hidden=connected;
  $('disconnect').hidden=!key;
  $('authorize-writes').hidden=!publicRead||Boolean(key);
  $('connection-state').textContent=key?'Conectado':publicRead?'Consulta liberada':'Desconectado';
  $('connection-state').classList.toggle('ok',connected);
  $('write-access-note').hidden=!publicRead||Boolean(key);
  for(const el of document.querySelectorAll('#cohort-form input,#cohort-form textarea,#cohort-form button,#experiment-form input,#experiment-form textarea,#experiment-form button'))el.disabled=!key;
  if(publicRead){
    document.querySelector('label[for="api-key"]').textContent='Chave para autorizar alterações';
    $('api-key').placeholder='Somente para criar coortes ou experimentos';
  }
}
$('authorize-writes').addEventListener('click',()=>{$('connect-form').hidden=!$('connect-form').hidden;if(!$('connect-form').hidden)$('api-key').focus();});
$('connect-form').addEventListener('submit',async e=>{
  e.preventDefault();key=$('api-key').value.trim();
  try{
    // A protected endpoint validates the key even when reads are public.
    const auth=await fetch('/openapi.json',{headers:{Authorization:'Bearer '+key},credentials:'omit',cache:'no-store'});
    if(!auth.ok)throw Error('Chave inválida.');
    $('api-key').value='';updateAccess();
    $('connection-state').textContent='Conectado';$('connection-state').classList.add('ok');await refreshOperations();if(activeTab!=='operations')showTab(activeTab);
  }catch(error){key='';updateAccess();$('connect-form').hidden=false;message('global-message',error.message,true);}
});
$('disconnect').addEventListener('click',()=>{
  key='';for(const c of controllers)c.abort();updateAccess();
  message('global-message',publicRead?'Consulta local liberada.':'Sessão encerrada. Resultados já exibidos são amostras anteriores.');
});
for(const tab of document.querySelectorAll('.tab'))tab.addEventListener('click',()=>showTab(tab.dataset.tab));
$('day').value=new Date().toISOString().slice(0,10);
$('refresh-operations').addEventListener('click',refreshOperations);
$('refresh-research').addEventListener('click',refreshResearch);
$('trajectory-form').addEventListener('submit',trajectory);
$('more-positions').addEventListener('click',()=>loadPositions(positionCursor).catch(e=>message('trajectory-message',e.message,true)));
$('new-cutoff').addEventListener('click',async()=>{try{$('cutoff').value=(await api('/v1/clock')).knowledge_cutoff;message('trajectory-message','Novo corte definido. Consulte para atualizar a curva.');}catch(e){message('trajectory-message',e.message,true);}});
$('cohort-form').addEventListener('submit',async e=>{
  e.preventDefault();try{const r=await api('/v1/research/jobs',{method:'POST',body:JSON.parse($('cohort-spec').value)});await refreshResearch();message('research-message','Coorte '+r.job_id+' · '+state(r.state));}catch(error){message('research-message',error.message,true);}
});
$('experiment-form').addEventListener('submit',async e=>{
  e.preventDefault();try{const r=await api('/v1/research/jobs/'+encodeURIComponent($('dataset-id').value)+'/experiments',{method:'POST',body:JSON.parse($('experiment-spec').value)});await refreshResearch();message('research-message','Experimento '+r.experiment_id+' · '+state(r.state));}catch(error){message('research-message',error.message,true);}
});
setInterval(()=>{if((key || publicRead) && !document.hidden && activeTab==='operations')refreshOperations();},2000);

$('strategy-filter').addEventListener('submit',e=>{e.preventDefault();loadStrategies(0);});
$('strategy-prev').addEventListener('click',()=>loadStrategies(Math.max(0,strategyOffset-20)));
$('strategy-next').addEventListener('click',()=>loadStrategies(strategyOffset+20));
$('download-strategies').addEventListener('click',()=>download('/v1/research/strategies/export','estrategias-anteriores'));
$('download-strategy').addEventListener('click',()=>{if(selectedStrategyId)download('/v1/research/strategies/'+selectedStrategyId+'/export','estrategia');});

const explorer=installExplorer({$,api,table,button,pretty,fmt,utc,metric,message,showTab,svg});
const researchViews=installResearchViews({$,api,table,button,pretty,fmt,utc,metric,message,explorer});
const initialTab=location.hash.slice(1);if(initialTab)showTab(initialTab,false);

async function initializeAccess(){
  try{
    const response=await fetch('/ui/config',{credentials:'omit',cache:'no-store'});
    if(!response.ok)throw Error('Não foi possível verificar o acesso local. Recarregue a página.');
    publicRead=(await response.json()).public_read===true;updateAccess();
    if(publicRead){message('global-message','Consulta local liberada.');showTab(activeTab);}
    else message('global-message','Conecte para consultar a base.');
  }catch(error){message('global-message',error.message,true);}
}
initializeAccess();
