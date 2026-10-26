// 실제 Vue runtime/compiled render를 사용한 분석 화면 회귀. 브라우저 레이아웃 평가는 별도다.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const sandbox={console,Intl,Date,TextEncoder,setTimeout,clearTimeout};sandbox.window=sandbox;vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('static/vendor/vue.global.prod.js','utf8'),sandbox);
let state;
sandbox.Vue.onMounted=()=>{};sandbox.Vue.onBeforeUnmount=()=>{};
sandbox.Vue.createApp=options=>({mount(){state=options.setup();}});
vm.runInContext(fs.readFileSync('static/finance-render.js','utf8'),sandbox);
vm.runInContext(fs.readFileSync('static/finance.js','utf8'),sandbox);
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
console.log('Vue 분석 화면: 표본 부족·통계 표시·원문 연결 통과');
