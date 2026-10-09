const root = document.getElementById('ts-studio');
const $ = id => root.querySelector('#' + id);
const weekdays = ['月', '火', '水', '木', '金', '土', '日'];
const views = new Map(), drafts = new Map();
let threadsAllowed = true;
let readOnly = false;
let accounts = [], selected = null, csrf = '', saving = false, posting = false;

function icon(name) { const el = document.createElement('i'); el.dataset.lucide = name; el.setAttribute('aria-hidden', 'true'); return el; }
function icons() { if (window.lucide) window.lucide.createIcons({attrs:{width:16,height:16}}); }
function message(id, value) { $(id).textContent = value; $(id).hidden = !value; }
function status(value) { message('ts-save-status', value); $('ts-save-state').hidden = !value; }
function dirty(id) { const a = drafts.get(id), b = views.get(id)?.settings; return !!a && !!b && JSON.stringify([a.prompt,a.days,a.times,a.enabled]) !== JSON.stringify([b.prompt,b.days,b.times,b.enabled]); }
function anyDirty() { return accounts.some(a => dirty(a.id)); }
function current() { return accounts.find(a => a.id === selected); }
function draft() { return drafts.get(selected); }
function apiError(err) { if(err.status===401){csrf='';$('ts-login-screen').hidden=false;message('ts-login-error','ログインし直してください。');return;} message('ts-error', err.message || '通信に失敗しました。'); }
async function api(path, method='GET', data) {
  const headers = {'Accept':'application/json'};
  if (method !== 'GET') { headers['Content-Type'] = 'application/json'; headers['X-CSRF-Token'] = csrf; }
  const payload=data===undefined?undefined:JSON.stringify(data);
  const response = await fetch('/api' + path, {method, headers, credentials:'same-origin', body:payload});
  let body = null; try { body = await response.json(); } catch { /* network error */ }
  if (!response.ok) { const error = new Error(typeof body?.detail === 'string' ? body.detail : '操作に失敗しました。'); error.status = response.status; throw error; }
  return body;
}
function applyView(id, view, keepDraft=false) {
  views.set(id, view);
  if (!keepDraft || !drafts.has(id)) {
    const s = view.settings;
    drafts.set(id, {prompt:s.prompt,days:[...s.days],times:[...s.times],enabled:s.enabled});
  }
}
async function loadAccounts() {
  accounts = await api('/accounts');
  if (!accounts.some(a=>a.id===selected)) selected = accounts[0]?.id || null;
  if (selected) await loadView(selected);
  render();
}
async function loadView(id, keepDraft=true) { applyView(id, await api('/accounts/' + encodeURIComponent(id)), keepDraft); }
function renderAccounts() {
  const box = $('ts-accounts'); box.replaceChildren();
  accounts.forEach(a=>{
    const b=document.createElement('button'); b.className='ts-account'; b.setAttribute('aria-pressed',String(a.id===selected));
    b.setAttribute('aria-label',a.name+(a.group_label?'、'+a.group_label:'')+'、定期投稿'+(a.enabled?'オン':'オフ'));
    const avatar=document.createElement('span'); avatar.className='ts-avatar'; avatar.textContent='';
    const copy=document.createElement('span'); copy.className='ts-account-copy';
    const name=document.createElement('span'); name.className='ts-account-name'; name.textContent=a.name+(a.group_label?' · '+a.group_label:'');
    const handle=document.createElement('span'); handle.className='ts-account-handle'; handle.textContent='@'+a.handle;
    copy.append(name,handle); const dot=document.createElement('span'); dot.className='ts-status-dot'+(a.enabled?'':' off');
    b.append(avatar,copy,dot); b.onclick=async()=>{selected=a.id; try {await loadView(a.id);render();} catch(err){apiError(err);} }; box.append(b);
  });
  $('ts-add').disabled=readOnly||!threadsAllowed||accounts.length>=5;
}
function renderSchedule() {
  const a=draft(); if(!a)return;
  $('ts-enabled').setAttribute('aria-checked',String(a.enabled));
  $('ts-active-label').textContent=a.enabled?'定期投稿 ON':'定期投稿 OFF';
  $('ts-days').replaceChildren(); $('ts-miniweek').replaceChildren();
  weekdays.forEach((d,i)=>{
    const b=document.createElement('button'); b.className='ts-day'; b.textContent=d; b.setAttribute('aria-pressed',String(a.days.includes(i))); b.setAttribute('aria-label',d+'曜日');
    b.onclick=()=>{a.days=a.days.includes(i)?a.days.filter(x=>x!==i):[...a.days,i].sort(); mark();renderSchedule();icons();}; $('ts-days').append(b);
    const mini=document.createElement('div');mini.className='ts-mini-day';mini.textContent=d;
    const dot=document.createElement('div');dot.className='ts-mini-dot'+(views.get(selected)?.settings.days.includes(i)&&views.get(selected)?.settings.enabled?' on':'');mini.append(dot);$('ts-miniweek').append(mini);
  });
  $('ts-times').replaceChildren();
  a.times.forEach((t,i)=>{
    const chip=document.createElement('div');chip.className='ts-time-chip';const input=document.createElement('input');input.type='time';input.value=t;input.required=true;input.setAttribute('aria-label','投稿時刻 '+(i+1));
    input.onchange=()=>{a.times[i]=input.value;mark();renderSummary();};const remove=document.createElement('button');remove.className='ts-remove';remove.setAttribute('aria-label',t+'の投稿枠を削除');remove.append(icon('x'));remove.onclick=()=>{a.times.splice(i,1);mark();renderSchedule();icons();};chip.append(input,remove);$('ts-times').append(chip);
  });
  const add=document.createElement('button');add.className='ts-add-time';add.append(icon('plus'),document.createTextNode('時刻'));add.disabled=a.times.length>=6;add.onclick=()=>{const t=['08:00','12:00','15:00','18:00','20:00','22:00'].find(x=>!a.times.includes(x))||'09:00';a.times.push(t);mark();renderSchedule();icons();};$('ts-times').append(add);
  renderSummary();
}
function renderSummary() {
  const a=draft(), view=views.get(selected); if(!a||!view)return;
  $('ts-frequency').textContent=a.enabled?(a.days.length===7?'毎日':`週${a.days.length}日`)+`、1日${a.times.length}回投稿`:'定期投稿を一時停止しています';
  const next=view.next_at?new Date(view.next_at):null;
  $('ts-next-time').textContent=!view.runtime_enabled?'全体停止':!view.settings.enabled?'一時停止':next?new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(next):'未設定';
  $('ts-next-date').textContent=!view.runtime_enabled?'接続設定から開始できます':!view.settings.enabled?'再開すると予定が表示されます':next?new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',month:'long',day:'numeric',weekday:'short'}).format(next)+' · JST':'曜日と時刻を設定してください';
}
function render() {
  renderAccounts();const a=current(), empty=!a;
  root.querySelector('.ts-columns').hidden=empty; root.querySelector('.ts-savebar').hidden=empty;
  $('ts-post-now').hidden=empty;
  $('ts-enabled').disabled=empty||readOnly;$('ts-connection').hidden=empty;
  $('ts-connection').disabled=empty||readOnly;
  $('ts-post-now').disabled=empty||readOnly;$('ts-save').disabled=empty||readOnly;
  $('ts-prompt').readOnly=readOnly;
  if(empty){$('ts-name').textContent='アカウントを追加';$('ts-handle').textContent='';$('ts-active-label').textContent='定期投稿 OFF';status('');icons();return;}
  const d=draft(),v=views.get(selected), run=v?.latest_run;
  $('ts-name').textContent=a.name;$('ts-handle').textContent='@'+a.handle;
  $('ts-prompt').value=d.prompt;$('ts-prompt-count').textContent=d.prompt.length+' / 2,400';
  $('ts-preview-user').textContent=a.handle;$('ts-preview-avatar').textContent='';
  $('ts-preview-text').textContent=v?.last_generated_text||'まだ生成した投稿はありません。';
  $('ts-preview-state').textContent=run?.status||'未生成';
  renderSchedule();status(dirty(selected)?'未保存の変更があります':'');
  for(const field of root.querySelectorAll('#ts-days button, #ts-times button, #ts-times input'))field.disabled=readOnly;
  message('ts-error',a.last_error||'');icons();
}
function mark(){status(dirty(selected)?'未保存の変更があります':'');message('ts-error','');}
function dialog(content,title='接続設定'){$('ts-dialog-title').textContent=title;$('ts-dialog-content').replaceChildren(content);$('ts-dialog-error').hidden=true;$('ts-dialog').hidden=false;}
function closeDialog(){$('ts-dialog').hidden=true;$('ts-dialog-content').replaceChildren();}
function field(label,type='text',value=''){const el=document.createElement('label');el.textContent=label;const input=document.createElement('input');input.type=type;input.value=value;input.autocomplete='off';el.append(input);return [el,input];}
function action(text,click,kind='ts-primary'){const b=document.createElement('button');b.className=kind;b.type='button';b.textContent=text;b.onclick=click;return b;}
function dialogError(err){message('ts-dialog-error',err.message||'操作に失敗しました。');}
async function connectionDialog() {
  $('ts-dialog-title').textContent='接続設定';
  const a=current();if(!a)return;const box=document.createElement('div');
  const description=document.createElement('p');description.className='ts-global-note';description.textContent=a.connection==='connected'?'接続済み':a.last_error||'再接続が必要です。';box.append(description);
  const [idLabel,idInput]=field('ThreadsユーザーID','text',a.id);idInput.readOnly=true;
  const [tokenLabel,tokenInput]=field('新しいアクセストークン','password');box.append(idLabel,tokenLabel);
  const actions=document.createElement('div');actions.className='ts-modal-actions';
  actions.append(action('トークンを更新',async()=>{try{await api('/accounts/'+a.id+'/connection','PUT',{user_id:a.id,access_token:tokenInput.value});tokenInput.value='';closeDialog();await loadAccounts();}catch(err){dialogError(err);}}));
  actions.append(action('接続解除',async()=>{if(!window.confirm('定期投稿を停止して接続を解除しますか？'))return;try{await api('/accounts/'+a.id+'/connection','DELETE');views.delete(a.id);drafts.delete(a.id);closeDialog();await loadAccounts();}catch(err){dialogError(err);}},'ts-danger'));
  actions.append(action('閉じる',closeDialog,'ts-utility'));box.append(actions);
  const run=views.get(a.id)?.latest_run;
  if(run?.status==='unknown'||run?.status==='publishing'){
    const detail=document.createElement('p');detail.className='ts-global-note';detail.textContent='公開結果不明：Threads上で投稿を照合してください。実行ID '+run.id;box.append(detail);
    const [postLabel,postInput]=field('公開済みの場合の投稿ID');box.append(postLabel);
    box.append(action('公開済みと確定',async()=>{try{await api('/runs/'+encodeURIComponent(run.id)+'/resolve','POST',{post_id:postInput.value});closeDialog();await loadAccounts();}catch(err){dialogError(err);}}));
    box.append(action('未公開と確定',async()=>{if(!window.confirm('Threads上で未公開を確認しましたか？'))return;try{await api('/runs/'+encodeURIComponent(run.id)+'/resolve','POST',{confirmed_not_published:true});closeDialog();await loadAccounts();}catch(err){dialogError(err);}},'ts-danger'));
  }
  const runtime=await api('/runtime');
  if(runtime.can_enable!==false){const note=document.createElement('p');note.className='ts-global-note';note.textContent=runtime.enabled?'全体の定期実行：稼働中':'全体の定期実行：停止中';box.append(note);
    box.append(action(runtime.enabled?'全体の定期実行を停止':'全体の定期実行を開始',async()=>{try{await api('/runtime','PUT',{enabled:!runtime.enabled});await loadView(selected);render();closeDialog();await connectionDialog();}catch(err){dialogError(err);}},'ts-utility'));}
  dialog(box);
}

