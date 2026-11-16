// ==UserScript==
// @name         Lecture to Telegram (authorized videos)
// @namespace    local.lecture.transfer
// @version      1.0.0
// @description  Submit the currently loaded lecture manifest to your VPS, without downloading media to the laptop.
// @match        https://foundation.badassworks.xyz/*
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      *
// ==/UserScript==
(function () {
  'use strict';
  if (document.getElementById('lecture-send-vps')) return;
  const b = document.createElement('button');
  b.id = 'lecture-send-vps';
  b.textContent = '📤 Send Lecture to Telegram';
  Object.assign(b.style, {position:'fixed',right:'18px',bottom:'90px',zIndex:'2147483647',padding:'13px 16px',border:'0',borderRadius:'10px',background:'#1769e0',color:'#fff',fontWeight:'bold',cursor:'pointer',boxShadow:'0 2px 16px #0005'});
  function getManifest() {
    const urls = [];
    for (const v of document.querySelectorAll('video')) if (v.currentSrc) urls.push(v.currentSrc);
    for (const e of performance.getEntriesByType('resource')) urls.push(e.name);
    for (const value of urls.reverse()) {
      try {
        const u = new URL(value);
        if (u.hostname !== 'video.badassworks.xyz') continue;
        if (!['/m'].includes(u.pathname) && !/^\/(p|b)\/\d+$/.test(u.pathname)) continue;
        if (!u.searchParams.has('l') || !u.searchParams.has('e') || !u.searchParams.has('s')) continue;
        u.pathname = '/m';
        u.hash = '';
        return u.toString();
      } catch (_) {}
    }
    return null;
  }
  function request(options) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({...options,timeout:30000,onload: r => {
        let data;
        try { data=JSON.parse(r.responseText); } catch (_) { data={detail:r.responseText}; }
        if (r.status < 200 || r.status >= 300) reject(Error(`HTTP ${r.status}: ${data.detail || r.responseText}`));
        else resolve(data);
      },ontimeout:()=>reject(Error('VPS request timeout')),onerror:()=>reject(Error('VPS connection failed'))});
    });
  }
  b.onclick = async () => {
    const manifest = getManifest();
    if (!manifest) { alert('Video signed URL not found. Play the lecture briefly and retry.'); return; }
    let api = GM_getValue('api_url', '');
    if (!api) {
      api = prompt('VPS API base URL (e.g. https://your-api.example.com)');
      if (!api) return;
      api = api.trim().replace(/\/$/, '');
      if (!/^https:\/\//.test(api)) { alert('Use HTTPS public URL'); return; }
      GM_setValue('api_url', api);
    }
    let key = GM_getValue('api_key', '');
    if (!key) {
      key = prompt('VPS API_KEY from your .env');
      if (!key) return;
      GM_setValue('api_key', key);
    }
    const title = prompt('Lecture title', document.title || 'Lecture') || 'Lecture';
    b.disabled = true; b.textContent = '⏳ Submitting...';
    try {
      const response = await request({method:'POST',url:`${api}/jobs`,headers:{'Content-Type':'application/json','X-API-Key':key},data:JSON.stringify({manifest_url:manifest,title})});
      b.textContent = '✅ Job sent to VPS';
      alert(`Job ${response.job_id} accepted. You can close this page now. Progress will appear in Telegram.`);
    } catch (e) {
      b.textContent = '❌ Submission failed';
      alert(e.message + '\nCheck VPS URL, API key, and tunnel.');
    } finally { b.disabled=false; setTimeout(()=>b.textContent='📤 Send Lecture to Telegram',4500); }
  };
  document.documentElement.appendChild(b);
})();
