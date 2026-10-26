// 실제 Vue runtime/compiled render를 사용한 분석 화면 회귀. 브라우저 레이아웃 평가는 별도다.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const sandbox={console,Intl,Date,TextEncoder,setTimeout,clearTimeout};sandbox.window=sandbox;vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('static/vendor/vue.global.prod.js','utf8'),sandbox);
let state;
sandbox.Vue.onMounted=()=>{};sandbox.Vue.onBeforeUnmount=()=>{};
sandbox.Vue.createApp=options=>({mount(){state=options.setup();}});
vm.runInContext(fs.readFileSync('static/finance-render.js','utf8'),sandbox);
vm.runInContext(fs.readFileSync('static/finance.js','utf8'),sandbox);
const chosen=state.displayRun([{id:'verified',status:'finished',trigger:{collection_result:{next_phase:'numeric_and_xbrl_validation'}}},{id:'freshness',status:'finished',trigger:{collection_result:{next_phase:'reuse_existing_published_or_verified_result'}}}]).id;
assert.equal(chosen,'verified');
state.research.value={company:{name:'합성 화면 시험',stock_code:''},bases:[{data:{purpose:'합성 시험'}}],runs:[]};
state.view.value={report_id:'report',financial_rows:[],evidence:[{id:'E1',fact_id:'fact1'}]};
state.currentTab.value='analysis';
state.analysis.value={financial_metrics:[],analysis_signals:[],analysis_plan:{steps:[{id:'gap:OFS:missing',kind:'verification_gap',question:'계정 대응을 확인할 자료가 있나요?',requested_information:'원문 계정표',reason_code:'mapping_unverified',limitation:'미검증 값을 사용하지 않습니다.',input_fact_ids:['fact1']}]},statistical_analysis:{status:'insufficient_peer_data',trend_analysis:[],peer_comparisons:[]},ml_readiness:{training_status:'not_trained_insufficient_data'},anomaly_detection:{status:'not_trained_insufficient_data'},followup_questions:{questions:[]}};
function text(node){if(node==null)return '';if(typeof node==='string')return node;if(Array.isArray(node))return node.map(text).join(' ');return text(node.children);}
function render(){const ctx=sandbox.Vue.proxyRefs(state);return text(sandbox.FinanceRender.call(ctx,ctx,[]));}
let content=render();assert(content.includes('비교 가능한 검증 데이터가 부족하여 머신러닝 모델이 학습되지 않았습니다.'));assert(content.includes('다음 조사 계획'));assert(content.includes('원문 계정표'));assert(!content.includes('탐색 점수'));
state.analysis.value.statistical_analysis={status:'measured',comparison_definition:{industry_code:'C29',industry_system:'synthetic',industry_version:'test',accounting_standard:'K-IFRS',scope:'OFS',public_cutoff:'2026-10-23'},trend_analysis:[],peer_comparisons:[{status:'measured',comparison_definition:{metric:'operating_margin',period_end:'2025-12-31'},distinct_company_count:20,sample_size:20,descriptive:{median:'10',q1:'5',q3:'15',iqr:'10'},target_value:'11',percentile_rank:'55',robust_outlier:{status:'unmeasured',reason:'zero_mad'}}]};
content=render();assert(content.includes('중앙값'));assert(content.includes('백분위'));assert(!content.includes('탐색 점수'));
state.openAnalysisEvidence(['fact1']);assert.equal(state.selectedEvidence.value.id,'E1');
state.view.value=null;
state.runAnalysis.value={row_counts:{verified:1,withheld:1},xbrl_gate:{matched_row_keys:['OFS:2025-12-31:revenue']},financial_rows:[{scope:'OFS',end:'2025-12-31',account:'revenue',label:'매출액',status:'verified',value:'1000',validation_ids:['V1'],fact_ids:['F1']},{scope:'OFS',end:'2025-12-31',account:'assets',label:'자산',status:'withheld',reason:'input_missing'}],financial_metrics:[{analysis_id:'M1',name:'operating_margin',scope:'OFS',period:{end:'2025-12-31'},status:'supported',value:'10',unit:'percent',source_ids:['S1'],input_validation_ids:['V1'],input_fact_ids:['F1']}],evidence:[{fact_id:'F1',fragment_id:'FR1',source_id:'S1',title:'공시 원문',receipt_number:'20260001',location:'표 1 / 행 2',excerpt:'매출액 1,000',content_hash:'abc',url:'https://dart.fss.or.kr/example'}],analysis_plan:{steps:[{id:'G1',kind:'verification_gap',question:'자산 근거가 있나요?',requested_information:'원문',reason_code:'input_missing'}]}};
content=render();assert(content.includes('미게시 검증 자료'));assert(content.includes('1000'));assert(content.includes('자산 근거가 있나요?'));assert(content.includes('input_missing'));assert(!content.includes('PDF ↓'));
state.openRunEvidence(['F1']);content=render();assert(content.includes('매출액 1,000'));assert(content.includes('표 1 / 행 2'));assert(content.includes('검증 위치의 원본 바이트'));
state.runAnalysis.value.disclosure_history={status:'partial_withheld',withheld_period_ends:['2026-06-30']};content=render();assert(content.includes('정정 전후 관계가 미확인'));
state.runAnalysis.value.disclosure_history={status:'pass',confirmed_relations:[{original_receipt:'20260813000914',receipt_number:'20260814002314'}]};content=render();assert(content.includes('정정 원본 관계 확인'));assert(content.includes('20260813000914'));assert(!content.includes('정정 전후 관계가 미확인'));
console.log('Vue 분석 화면: 표본 부족·통계 표시·원문 연결 통과');
