// Read-only data explorers. All paging stays in the local authenticated API.
export function installExplorer(h){
 const {$,api,table,button,pretty,fmt,utc,metric,message,showTab,svg}=h;
 let captureRequest=0;const versions=new Map();let tokenCutoff=null,captureCutoff=null,selectedToken=null,selectedCapture=null,selectedCaptureCutoff=null;
 const enc=v=>encodeURIComponent(v),qs=v=>new URLSearchParams(v).toString();
 const safe=(fn,id='data-message')=>(...args)=>{args[0]?.preventDefault?.();return Promise.resolve().then(()=>fn(...args)).catch(e=>message(id,e.message,true));};
 function actions(...nodes){const span=document.createElement('span');span.className='actions';span.append(...nodes);return span;}
 function pageLocal(id,rows,render,page=0,size=25){
  const start=page*size;render(rows.slice(start,start+size));const controls=$(id);controls.replaceChildren();
  const prev=button('Anterior',()=>pageLocal(id,rows,render,page-1,size));prev.disabled=page===0;
  const next=button('Próxima',()=>pageLocal(id,rows,render,page+1,size));next.disabled=start+size>=rows.length;
  const count=document.createElement('span');count.className='subtle';count.textContent=rows.length?`${start+1}–${Math.min(start+size,rows.length)} de ${rows.length}`:'Sem registros';controls.append(prev,count,next);
 }
 async function remotePage(id,path,base,render,nextOf,messageId='data-message'){
  const version=(versions.get(id)||0)+1;versions.set(id,version);const history=[];
  async function load(cursor={}){
   const r=await api(path+'?'+qs({...base,...cursor}));if(versions.get(id)!==version)return;
   render(r);const controls=$(id);controls.replaceChildren();
   const prev=button('Anterior',safe(()=>load(history.pop()),messageId));prev.disabled=!history.length;
   const next=button('Próxima',safe(()=>{history.push(cursor);return load(nextOf(r));},messageId));next.disabled=!nextOf(r);
   const count=document.createElement('span');count.className='subtle';count.textContent='Página '+(history.length+1)+' · '+(r.items||[]).length+' registros'+(nextOf(r)?' · há mais':' · fim');controls.append(prev,count,next);
  }
  await load();
 }
 const usageName={metric:'Métrica com contrato',metadata:'Metadado',blocked:'Cálculo bloqueado',structure:'Estrutura'};
 async function loadDictionary(){
  const base={limit:50,query:$('dictionary-query').value};
  for(const key of ['endpoint','category','usage'])if($('dictionary-'+key).value)base[key]=$('dictionary-'+key).value;
  await remotePage('dictionary-pages','/v1/dictionary',base,r=>{
   if($('dictionary-category').options.length===1)for(const category of r.categories){const o=document.createElement('option');o.value=category;o.textContent=category;$('dictionary-category').append(o);}
   const totals=r.summary.reduce((a,f)=>({paths:a.paths+f.catalogued_paths,observed:a.observed+f.observed_paths,metric:a.metric+(f.by_usage.metric||0),blocked:a.blocked+(f.by_usage.blocked||0)}),{paths:0,observed:0,metric:0,blocked:0});
   $('dictionary-metrics').replaceChildren(metric('Caminhos catalogados',fmt(totals.paths),fmt(totals.observed)+' observados na amostra'),metric('Métricas com contrato',fmt(totals.metric),'Cada ocorrência exige validação'),metric('Cálculo bloqueado',fmt(totals.blocked),'Valores originais preservados',true));
   $('dictionary-count').textContent=r.total+' caminhos neste filtro · base: '+r.baseline.captures+' capturas em '+utc(r.baseline.at)+'. Metadados e estruturas não contam como métricas.';
   table('dictionary-fields',['Fonte / caminho','Categoria','Significado','Unidade / janela','Uso','Contrato'],r.items.map(f=>[f.endpoint+' · '+(f.path||'(raiz)'),f.category,f.meaning,(f.unit||'Não homologada / não aplicável')+' · '+f.window,usageName[f.usage],button('Detalhes',()=>pretty('dictionary-detail',f))]));
  },r=>r.has_more?{offset:r.offset+r.limit}:null,'dictionary-message');
 }
 async function refreshDictionary(){
  await loadDictionary();
  try{const r=await api('/v1/dictionary/coverage');
   $('dictionary-stamp').textContent='Conferência das últimas capturas: '+utc(r.sampled_at)+' · cache de até '+r.cache_ttl_seconds+' s. Esta amostra não certifica todo o histórico. Novos campos e mudanças de tipo ficam bloqueados.';
   table('dictionary-coverage',['Fonte','Recebida UTC','Captura','Novos caminhos','Mudanças de tipo','Ausentes / inválidos','Ver evidência'],r.items.map(f=>[f.endpoint,utc(f.received_at),f.normalization_state||f.state,f.quality?.uncatalogued_path_count??'—',f.quality?.type_drift_path_count??'—',f.quality?(f.quality.qualities.missing||0)+' / '+(f.quality.qualities.invalid||0):f.reason||'Sem captura',f.capture_id?button('Ver captura',safe(()=>openCapture(f.capture_id,'gmgn',r.sampled_at),'dictionary-message')):'—']));
  }catch(e){message('dictionary-message',e.message,true);}
 }
 async function refreshTokens(){
  tokenCutoff=(await api('/v1/clock')).knowledge_cutoff;
  const base={chain:$('tokens-chain').value.trim(),source_kind:$('tokens-source').value,knowledge_cutoff:tokenCutoff,limit:50};
  if($('tokens-address').value.trim())base.token=$('tokens-address').value.trim();
  await remotePage('token-pages','/v1/tokens',base,r=>{
   table('token-list',['Token / registro','Primeira inscrição UTC','Fonte de descoberta','Ações'],r.items.map(t=>[t.token_address+' · #'+t.registry_id,utc(t.registered_at),t.endpoint,button('Ver dados',safe(()=>openToken(t,r.knowledge_cutoff),'tokens-message'))]));
   message('tokens-message','Corte '+utc(r.knowledge_cutoff)+' · registro iniciado em '+utc(r.registry_started_at)+'. Inscrição não é criação on-chain.');
  },r=>r.has_more?{after_registry_id:r.next_after_registry_id}:null,'tokens-message');
 }
 function factsRows(rows,detailId,cutoff){return rows.map(r=>[r.metric,r.value_text??'Ausente',r.unit||'Não definida',r.quality,utc(r.occurred_at),actions(
  button('Evidência',safe(async()=>pretty(detailId,await api('/v1/observations/'+enc(r.fact_id)+'/context?'+qs({source_kind:r.source_kind,knowledge_cutoff:cutoff}))),detailId==='token-fact-detail'?'tokens-message':'data-message')),
  button('Captura',safe(()=>openCapture(r.capture_id,r.source_kind,cutoff),'data-message')))]);}
 async function openToken(t,cutoff){
  selectedToken={...t,cutoff};$('token-view').hidden=false;$('token-view-title').textContent=t.token_address;
  $('token-clock').textContent='Conhecido até '+utc(cutoff)+' · '+t.source_kind+' / '+t.chain;
  pretty('token-registry-detail',t);const version=t.token_address+'|'+cutoff;
  const r=await api('/v1/as-known?'+qs({source_kind:t.source_kind,chain:t.chain,token:t.token_address,event_at:cutoff,knowledge_cutoff:cutoff,include_unverified:true}));
  if(selectedToken.token_address+'|'+selectedToken.cutoff!==version)return;
  table('token-facts',['Métrica','Valor exato','Unidade','Qualidade','Observado UTC','Detalhes'],factsRows(r.items,'token-fact-detail',r.knowledge_cutoff));
  $('token-view').scrollIntoView({block:'start',behavior:'smooth'});
 }
 async function observations(){
  if(!selectedToken)throw Error('Selecione um token.');const t=selectedToken;
  const base={source_kind:t.source_kind,chain:t.chain,token:t.token_address,knowledge_cutoff:t.cutoff,metric:$('observations-metric').value.trim(),start:$('observations-start').value,end:$('observations-end').value,limit:100,include_unverified:true};
  await remotePage('observation-pages','/v1/observations',base,r=>table('observation-list',['Métrica','Valor exato','Unidade','Qualidade','Observado UTC','Detalhes'],factsRows(r.items,'token-fact-detail',r.knowledge_cutoff)),r=>r.next_after_seq?{after_seq:r.next_after_seq}:null,'tokens-message');
 }
 async function refreshCaptures(reset=false){
  captureCutoff=(await api('/v1/clock')).knowledge_cutoff;
  if(reset||!$('capture-start').value){$('capture-start').value=new Date(Date.parse(captureCutoff)-86400000).toISOString();$('capture-end').value=captureCutoff;}
  const base={source_kind:$('capture-source').value,knowledge_cutoff:captureCutoff,start:$('capture-start').value,end:$('capture-end').value,limit:50};
  for(const [key,id] of [['chain','capture-chain'],['endpoint','capture-endpoint'],['state','capture-state']])if($(id).value.trim())base[key]=$(id).value.trim();
  await remotePage('capture-pages','/v1/explorer/captures',base,r=>{
   table('capture-list',['Recebida UTC','Família / cadeia','Estado atual','Captura','Evidência'],r.items.map(c=>[utc(c.received_at),c.endpoint+' / '+c.chain,c.normalization_state,button(c.capture_id,safe(()=>openCapture(c.capture_id,c.source_kind,r.knowledge_cutoff))),c.evidence_kind]));
   message('data-message','Corte '+utc(r.knowledge_cutoff)+'. Até sete dias por intervalo; dados mais antigos continuam acessíveis mudando o período.');
  },r=>r.next_cursor);
 }
 async function openCapture(id,source,cutoff){
  const request=++captureRequest;for(const id of ['raw-field-pages','capture-fact-pages'])versions.set(id,(versions.get(id)||0)+1);
  showTab('data',false);const c=await api('/v1/captures/'+enc(id));if(request!==captureRequest)return;selectedCapture=c;selectedCaptureCutoff=cutoff||(await api('/v1/clock')).knowledge_cutoff;
  $('capture-view').hidden=false;$('capture-title').textContent=c.endpoint+' · '+c.capture_id;
  $('capture-provenance').textContent=c.source_kind+' / '+c.chain+' · recebido '+utc(c.received_at)+' · SHA-256 '+c.raw_sha256;
  pretty('capture-metadata',c);$('raw-fields-query').value='';$('capture-family').hidden=!['token_top_holders','token_kline'].includes(c.endpoint);
  await loadFields();if(request!==captureRequest)return;
  await remotePage('capture-fact-pages','/v1/explorer/captures/'+enc(id)+'/facts',{source_kind:source,knowledge_cutoff:selectedCaptureCutoff,limit:100},r=>{
   table('capture-facts',['Token','Métrica / caminho','Valor exato','Unidade','Qualidade','Detalhes'],r.items.map(f=>[f.token_address,f.metric+' · '+f.pointer,f.value_text??'Ausente',f.unit||'Não definida',f.quality,button('Contrato',()=>pretty('capture-fact-detail',f))]));
  },r=>r.has_more?{after_seq:r.next_after_seq}:null);
  if(request!==captureRequest)return;const cat=await api('/v1/catalog?'+qs({source_kind:source,capture_id:id,limit:1000}));if(request!==captureRequest)return;
  table('field-catalog',['Caminho','Categoria','Tipo','Qualidade','Contrato'],cat.items.map(f=>[f.pointer,f.category,f.value_type,f.quality,f.contract_id]));
  $('catalog-scope').textContent=cat.items.length+' grupos · interpretação atual, fora do corte histórico'+(cat.has_more?' · resumo truncado; a resposta original e suas páginas de campos conservam o restante.':'.');
  $('capture-view').scrollIntoView({block:'start',behavior:'smooth'});
 }
 async function loadFields(){
  if(!selectedCapture)return;const c=selectedCapture;
  try{await remotePage('raw-field-pages','/v1/explorer/captures/'+enc(c.capture_id)+'/fields',{source_kind:c.source_kind,knowledge_cutoff:selectedCaptureCutoff,query:$('raw-fields-query').value,limit:100},r=>{
   table('raw-fields',['Caminho JSON','Tipo original','Valor original','Categoria / qualidade','Cálculo','Contrato'],r.items.map(f=>{const value=document.createElement('span');value.className='raw-value';value.textContent=f.value_text;const a=f.interpretation||{};return[f.pointer||'(raiz)',f.value_type,value,(a.category||'—')+' · '+(a.quality||'—'),a.calculation_allowed?'Elegível neste contrato':'Bloqueado / descritivo',button('Ver contrato',()=>pretty('raw-field-contract',a))];}));
   const q=r.quality_summary;$('raw-fields-count').textContent=r.total+' campos neste filtro. '+(q?q.uncatalogued_path_count+' caminhos novos; '+q.type_drift_path_count+' mudanças de tipo na resposta inteira. ':'')+'Valores exatos; interpretação do dicionário atual sobre o adapter publicado até o corte.';
  },r=>r.has_more?{offset:r.offset+r.limit}:null);}
  catch(e){table('raw-fields',['Caminho','Tipo','Valor'],[]);$('raw-field-pages').replaceChildren();message('raw-fields-count',e.message+' · use o download original para ver a evidência preservada.',true);}
 }
 async function downloadCapture(){
  if(!selectedCapture)return;const id=selectedCapture.capture_id,r=await api('/v1/captures/'+enc(id)+'/raw',{binary:true});
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',r.bytes))).map(v=>v.toString(16).padStart(2,'0')).join('');
  if(hash!==r.hash)throw Error('SHA-256 do bruto não confere.');
  const url=URL.createObjectURL(new Blob([r.bytes],{type:'application/octet-stream'}));const a=document.createElement('a');a.href=url;a.download='capture-'+id+'.raw';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  message('data-message','Resposta original baixada, byte a byte; SHA-256 verificado.');
 }
 function drawCandles(r){
  const chart=$('candle-curve');chart.replaceChildren();chart.hidden=false;const good=r.items.filter(c=>c.state==='available');
  if(!good.length){chart.append(svg('text',{x:30,y:120,class:'chart-label'},'Sem barras elegíveis.'));return;}
  const low=Math.min(...good.map(c=>Number(c.low))),high=Math.max(...good.map(c=>Number(c.high))),span=high-low||high*.1||1;
  const x=t=>60+(t-r.request_from_ms)/(r.request_to_ms-r.request_from_ms)*910,y=v=>240-(Number(v)-low)/span*210;
  const step=(good[0].closed_at?Date.parse(good[0].closed_at)-good[0].time_ms:60000),width=Math.max(1,Math.min(12,step/(r.request_to_ms-r.request_from_ms)*600));
  for(let i=0;i<=4;i++){const v=low+span*i/4;chart.append(svg('line',{x1:60,x2:980,y1:y(v),y2:y(v),class:'axis'}),svg('text',{x:2,y:y(v)+4,class:'chart-label'},v.toPrecision(3)));}
  for(const c of good){const color=Number(c.close)>=Number(c.open)?'#75dfb1':'#f58c8c';const line=svg('line',{x1:x(c.time_ms),x2:x(c.time_ms),y1:y(c.high),y2:y(c.low),stroke:color});line.append(svg('title',{},utc(c.time_ms)+' O '+c.open+' H '+c.high+' L '+c.low+' C '+c.close));chart.append(line,svg('rect',{x:x(c.time_ms)-width/2,y:Math.min(y(c.open),y(c.close)),width,height:Math.max(1,Math.abs(y(c.open)-y(c.close))),fill:color}));}
 }
 async function openFamily(id,endpoint,source,cutoff){
  showTab('data',false);const at=cutoff||(await api('/v1/clock')).knowledge_cutoff;
  const route=endpoint==='token_kline'?'/v1/candles/':'/v1/holders/';const r=await api(route+enc(id)+'?'+qs({source_kind:source,knowledge_cutoff:at}));
  $('family-view').hidden=false;$('family-title').textContent=(endpoint==='token_kline'?'OHLCV':'Holders')+' · '+id;pretty('family-json',r);
  if(endpoint==='token_kline'){
   $('family-coverage').textContent=r.coverage.eligible_rows+' barras elegíveis / '+r.coverage.returned_rows+' recebidas; '+r.coverage.missing_bar_times_ms.length+' lacunas. Preço: '+r.price_unit+'; volume: '+r.volume_unit+'. Valores arredondados apenas no gráfico.';
   drawCandles(r);pageLocal('family-pages',r.items,rows=>table('family-rows',['Início UTC','Open','High','Low','Close','Volume','Estado'],rows.map(c=>[c.time_ms===null?'Ausente':utc(c.time_ms),c.open,c.high,c.low,c.close,c.volume,c.reason||c.state])));
  }else{
   $('candle-curve').hidden=true;$('family-coverage').textContent='Snapshot publicado · '+r.run.state+' · '+r.items.length+' carteiras nesta página. Lista do provedor não representa todos os holders.';
   const render=rows=>table('family-rows',['Carteira','Saldo','Fração reportada do supply','Qualidade','Todos os campos'],rows.map(w=>[w.wallet_address,w.values.balance?.value_text??w.values.amount_cur?.value_text,w.values.amount_percentage?.value_text,w.identity_quality,button('Detalhes',()=>pretty('family-json',w))]));
   render(r.items);$('family-pages').replaceChildren();if(r.has_more)$('family-pages').append(button('Próxima página',safe(async()=>{const n=await api(route+enc(id)+'?'+qs({source_kind:source,knowledge_cutoff:at,after_ordinal:r.next_after_ordinal}));render(n.items);})));
  }
  $('family-view').scrollIntoView({block:'start',behavior:'smooth'});
 }
 async function paperEvents(path,nextKey){
  $('paper-event-view').hidden=false;await remotePage('paper-event-pages',path,{limit:50},r=>table('paper-events',['Revisão / sequência','Tipo','Tempo','Conteúdo'],r.items.map(e=>[e.revision??e.sequence,e.kind??e.event??e.disposition??'Evento',utc(e.at??e.recorded_at),button('Ver',safe(async()=>{pretty('paper-detail',e);if(e.frame_key)pretty('paper-detail',{decision:e,frame:await api('/v1/rule-frames/'+enc(e.frame_key))});},'paper-message'))])),r=>r.has_more?{[nextKey]:r['next_'+nextKey]}:null,'paper-message');
 }
 async function refreshPaper(){
  const tasks=[['account','/v1/paper/account'],['rules','/v1/rules/active'],['bindings','/v1/paper/bindings'],['windows','/v1/feature-windows'],['tracking','/v1/paper/tracking']];const values={},failures=[];
  for(const [key,path] of tasks){try{values[key]=await api(path);}catch(e){if(key==='account'&&e.status===404)values.account=null;else failures.push(key+': '+e.message);}}
  pretty('paper-account-state',values.account||{state:'not_initialized',meaning:'Conta paper ainda não inicializada. Nenhum saldo foi presumido.'});
  const account=values.account;$('paper-metrics').replaceChildren(metric('Caixa paper',account?fmt(account.state.cash_usd):'Indisponível','USD simulado · exato no detalhe'),metric('Disponível',account?fmt(account.balances.available_usd):'Indisponível','USD após reservas · arredondado'),metric('P&L realizado',account?fmt(account.state.realized_pnl_usd):'Indisponível','USD; sem marcação a mercado'),metric('Regras',fmt(values.rules?.items?.length),'Registradas nesta base'));
  table('paper-rules',['Regra','Estado','Decisões'],(values.rules?.items||[]).map(r=>[button(r.rule_id,()=>{$('paper-event-view').hidden=false;pretty('paper-detail',r);}),r.enabled?'Habilitada':'Pausada',button('Histórico',safe(()=>paperEvents('/v1/rules/active/'+enc(r.rule_id)+'/evaluations','before_sequence'),'paper-message'))]));
  table('paper-bindings',['Regra vinculada','Estado','Eventos'],(values.bindings?.items||[]).map(r=>[button(r.rule_id,()=>{$('paper-event-view').hidden=false;pretty('paper-detail',r);}),r.enabled?'Habilitado':'Pausado',button('Histórico',safe(()=>paperEvents('/v1/paper/bindings/'+enc(r.rule_id)+'/events','before_revision'),'paper-message'))]));
  table('paper-windows',['Janela','Estado','Próxima consulta','Detalhes'],(values.windows?.items||[]).map(w=>[button(w.window_id,()=>pretty('paper-tracking',w)),w.state,utc(w.next_due_at),button('Ver frame',safe(async()=>pretty('paper-tracking',await api('/v1/feature-windows/'+enc(w.window_id))),'paper-message'))]));pretty('paper-tracking',values.tracking||{});
  await remotePage('paper-account-pages','/v1/paper/account/events',{limit:50},r=>table('paper-account-events',['Revisão','Tipo','Detalhes'],r.items.map(e=>[e.revision,e.kind??e.event,button('Ver',()=>pretty('paper-account-state',e))])),r=>r.has_more?{before_revision:r.next_before_revision}:null,'paper-message');
  await remotePage('paper-position-pages','/v1/paper/positions',{limit:50},r=>table('paper-positions',['Posição','Estado','Token / regra','Detalhes'],r.items.map(p=>[p.position_number,p.status,p.definition?.token_address||p.definition?.token||p.rule_id,actions(button('Posição',()=>{$('paper-event-view').hidden=false;pretty('paper-detail',p);}),button('Eventos',safe(()=>paperEvents('/v1/paper/positions/'+enc(p.position_id)+'/events','before_revision'),'paper-message')))])),r=>r.has_more?{before_number:r.next_before_number}:null,'paper-message');
  message('paper-message',failures.length?failures.join(' · '):'Estado atual consultado. Conta ausente e listas vazias não significam retorno zero.',!!failures.length);
 }
 function overview(s){
  const captures=s.captures||[],counts=s.token_registry?.counts||[];
  $('data-metrics').replaceChildren(metric('Tokens registrados',fmt(counts.reduce((n,r)=>n+Number(r.count),0)),'Todas as origens; veja a separação abaixo'),metric('Capturas',fmt(captures.reduce((n,r)=>n+Number(r.count),0)),'Manifestos recebidos'),metric('Projeções pendentes',fmt(s.analytics_pending),'Não confundir com dados perdidos'),metric('Registro de tokens',s.token_registry?.current?'Atual':'Atrasado','Fila: '+fmt(s.token_registry?.queue?.pending),!s.token_registry?.current));
  table('base-coverage',['Camada','Origem / cadeia','Estado','Quantidade'],[...counts.map(r=>['Tokens',r.source_kind+' / '+r.chain,'Registrados',fmt(r.count)]),...captures.map(r=>['Capturas',r.source_kind,r.normalization_state??r.state,fmt(r.count)])]);$('full-status').textContent=JSON.stringify(s,null,2);
 }
 $('dictionary-form').addEventListener('submit',safe(e=>{e.preventDefault();return loadDictionary();},'dictionary-message'));
 $('tokens-form').addEventListener('submit',safe(e=>{e.preventDefault();return refreshTokens();},'tokens-message'));
 $('refresh-tokens').addEventListener('click',safe(refreshTokens,'tokens-message'));
 $('token-to-curve').addEventListener('click',()=>{if(!selectedToken)return;$('token').value=selectedToken.token_address;$('source').value=selectedToken.source_kind;$('day').value=selectedToken.cutoff.slice(0,10);$('cutoff').value=selectedToken.cutoff;showTab('trajectory');});
 $('token-to-capture').addEventListener('click',safe(()=>selectedToken&&openCapture(selectedToken.capture_id,selectedToken.source_kind,selectedToken.cutoff)));
 $('token-more-facts').addEventListener('click',()=>{if(!selectedToken)return;$('observation-view').hidden=false;$('observations-start').value=new Date(Date.parse(selectedToken.cutoff)-86400000).toISOString();$('observations-end').value=selectedToken.cutoff;});
 $('observations-form').addEventListener('submit',safe(e=>{e.preventDefault();return observations();},'tokens-message'));
 $('capture-form').addEventListener('submit',safe(e=>{e.preventDefault();return refreshCaptures();}));$('refresh-data').addEventListener('click',safe(()=>Promise.all([refreshCaptures(true),refreshDictionary()])));
 $('raw-fields-form').addEventListener('submit',safe(e=>{e.preventDefault();return loadFields();}));$('capture-download').addEventListener('click',safe(downloadCapture));
 $('capture-family').addEventListener('click',safe(()=>selectedCapture&&openFamily(selectedCapture.capture_id,selectedCapture.endpoint,selectedCapture.source_kind,selectedCaptureCutoff)));
 $('refresh-ingestion').addEventListener('click',safe(async()=>pretty('ingestion-detail',await api('/v1/ingestion?limit=100'))));$('refresh-paper').addEventListener('click',safe(refreshPaper,'paper-message'));
 return {overview,openCapture,openFamily,pageLocal,refresh:name=>{const work={tokens:refreshTokens,data:()=>Promise.all([refreshCaptures(false),refreshDictionary()]),paper:refreshPaper}[name];if(work)safe(work,name==='tokens'?'tokens-message':name==='paper'?'paper-message':'data-message')();}};
}
