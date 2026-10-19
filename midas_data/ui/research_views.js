// Views of immutable research artifacts; no fitting, trading or hidden retries.
export function installResearchViews(h){
 const {$,api,table,button,pretty,fmt,utc,metric,message,explorer}=h;
 let cohort=null,request=0,studySections=[];
 const safe=fn=>(...args)=>{args[0]?.preventDefault?.();return Promise.resolve().then(()=>fn(...args)).catch(e=>message('research-message',e.message,true));};
 const labels={reached:'Atingiu',not_reached:'Não atingiu nas amostras',unknown:'Desconhecido',censored:'Censurado'};
 function members(){
  if(!cohort)return;const q=$('cohort-member-query').value.trim().toLowerCase(),filter=$('cohort-member-state').value;
  const rows=cohort.members.filter(m=>(!q||(m.token_address+' '+m.registry_id).toLowerCase().includes(q))&&(!filter||(filter==='available'?m.entry.state==='available':m.entry.state!=='available')));
  explorer.pageLocal('cohort-pages',rows,page=>table('cohort-members',['Token','Entrada UTC','Market cap na entrada','Saída conhecida','Labels / níveis','Evidência'],page.map(m=>[
   m.token_address,utc(m.entry_at),m.entry.point?.value_text??'Desconhecido',m.exit.state==='available'?'Sim':'Não',m.outcome.labels.map(l=>l.level_usd+': '+(labels[l.state]||l.state)).join(' · '),button('Ver membro',()=>pretty('cohort-member-detail',m))])));
 }
 async function openCohort(job){
  const version=++request;pretty('research-detail',job);$('dataset-id').value=job.job_id;$('cohort-view').hidden=true;
  if(job.state!=='ready')return;
  const bundle=await api('/v1/research/jobs/'+encodeURIComponent(job.job_id)+'/bundle');if(version!==request)return;
  cohort=bundle;$('cohort-view').hidden=false;
  const s=bundle.summary;$('cohort-metrics').replaceChildren(metric('Membros',fmt(s.population),'Frame: '+fmt(bundle.sampling_frame?.population)+' identidades'),metric('Entrada conhecida',fmt(s.entry_available),'Desconhecidas: '+fmt(s.entry_unknown)),metric('Saída conhecida',fmt(bundle.members.filter(m=>m.exit.state==='available').length),'Cotação elegível no horizonte'),metric('Holders conhecidos',fmt(s.holder_available),'Conforme contrato da entrada'));
  table('cohort-levels',['Nível USD','N','Atingiu','Não atingiu','Censurado','Desconhecido','Bounds da população'],s.levels.map(l=>[l.level_usd,l.N,l.reached,l.not_reached,l.censored,l.unknown,fmt(Number(l.population_lower_bound)*100)+'%–'+fmt(Number(l.population_upper_bound)*100)+'%']));
  $('cohort-member-query').value='';$('cohort-member-state').value='';members();
  message('research-message','Coorte carregada: '+s.population+' membros; censura e desconhecidos permanecem no denominador.');
 }
 function studyRows(value){
  const rows=[];
  for(const [split,data] of Object.entries(value||{})){
   if(!data||typeof data!=='object')continue;
   for(const [key,v] of Object.entries(data))if(!Array.isArray(v)&&(v===null||typeof v!=='object'))rows.push([split,key,v]);
  }return rows;
 }
 function openExperiment(result){
  $('experiment-view').hidden=false;
  pretty('experiment-detail',{version:result.version,dataset_sha256:result.dataset_sha256,result_sha256:result.result_sha256,definition:result.definition,membership:result.membership});
  const d=result.definition;
  $('experiment-context').textContent='Simulação exploratória de cotações · taxa '+d.fee_bps+' bps + slippage '+d.slippage_bps+' bps por lado · rede USD '+d.network_usd_per_side+' por lado · capital USD '+d.starting_cash_usd+' · embargo '+d.embargo_seconds+'s. Sem prova de fills; retorno total permanece desconhecido quando há posições sem saída.';
  const rows=[];
  for(const strategy of result.results)for(const split of ['train','validation','test']){
   const s=strategy.splits[split];if(!s)continue;
   rows.push([strategy.definition.name,split,s.population,s.states.closed,s.unresolved_positions,s.states.unknown,s.states.blocked_capital,fmt(s.closed_lot_pnl_usd),s.return_pct===null?'Desconhecido':fmt(s.return_pct)+'%',button('Explorar',()=>openLedger(strategy.definition.name,split,s))]);
  }
  table('experiment-results',['Estratégia','Período','N','Fechadas','Sem saída','Desconhecidas','Sem capital','P&L fechado USD','Retorno total','Detalhes'],rows);
  table('experiment-studies',['Estudo','Tipo','Resultados'],(result.studies?.outputs||[]).map(study=>[study.definition.name,study.definition.kind,button('Ver estudo',()=>{
   openStudy(study);
  })]));
  if(result.results.length){const first=result.results[0];openLedger(first.definition.name,'train',first.splits.train);}
 }
 function openLedger(name,split,s){
  const groups={decisions:s.decision_membership||[],closed:s.closed||[],ledger:s.ledger||[]};
  const display={decisions:'Decisões',closed:'Lotes fechados',ledger:'Movimentos de caixa'};
  const choices=$('experiment-ledger-choice');choices.replaceChildren();
  const label=document.createElement('span');label.className='subtle';label.textContent=name+' / '+split;choices.append(label);
  function show(kind){const values=groups[kind];explorer.pageLocal('experiment-ledger-pages',values,page=>table('experiment-ledger',['Token / registro','Estado / tipo','Horário UTC','Valor USD','Detalhes'],page.map(r=>[r.token_address??r.registry_id,r.state??r.kind??'Fechado',utc(r.at??r.exit_at),r.net_pnl_usd??r.cash??'—',button('Ver registro',()=>pretty('experiment-row-detail',r))])));}
  for(const key of Object.keys(groups))choices.append(button(display[key]+' ('+groups[key].length+')',()=>show(key)));
  show('decisions');
 }
 function openStudy(study){
  pretty('study-detail',study);$('study-view').hidden=false;studySections=[];
  function visit(value,path){
   if(Array.isArray(value)){studySections.push({path,rows:value.map(v=>v&&typeof v==='object'&&!Array.isArray(v)?v:{value:v})});return;}
   if(value&&typeof value==='object'){
    const scalars=Object.entries(value).filter(([,v])=>v===null||typeof v!=='object').map(([field,value])=>({field,value}));
    if(scalars.length)studySections.push({path:path+' / resumo',rows:scalars});
    for(const [k,v] of Object.entries(value))if(v&&typeof v==='object')visit(v,path+' / '+k);
   }
  }
  visit(study.result,study.definition.name);const selector=$('study-section');selector.replaceChildren();
  studySections.forEach((section,i)=>{const option=document.createElement('option');option.value=i;option.textContent=section.path+' ('+section.rows.length+')';selector.append(option);});
  showStudySection();message('research-message','Estudo carregado em tabelas por seção e período; contrato completo disponível abaixo.');
 }
 function showStudySection(){
  const section=studySections[Number($('study-section').value)];if(!section)return;
  const keys=[...new Set(section.rows.flatMap(r=>Object.keys(r)))].slice(0,7);
  explorer.pageLocal('study-pages',section.rows,rows=>table('study-rows',[...keys,'Registro completo'],rows.map(r=>[...keys.map(k=>r[k]===null?'Desconhecido':typeof r[k]==='object'?'Ver registro':r[k]),button('Ver registro',()=>pretty('study-row-detail',r))])));
 }
 $('study-section').addEventListener('change',showStudySection);
 $('cohort-member-filter').addEventListener('submit',safe(members));
 return {openCohort:safe(openCohort),openExperiment};
}
