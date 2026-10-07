(() => {
'use strict';
const el = id => document.getElementById(id);
const number = value => new Intl.NumberFormat('en-GB').format(value);
const pages = {swaps: {page: 0, busy: false}, payments: {page: 0, busy: false}};
const reasons = {missing_transaction_id: 'Missing transaction ID', no_matching_operator_swap: 'No matching operator swap', no_matching_payment: 'No matching payment'};
function node(tag, value, className) { const n=document.createElement(tag); if(value!==undefined) n.textContent=value; if(className) n.className=className; return n; }
function error(message) {el('error').hidden=!message;el('error').textContent=message || '';}
async function api(path) { const response=await fetch(path,{cache:'no-store'}); const data=await response.json(); if(!response.ok) throw new Error(data.error || 'Unable to load reconciliation.'); return data; }
async function summary() {
    const data=await api('/api/payments-swap-summary');
    for(const [id,key] of [['payment-total','payment_total'],['swap-total','swap_total'],['matched','matched'],['missing-in-payments','missing_in_payments'],['missing-in-swap','missing_in_swap']]) el(id).textContent=number(data[key]);
}
function render(kind, data) {
    const rows=el(kind+'-rows'); rows.replaceChildren();
    el(kind+'-count').textContent=number(data.total);
    for(const r of data.records) {
        const row=node('tr');
        const amount=r.amount===null || r.amount===undefined || r.amount==='' ? '\u2014' : String(r.amount)+(r.currency ? ' '+r.currency : '');
        for(const [value,cls] of [[r.transaction_id,'reference'],[r.reference,'reference'],[r.customer_name,'customer-name'],[amount,'amount-column'],[r.status,''],[kind==='swaps'?r.pay_method:r.payment_operator,''],[r.transaction_date,''],[reasons[r.reason] || r.reason,'']]) row.append(node('td',value || '\u2014',cls));
        rows.append(row);
    }
    if(!data.records.length) {const row=node('tr');const cell=node('td','No missing transactions','empty-state');cell.colSpan=8;row.append(cell);rows.append(row);}
    const from=data.records.length ? data.offset+1 : 0;
    const to=data.offset+data.records.length;
    el(kind+'-page-info').textContent='Showing '+number(from)+' to '+number(data.records.length?to:0)+' of '+number(data.total);
}
async function loadPage(kind, page=0) {
    const current=pages[kind]; if(current.busy) return;
    current.busy=true;el(kind+'-previous').disabled=true;el(kind+'-next').disabled=true;
    try {const data=await api('/api/payments-swap-missing?'+new URLSearchParams({direction:kind,page})); current.page=page;current.data=data;render(kind,data);}
    catch(e) { current.data=null;error(e.message);const row=node('tr');const cell=node('td','Could not load records. Click Refresh to retry.','empty-state');cell.colSpan=8;row.append(cell);el(kind+'-rows').replaceChildren(row);el(kind+'-count').textContent='\u2014';el(kind+'-page-info').textContent='Results unavailable'; }
    finally {current.busy=false;el(kind+'-previous').disabled=!current.data || current.page===0;el(kind+'-next').disabled=!current.data?.has_more;}
}
async function refresh() {
    el('refresh').disabled=true;error();
    try {await Promise.all([summary(),loadPage('swaps'),loadPage('payments')]);}
    catch(e) {error(e.message);for(const id of ['payment-total','swap-total','matched','missing-in-payments','missing-in-swap']) el(id).textContent='\u2014';}
    finally {el('refresh').disabled=false;}
}
for(const kind of ['swaps','payments']) {el(kind+'-previous').addEventListener('click',()=>loadPage(kind,pages[kind].page-1));el(kind+'-next').addEventListener('click',()=>loadPage(kind,pages[kind].page+1));}
el('refresh').addEventListener('click',refresh);
document.addEventListener('data-imported',refresh);
const menu=el('payments-menu');const toggle=el('payments-toggle');
try {menu.classList.toggle('hidden',localStorage.getItem('payments-menu-open')==='false');}catch(e){}
toggle.addEventListener('click',()=>{menu.classList.toggle('hidden');try {localStorage.setItem('payments-menu-open',String(!menu.classList.contains('hidden')));}catch(e){}});
refresh();
})();
