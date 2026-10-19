(() => {
'use strict';
const el=id=>document.getElementById(id);
const number=value=>value===null || value===undefined ? '\u2014' : new Intl.NumberFormat('en-GB').format(value);
const countries=['Uganda','Kenya','Rwanda'];
const initial=new URLSearchParams(window.location.search).get('country') || '';
const state={country:countries.includes(initial)?initial:'',page:0,busy:false,reload:false,exporting:false,data:null};
el('due-country').value=state.country;
function node(tag,value,className){const n=document.createElement(tag);if(value!==undefined)n.textContent=value;if(className)n.className=className;return n;}
function error(message){el('due-error').hidden=!message;el('due-error').textContent=message||'';}
function controls(){el('due-previous').disabled=state.busy || !state.data || state.page===0;el('due-next').disabled=state.busy || !state.data?.has_more;el('due-export').disabled=state.exporting || state.busy || !state.data?.rule_configured || !state.data?.total;el('due-refresh').disabled=state.busy;}
function empty(text){const row=node('tr');const cell=node('td',text,'empty-state');cell.colSpan=8;row.append(cell);el('due-rows').replaceChildren(row);}
function render(data){
    for(const [id,key] of [['due-payment-total','payment_total'],['dues-total','dues_total'],['due-with-id','with_due_id'],['due-no-id','no_due']])el(id).textContent=number(data.summary[key]);
    el('due-missing-count').textContent=data.rule_configured?number(data.total):'\u2014';
    el('due-scope-note').textContent=data.rule_configured ? 'Uganda No due rule: Remarks = Settlement of Rent and Union Due ID (New) is null, empty or whitespace-only.' : 'The No due rule for '+state.country+' has not been configured yet.';
    el('due-source-title').textContent=!data.rule_configured ? state.country+' rule awaiting configuration' : data.loaded_total ? 'Payment data loaded' : 'No due-payment data loaded yet';
    el('due-source-caption').textContent=data.loaded_total ? number(data.loaded_total)+' payment records loaded. This check uses Uganda rent-settlement payments. A populated Due ID does not confirm that a due-table record exists.' : 'Upload your payment CSV using Add files & manage data.';
    el('due-rows').replaceChildren();
    for(const r of data.records){
        const row=node('tr');
        const amount=r.amount===null?'\u2014':String(r.amount)+(r.currency?' '+r.currency:'');
        for(const [value,cls] of [[r.atlas_transaction_id,'reference'],[r.payment_transaction_no,'reference'],[r.customer_name,'customer-name'],[amount,'amount-column'],[r.payment_status,''],[r.union_due_id_new,''],[r.payment_operator,''],[r.transaction_date,'']])row.append(node('td',value || '\u2014',cls));
        el('due-rows').append(row);
    }
    if(!data.records.length)empty(!data.rule_configured?'This country rule is awaiting configuration.':!data.loaded_total?'Upload a payment CSV to find payments without a due.':'No Uganda rent-settlement payments without a Due ID.');
    const from=data.records.length?data.offset+1:0;
    const to=data.records.length?data.offset+data.records.length:0;
    el('due-page-info').textContent=data.rule_configured?'Showing '+number(from)+' to '+number(to)+' of '+number(data.total):'Results unavailable for this country';
}
async function load(page=0){
    const requestCountry=el('due-country').value;
    state.busy=true;state.data=null;state.country=requestCountry;controls();error();
    try{
        const response=await fetch('/api/payments-due?'+new URLSearchParams({country:requestCountry,page}),{cache:'no-store'});
        const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not load Due payments.');
        // A country change during an earlier request triggers a fresh load below.
        if(el('due-country').value!==requestCountry)return;
        state.page=page;state.data=data;render(data);
    }catch(e){error(e.message);empty('Could not load payments. Click Refresh to retry.');for(const id of ['due-payment-total','dues-total','due-with-id','due-no-id','due-missing-count'])el(id).textContent='\u2014';el('due-page-info').textContent='Results unavailable';}
    finally{state.busy=false;controls();if(el('due-country').value!==requestCountry || state.reload){state.reload=false;load();}}
}
function apply(){const country=el('due-country').value;const url=new URL(window.location.href);if(country)url.searchParams.set('country',country);else url.searchParams.delete('country');window.history.replaceState(null,'',url.pathname+url.search+url.hash);if(!state.busy)load();}
el('due-filters').addEventListener('submit',event=>{event.preventDefault();apply();});
el('due-reset').addEventListener('click',()=>{el('due-country').value='';apply();});
el('due-refresh').addEventListener('click',()=>load());
el('due-previous').addEventListener('click',()=>load(state.page-1));
el('due-next').addEventListener('click',()=>load(state.page+1));
el('due-export').addEventListener('click',async()=>{
    state.exporting=true;controls();error();
    try{const response=await fetch('/api/payments-due-export?'+new URLSearchParams({country:state.country}),{cache:'no-store'});if(!response.ok){const data=await response.json();throw new Error(data.error||'Could not export payments.');}
        const url=URL.createObjectURL(await response.blob());const a=node('a');a.href=url;a.download='uganda_payments_without_due.csv';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(e){error(e.message);}finally{state.exporting=false;controls();}
});
document.addEventListener('data-imported',()=>{if(state.busy)state.reload=true;else load();});
load();
})();
