// ==UserScript==
// @name         MyCAMate Browser Upload to Telegram
// @namespace    local.lecture.transfer
// @version      3.2.0
// @description  Upload authorized lecture MP4 parts from this browser to Railway for merging and Telegram delivery.
// @match        https://foundation.badassworks.xyz/*
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      video.badassworks.xyz
// @connect      ubuntu-desktop-production-1c3377bc.up.railway.app
// ==/UserScript==
(function () {
  'use strict';
  const API = 'https://ubuntu-desktop-production-1c3377bc.up.railway.app';
  const VIDEO = 'video.badassworks.xyz';
  const ID = 'lecture-browser-transfer-v3';
  const CHUNK = 10 * 1024 * 1024;
  const PARALLEL_PARTS = 5;
  if (document.getElementById(ID)) return;
  let active = false, cancel = false;

  const root = document.createElement('div');
  root.id = ID;
  Object.assign(root.style, {
    position: 'fixed', top: '135px', right: '12px',
    zIndex: '2147483647', fontFamily: 'Arial,sans-serif',
    background: '#111827', color: '#fff', width: '220px',
    padding: '12px', borderRadius: '12px', border: '1px solid #475569',
    boxShadow: '0 6px 24px #0007'
  });
  const title = document.createElement('div');
  title.textContent = '🎬 Lecture → Telegram';
  title.style.cssText = 'font-weight:bold;margin-bottom:9px';
  const send = document.createElement('button');
  send.textContent = '📤 Upload Lecture';
  const settings = document.createElement('button');
  settings.textContent = '⚙ API Key';
  const stop = document.createElement('button');
  stop.textContent = '⏸ Pause';
  const status = document.createElement('div');
  status.textContent = 'Play lecture, then upload.';
  status.style.cssText = 'font-size:11px;margin-top:9px;overflow-wrap:anywhere;line-height:1.4';
  for (const b of [send,settings,stop]) {
    b.style.cssText = 'cursor:pointer;border:0;border-radius:7px;padding:8px;margin:3px 2px;background:#2563eb;color:white;font-size:12px';
  }
  stop.style.background = '#475569';
  root.append(title,send,settings,stop,status);
  (document.body || document.documentElement).appendChild(root);
  const say = value => {status.textContent = value;console.log('[LectureUpload]',value);};
  function getSavedKey() {
    const saved = GM_getValue('browser_api_key', '') || GM_getValue('api_key', '');
    if (saved) GM_setValue('browser_api_key', saved);
    return saved;
  }
  if (getSavedKey()) settings.textContent = '⚙ Key saved';

  function gm({url,method='GET',headers={},data,responseType='text',timeout=180000}) {
    return new Promise((resolve,reject) => GM_xmlhttpRequest({
      url, method, headers, data, responseType, timeout,
      onload: r => {
        if (r.status < 200 || r.status >= 300) {
          let detail = r.responseText || 'Request failed';
          try {detail = JSON.parse(detail).detail || detail;} catch {}
          reject(new Error('HTTP '+r.status+': '+(typeof detail==='string'?detail:JSON.stringify(detail))));
        } else resolve(r);
      },
      onerror:()=>reject(new Error('Network request failed')),
      ontimeout:()=>reject(new Error('Network timeout')),
      onabort:()=>reject(new Error('Request aborted'))
    }));
  }
  function manifestURL() {
    const entries = performance.getEntriesByType('resource')
      .filter(e => {
        try {
          const u = new URL(e.name);
          return u.hostname === VIDEO && u.pathname === '/m' &&
            ['l','e','s'].every(k=>u.searchParams.has(k)) &&
            Number(u.searchParams.get('e')) > Date.now()/1000;
        } catch {return false;}
      }).sort((a,b)=>b.startTime-a.startTime);
    return entries[0]?.name || null;
  }
  async function api(method,path,key,data) {
    const isBinary = data instanceof ArrayBuffer;
    const r = await gm({
      method,url:API+path,
      headers:{'X-API-Key':key,'Content-Type':isBinary?'application/octet-stream':'application/json'},
      data:data===undefined?undefined:(isBinary?data:JSON.stringify(data)),
      timeout:180000
    });
    return JSON.parse(r.responseText);
  }
  settings.onclick=()=>{
    const previous=getSavedKey();
    const key=prompt(previous?'Replace saved API key (Cancel keeps it):':'VPS API_KEY (saved for future uploads):');
    if(key?.trim()) {
      GM_setValue('browser_api_key',key.trim());
      settings.textContent = '⚙ Key saved';
      say('API key saved in Tampermonkey for future uploads');
    }
  };
  stop.onclick=()=>{cancel=true;say('Pausing after current request. Resume by clicking Upload Lecture.');};
  async function uploadPart(job, i, u, key, knownOffset=0) {
    // Requesting media bytes from the browser network, NOT from Railway.
    const r = await gm({url:u, responseType:'arraybuffer',timeout:300000});
    const bytes = r.response;
    if (!(bytes instanceof ArrayBuffer) || bytes.byteLength < 12)
      throw new Error('Invalid video response: part '+i);
    const sig = String.fromCharCode(...new Uint8Array(bytes.slice(4,8)));
    if(sig!=='ftyp') throw new Error('Not an MP4: part '+i);
    let offset=knownOffset;
    if(offset>bytes.byteLength) throw new Error('Server part offset exceeds media length');
    while(offset<bytes.byteLength) {
      if(cancel) return false;
      const end=Math.min(offset+CHUNK,bytes.byteLength);
      const resp=await api('PUT','/browser-jobs/'+job+'/parts/'+i+'?offset='+offset,
                           key,bytes.slice(offset,end));
      offset=resp.offset;
      say('Part '+(i+1)+' uploading: '+Math.round(offset*100/bytes.byteLength)+'%');
    }
    await api('POST','/browser-jobs/'+job+'/parts/'+i+'/finish',key,{size:bytes.byteLength});
    return true;
  }
  send.onclick=async()=>{
    if(active)return;
    cancel=false;active=true;send.disabled=true;
    try {
      let key=getSavedKey();
      if(!key) {
        key=prompt('VPS API_KEY:')?.trim();
        if(!key) return;
        GM_setValue('browser_api_key',key);
        settings.textContent = '⚙ Key saved';
      }
      const manifest=manifestURL();
      if(!manifest) throw new Error('Fresh /m URL not found. Play the lecture first.');
      const m=new URL(manifest);
      const lectureId=m.searchParams.get('l');
      const response=await gm({url:manifest,responseType:'text'});
      const metadata=JSON.parse(response.responseText);
      if(!Array.isArray(metadata.parts) || !metadata.parts.length)
        throw new Error('Invalid lecture manifest');
      const count=metadata.parts.length;
      const keyName='browser_job_'+lectureId;
      let job=GM_getValue(keyName,'');
      let state;
      if(job){
        try {
          state=await api('GET','/browser-jobs/'+job,key);
          if(state.total_parts!==count || state.phase==='complete') {
            job='';state=null;
          } else if(!['receiving','failed'].includes(state.phase)) {
            throw new Error('Job already '+state.phase+'. Check Telegram.');
          }
        } catch(e) {
          if(String(e.message).includes('HTTP 404')) {job='';state=null;}
          else throw e;
        }
      }
      if(!job) {
        // No repeated title prompt: use the player page title automatically.
        const pageTitle=(document.title||'').trim();
        const lectureTitle=pageTitle && !/^(telegram|mini app)$/i.test(pageTitle)
          ? pageTitle+' - '+lectureId : 'Lecture '+lectureId;
        state=await api('POST','/browser-jobs',key,{
          lecture_id:lectureId,total_parts:count,
          title:lectureTitle.slice(0,120)
        });
        job=state.job_id;
        GM_setValue(keyName,job);
      }
      const already=new Set(state.completed_parts||[]);
      const pending=Array.from({length:count},(_,i)=>i).filter(i=>!already.has(i));
      const startedAt=Date.now();
      let next=0, uploaded=already.size, failed=null;
      const speed=() => ((Date.now()-startedAt)/1000).toFixed(0)+'s';
      async function worker(){
        while(!cancel && !failed){
          const position=next++;
          if(position>=pending.length) return;
          const i=pending[position];
          try{
            if(Number(m.searchParams.get('e'))<=Date.now()/1000)
              throw new Error('Signed link expired. Reopen the lecture and resume.');
            const u=new URL(manifest);
            u.pathname=(i%2===0?'/p/':'/b/')+i;
            say('⬇️ '+(uploaded)+'/'+count+' saved | Active workers: '+PARALLEL_PARTS);
            const ok=await uploadPart(job,i,u.toString(),key,Number(state.offsets?.[String(i)]||0));
            if(!ok) return;
            uploaded++;
            say('✅ '+uploaded+'/'+count+' parts | '+speed()+' elapsed');
          } catch(e){
            failed=e;
            cancel=true;
            return;
          }
        }
      }
      await Promise.all(Array.from({length:Math.min(PARALLEL_PARTS,pending.length)},()=>worker()));
      if(failed) throw failed;
      if(cancel) {say('Paused. Reopen lecture and click Upload Lecture to resume.');return;}
      await api('POST','/browser-jobs/'+job+'/complete',key,{});
      say('✅ All parts sent! Railway merging and Telegram uploading. Page may be closed.');
      alert('All parts uploaded. VPS will merge and send to Telegram.');
    } catch(e) {
      say('❌ '+e.message);
      alert('Lecture upload stopped: '+e.message);
    } finally {
      active=false;send.disabled=false;
    }
  };
})();