$('ts-enabled').onclick=()=>{draft().enabled=!draft().enabled;mark();renderSchedule();renderAccounts();icons();};
$('ts-prompt').oninput=e=>{draft().prompt=e.target.value;$('ts-prompt-count').textContent=e.target.value.length+' / 2,400';mark();};
$('ts-save').onclick=async()=>{if(saving||posting||!selected)return;const d=draft(), v=views.get(selected);
  if(d.enabled&&(!d.days.length||!d.times.length||!d.prompt.trim()||new Set(d.times).size!==d.times.length)){message('ts-error','曜日・時刻・プロンプトを設定し、重複する時刻を削除してください。');return;}
  const accountId=selected,payload={prompt:d.prompt,days:[...d.days],times:[...d.times],enabled:d.enabled,version:v.settings.version};
  const submitted=JSON.stringify([payload.prompt,payload.days,payload.times,payload.enabled]);saving=true;$('ts-save').disabled=true;$('ts-post-now').disabled=true;
  try{const result=await api('/accounts/'+encodeURIComponent(accountId)+'/settings','PUT',payload);const draftNow=drafts.get(accountId);const changedDuringSave=JSON.stringify([draftNow.prompt,draftNow.days,draftNow.times,draftNow.enabled])!==submitted;applyView(accountId,result.view,changedDuringSave);const account=accounts.find(a=>a.id===accountId);if(account)account.enabled=result.view.settings.enabled;message('ts-error','');render();if(selected===accountId&&!dirty(accountId))status('設定を保存しました');}
  catch(err){apiError(err);}finally{saving=false;$('ts-save').disabled=false;$('ts-post-now').disabled=false;}
};
$('ts-post-now').onclick=async()=>{if(posting||saving||!selected)return;
  const accountId=selected,prompt=draft()?.prompt||'';
  if(!prompt.trim()){message('ts-error','プロンプトを入力してください。');return;}
  posting=true;$('ts-post-now').disabled=true;$('ts-save').disabled=true;message('ts-error','');status('投稿処理中…');
  try{const result=await api('/accounts/'+encodeURIComponent(accountId)+'/post-now','POST',{prompt});
    await loadAccounts();
    if(selected===accountId){
      if(result.status==='published')status('投稿しました');
      else {status('');message('ts-error',result.status==='unknown'?'公開結果を接続設定から確認してください。':'投稿に失敗しました。接続設定と実行状態を確認してください。');}
    }
  }catch(err){apiError(err);status('');}finally{posting=false;$('ts-post-now').disabled=false;$('ts-save').disabled=false;}
};
$('ts-add').onclick=()=>{const show=$('ts-add-form').hidden;$('ts-add-form').hidden=!show;$('ts-add').setAttribute('aria-expanded',String(show));if(show)$('ts-new-id').focus();};
$('ts-add-form').onsubmit=async e=>{e.preventDefault();const id=$('ts-new-id').value.trim(),token=$('ts-new-token').value;
  if(!/^\d+$/.test(id)||!token){message('ts-add-error','ユーザーIDとアクセストークンを入力してください。');return;}
  const button=$('ts-add-form').querySelector('button[type=submit]');button.disabled=true;
  try{await api('/accounts','POST',{user_id:id,access_token:token});$('ts-add-form').reset();$('ts-add-form').hidden=true;$('ts-add').setAttribute('aria-expanded','false');selected=id;await loadAccounts();}
  catch(err){message('ts-add-error',err.message);}finally{button.disabled=false;}
};
$('ts-connection').onclick=()=>connectionDialog().catch(apiError);
$('ts-dialog').onclick=e=>{if(e.target===$('ts-dialog'))closeDialog();};
$('ts-logout').onclick=async()=>{if(anyDirty()&&!window.confirm('未保存の変更があります。ログアウトしますか？'))return;try{await api('/logout','POST');location.reload();}catch(err){apiError(err);}};
window.addEventListener('beforeunload',e=>{if(anyDirty()){e.preventDefault();e.returnValue='';}});
async function boot(){
  try{const user=await api('/me');csrf=user.csrf_token;readOnly=user.read_only===true;$('ts-login-screen').hidden=true;$('ts-logout').hidden=false;
    threadsAllowed=user.meta_group?.threads_allowed!==false;
    let label=root.querySelector('#ts-active-group');
    if(!label){label=document.createElement('span');label.id='ts-active-group';label.className='ts-global-note';$('ts-logout').before(label);}
    label.textContent=user.meta_group?.label+(readOnly?'（閲覧専用）':'');
    if(!threadsAllowed){accounts=[];selected=null;views.clear();drafts.clear();render();
      $('ts-add-form').hidden=true;message('ts-error',user.threads_denied_message);return;}
    message('ts-error','');
    try{await loadAccounts();}catch(err){accounts=[];render();apiError(err);}}
  catch(err){$('ts-login-screen').hidden=false;if(err.status!==401)message('ts-login-error',err.message);}
}
$('ts-login-form').onsubmit=async e=>{
  e.preventDefault();const button=$('ts-login');button.disabled=true;message('ts-login-error','');
  try{await api('/session','POST',{password:$('ts-login-password').value});$('ts-login-password').value='';await boot();}
  catch(err){message('ts-login-error',err.message||'ログインに失敗しました。');}
  finally{button.disabled=false;}
};
boot();
