// Feeds hostile input through the REAL page code (same loader as parity.mjs) and asserts nothing
// that could become markup survives. Fails loudly if a fix regresses.
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
// the sharecase checkout, beside this repo unless SHARECASE_REPO says otherwise
const REPO = process.env.SHARECASE_REPO || new URL('../folio-terminal/', import.meta.url).pathname;
const stub=()=>new Proxy(function(){},{get:(t,k)=>k===Symbol.toPrimitive?()=>'':stub(),apply:()=>stub(),set:()=>true});
function load(file,names){const html=readFileSync(REPO+file,'utf8');const code=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].pop()[1];
  const ctx={console,document:stub(),localStorage:{getItem:()=>null,setItem(){},removeItem(){}},addEventListener(){},setInterval(){},setTimeout(){},
    fetch:()=>new Promise(()=>{}),window:{},navigator:{},location:{},XLSX:undefined};vm.createContext(ctx);
  vm.runInContext(code+`\n;globalThis.__x={${names.map(n=>`${n}:typeof ${n}!=="undefined"?${n}:null`).join(',')}};`,ctx);return ctx.__x;}
const EVIL='<img src=x onerror=alert(1)>';let fails=0;const ok=(c,m)=>{console.log((c?'ok   ':'FAIL ')+m);if(!c)fails++;};
// Workbench: validate() is the gate for a shared sheet
const b=load('basket.html',['validate','esc','SYMRE']);
let threw=''; try{b.validate({label:'x',weights:{[EVIL]:100}});}catch(e){threw=e.message;}
ok(threw && !threw.includes('<'), 'sheet with a markup symbol is rejected, and the message carries no markup: '+threw);
threw=''; try{b.validate({label:{toString(){return EVIL}},weights:{TCS:100}});}catch(e){threw=e.message;}
ok(!!threw,'non-string label is rejected');
const s=b.validate({label:EVIL,theme:EVIL,weights:{TCS:60,'M&M':40},prices:{[EVIL]:5,TCS:3000}});
ok(Object.keys(s.prices).join()==='TCS','bad keys are dropped from prices');
ok(!b.esc(s.label).includes('<') && !b.esc('"').includes('"'),'esc() neutralises < and quotes');
ok(b.SYMRE.test('M&M')&&b.SYMRE.test('BAJAJ-AUTO')&&b.SYMRE.test('ACME-BE')&&!b.SYMRE.test('A B'),'real symbols still pass');
// Dashboard: buildData() is the gate for uploaded files
const d=load('dashboard.html',['buildData','SYMOK']);
const out=d.buildData([{sym:EVIL,date:'2026-01-01',type:'buy',qty:1,price:1,time:'2026-01-01'},{sym:'TCS',date:'2026-01-01',type:'buy',qty:2,price:10,time:'2026-01-01'}],
  [{s:EVIL,q:1,avg:1,ltp:1},{s:'TCS',q:2,avg:10,ltp:12}],0,'test');
const txt=JSON.stringify(out);
ok(!txt.includes('<img'),'no hostile symbol survives buildData');
ok(out.holdings.length===1 && out.holdings[0].s==='TCS','the real holding is kept');
ok(out.attention.some(a=>/Skipped/.test(a.s)&&/2 rows/.test(a.msg)),'the skip is shown, not silent: '+JSON.stringify(out.attention.map(a=>a.msg)));
process.exit(fails?1:0);
