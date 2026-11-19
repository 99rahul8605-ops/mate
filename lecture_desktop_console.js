// Telegram Desktop Console uploader v1.1 (streaming; no Tampermonkey)
// Run this code only for media you are authorized to transfer.
// For requests to third-party video origins, normal browser CORS still applies.
(function () {
  'use strict';
  const API = 'https://ubuntu-desktop-production-1c3377bc.up.railway.app';
  // API key stays in this page's JavaScript memory (not localStorage).
  let volatileApiKey = '';
  function GM_getValue(k, fallback) {
    if (k === 'browser_api_key' || k === 'api_key') return volatileApiKey || fallback;
    try {const v=localStorage.getItem('mate_desktop_'+k);return v===null?fallback:JSON.parse(v);}
    catch {return fallback;}
  }
  function GM_setValue(k, value) {
    if (k === 'browser_api_key' || k === 'api_key') {volatileApiKey=String(value);return;}
    localStorage.setItem('mate_desktop_'+k,JSON.stringify(value));
  }
  const VIDEO = 'video.badassworks.xyz';
  const ID = 'lecture-desktop-transfer-v1';
  const CHUNK = 512 * 1024; // Stream only 512 KiB per VPS request.
  const DEFAULT_PARALLEL_PARTS = 2; // Safer for slow Telegram Desktop WebViews.
  const WORKERS_KEY = "browser_upload_workers";
  const getWorkers = () => {
    const saved = Number(GM_getValue(WORKERS_KEY, DEFAULT_PARALLEL_PARTS));
    return Number.isInteger(saved) && saved >= 1 && saved <= 10 ? saved : DEFAULT_PARALLEL_PARTS;
  };
  const SAVED_JOBS = 'lecture_upload_queue_v1';
  if (document.getElementById(ID)) {console.log('[LectureDesktop] Uploader panel already exists');return;}
  let active = false, cancel = false;
  const sourceControllers = new Set();

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
  const workersButton = document.createElement('button');
  const setWorkersLabel = () => { workersButton.textContent = '⚙ Workers: '+getWorkers(); };
  setWorkersLabel();
  const queuedButton = document.createElement('button');
  queuedButton.textContent = '📋 Queue';
  const queueStatus = document.createElement('div');
  queueStatus.style.cssText = 'font-size:11px;margin-top:7px;max-height:130px;overflow:auto;white-space:pre-line;color:#cbd5e1';
  const status = document.createElement('div');
  status.textContent = 'Desktop Console mode. Open an online lecture and Play, then upload.';
  status.style.cssText = 'font-size:11px;margin-top:9px;overflow-wrap:anywhere;line-height:1.4';
  for (const b of [send,settings,workersButton,stop,queuedButton]) {
    b.style.cssText = 'cursor:pointer;border:0;border-radius:7px;padding:8px;margin:3px 2px;background:#2563eb;color:white;font-size:12px';
  }
  stop.style.background = '#475569';
  root.append(title,send,settings,workersButton,stop,queuedButton,status,queueStatus);
  (document.body || document.documentElement).appendChild(root);
  const say = value => {status.textContent = value;console.log('[LectureUpload]',value);};
  function getSavedKey() {
    const saved = GM_getValue('browser_api_key', '') || GM_getValue('api_key', '');
    if (saved) GM_setValue('browser_api_key', saved);
    return saved;
  }
  if (getSavedKey()) settings.textContent = '⚙ Key saved';

  function storedJobs() {
    const value = GM_getValue(SAVED_JOBS, []);
    return Array.isArray(value) ? value.filter(j => j && j.id) : [];
  }
  function rememberJob(id, name, lectureId) {
    const jobs = storedJobs().filter(j => j.id !== id);
    jobs.unshift({id, name, lectureId, phase:'receiving', updated:Date.now()});
    GM_setValue(SAVED_JOBS, jobs.slice(0, 30));
  }
  function updateStoredJob(id, changes) {
    const jobs = storedJobs();
    const j = jobs.find(j => j.id === id);
    if (j) {
      Object.assign(j, changes, {updated:Date.now()});
      GM_setValue(SAVED_JOBS, jobs);
    }
  }
  function showQueue() {
    const jobs = storedJobs();
    const live = jobs.filter(j => !['complete'].includes(j.phase));
    if (!jobs.length) {
      queueStatus.textContent = 'Queue: empty';
      return;
    }
    const names = live.slice(0, 5).map(j => {
      const state = j.phase === 'queued' ? '⏳' :
        j.phase === 'uploading' ? '📤' :
        j.phase === 'merging' ? '🔧' :
        j.phase === 'failed' ? '⚠️' : '⬇️';
      const pct = j.phase === 'uploading' && Number.isFinite(j.percent)
        ? ' '+j.percent+'%' : '';
      return state+' '+j.name.slice(0,38)+' ('+j.phase+pct+')';
    });
    queueStatus.textContent =
      'Pending: '+live.length+' | Delivered: '+jobs.filter(j=>j.phase==='complete').length+
      (names.length ? '\n'+names.join('\n') : '');
  }
  let queueRefreshing = false;
  async function refreshQueue() {
    if (queueRefreshing || active) return;
    const key = getSavedKey();
    if (!key) {showQueue();return;}
    queueRefreshing = true;
    try {
      for (const job of storedJobs().slice(0,15)) {
        if (job.phase === 'complete') continue;
        try {
          const state = await api('GET','/browser-jobs/'+job.id,key);
          updateStoredJob(job.id, {
            phase:state.phase, name:state.title||job.name,
            percent:Number(state.upload_percent)||0
          });
        } catch(e) {
          console.warn('[LectureUpload] Queue status unavailable:',job.id,e.message);
        }
      }
    } finally {
      queueRefreshing = false;
      showQueue();
    }
  }
  queuedButton.onclick=()=>refreshQueue();
  showQueue();
  setInterval(()=>{if(!active && !document.hidden) refreshQueue();},20000);
  async function gm({url,method='GET',headers={},data,responseType='text',timeout=180000}) {
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),timeout);
    try {
      const r=await fetch(url,{method,headers,body:data,signal:controller.signal,credentials:'omit'});
      if(!r.ok) {
        const text=await r.text();
        let detail=text||'Request failed';
        try {detail=JSON.parse(text).detail||detail;} catch {}
        throw new Error('HTTP '+r.status+': '+(typeof detail==='string'?detail:JSON.stringify(detail)));
      }
      if(responseType==='arraybuffer') return {status:r.status,response:await r.arrayBuffer()};
      return {status:r.status,responseText:await r.text()};
    } catch(e) {
      if(e.name==='AbortError') throw new Error('Network timeout');
      if(e instanceof TypeError) throw new Error('Browser blocked request (CORS or network). Check the Console and API server CORS headers.');
      throw e;
    } finally {clearTimeout(timer);}
  }
  // Collect signed video requests even when Chrome evicts Resource Timing entries.
  // A playing video may request /p/0 or /b/1 without a new /m request.
  let lastLectureClickAt = 0;
  const recentVideoRequests = [];
  function captureVideoRequest(name, startedAt) {
    try {
      const u = new URL(name);
      const isPart = u.pathname.startsWith('/p/') || u.pathname.startsWith('/b/');
      const validPart = isPart && Number.isInteger(Number(u.pathname.slice(3))) &&
        Number(u.pathname.slice(3)) >= 0;
      if (u.hostname !== VIDEO || !(u.pathname === '/m' || validPart) ||
          !['l','e','s'].every(k => u.searchParams.has(k))) return;
      const when = Number(startedAt) || performance.now();
      if (!recentVideoRequests.some(r => r.name === name && Math.abs(r.at - when) < 100))
        recentVideoRequests.push({name, at:when});
      if (recentVideoRequests.length > 100) recentVideoRequests.splice(0, recentVideoRequests.length - 100);
    } catch {}
  }
  try {
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) captureVideoRequest(entry.name, entry.startTime);
    }).observe({type:'resource', buffered:true});
  } catch {}
  function manifestURL() {
    for (const entry of performance.getEntriesByType('resource'))
      captureVideoRequest(entry.name, entry.startTime);
    const now = Date.now() / 1000;
    const candidates = recentVideoRequests.filter(item => {
      try {
        const url = new URL(item.name);
        return Number(url.searchParams.get('e')) > now + 30 &&
          (!lastLectureClickAt || item.at >= lastLectureClickAt - 1000);
      } catch { return false; }
    }).sort((a,b) => b.at - a.at);
    if (!candidates.length) return null;
    // Pick the most recently requested lecture, but prefer its real manifest
    // to an inferred /m URL when both are present.
    const latestId = new URL(candidates[0].name).searchParams.get('l');
    const matching = candidates.filter(item =>
      new URL(item.name).searchParams.get('l') === latestId
    );
    const source = matching.find(item => new URL(item.name).pathname === '/m') || matching[0];
    const manifest = new URL(source.name);
    manifest.pathname = '/m';
    return manifest.href;
  }
  async function waitForManifest() {
    for (let attempt = 0; attempt < 10; attempt++) {
      const manifest = manifestURL();
      if (manifest) return manifest;
      if (attempt < 9) await new Promise(resolve => setTimeout(resolve, 1000));
    }
    return null;
  }
  // The CA Mate lecture grid contains lecture numbers, not individual titles.
  // Capture the number from the actual lecture button clicked in this iframe.
  const SELECTED_LECTURE_KEY = 'ca_mate_selected_lecture_v1';
  const cleanHeading = value => String(value || '').replace(/\s+/g, ' ').trim();
  const currentCourse = () => cleanHeading(
    document.querySelector('.lt-back-title')?.textContent
  );
  const SELECTED_CHAPTER_KEY = 'ca_mate_selected_chapter_v1';
  function trackedChapterName(course) {
    let saved;
    try { saved = JSON.parse(sessionStorage.getItem(SELECTED_CHAPTER_KEY) || 'null'); }
    catch { return ''; }
    return saved && saved.course === course && typeof saved.name === 'string' &&
      Date.now() - saved.at < 12 * 60 * 60 * 1000 ? saved.name : '';
  }
  // Capture the chapter on the chapter-list screen BEFORE CA Mate opens the lecture grid.
  // Ignore Notes actions; only entering lectures updates the selected chapter.
  document.addEventListener('click', event => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const chapterCard = target.closest('.lt-ch');
    if (!chapterCard) return;
    const action = target.closest('.lt-act');
    if (action && !/^lectures$/i.test(action.textContent.trim())) return;
    const name = cleanHeading(chapterCard.querySelector('.lt-ch-name')?.textContent);
    const course = currentCourse();
    if (!name || !course) return;
    sessionStorage.setItem(SELECTED_CHAPTER_KEY,
      JSON.stringify({course, name, at:Date.now()}));
    sessionStorage.removeItem(SELECTED_LECTURE_KEY);
    console.log('[LectureUpload] Chapter selected:', name);
    say('📚 Chapter: '+name+'. Select a lecture number.');
  }, true);
  function trackedLectureTitle() {
    let info;
    try { info = JSON.parse(sessionStorage.getItem(SELECTED_LECTURE_KEY) || 'null'); }
    catch { return ''; }
    const heading = currentCourse();
    if (!info || !heading || info.course !== heading ||
        !Number.isInteger(info.number) || info.number < 1 ||
        Date.now() - info.at > 12 * 60 * 60 * 1000) return '';
    const sections = heading.split(/\s*[›>]\s*/).map(v => v.trim()).filter(Boolean);
    const chapter = trackedChapterName(heading);
    const name = [...sections, ...(chapter ? [chapter] : []),
      'Lecture ' + String(info.number).padStart(2, '0')].join(' | ');
    return name.slice(0, 120);
  }
  document.addEventListener('click', event => {
    const btn = event.target.closest?.('.lt-lec');
    if (!btn || !document.contains(btn)) return;
    lastLectureClickAt = performance.now();
    const num = Number(btn.querySelector('.lt-lec-num')?.textContent?.trim());
    const course = currentCourse();
    if (!Number.isInteger(num) || num < 1 || !course) return;
    sessionStorage.setItem(SELECTED_LECTURE_KEY,
      JSON.stringify({course, number:num, at:Date.now()}));
    console.log('[LectureUpload] Selected', course, 'Lecture', num);
    say('🎬 Selected: '+trackedLectureTitle());
  }, true);

  function autoLectureTitle(metadata, lectureId) {
    const selected = trackedLectureTitle();
    if (selected) return selected;
    const clean = value => String(value ?? '')
      .replace(/\s+/g, ' ').replace(/[\x00-\x1f]/g, ' ').trim()
      .replace(/\s*[|—–-]\s*(?:My\s*CA\s*Mate|CA\s*Mate|Foundation|Badassworks)\s*$/i, '');
    const generic = value => !value || value.length < 4 ||
      /^(?:CA\s*Mate|My\s*CA\s*Mate|Foundation|Badassworks|Telegram|Mini App|Lecture|Player|Video)(?:\s*[-|—–]\s*(?:Player|Video|Lecture))?$/i.test(value) ||
      /^[0-9]+$/.test(value);
    const candidates = [];
    const add = (value, score) => {
      if (typeof value !== 'string') return;
      const name = clean(value);
      if (!generic(name) && name.length <= 240) candidates.push({name, score});
    };

    // A title returned by the authorized lecture manifest is best.
    const objects = [metadata, metadata?.lecture, metadata?.video, metadata?.data];
    for (const obj of objects) {
      if (!obj || typeof obj !== 'object' || Array.isArray(obj)) continue;
      for (const key of ['lecture_title','lectureTitle','video_title','videoTitle','name','title'])
        add(obj[key], 110);
    }

    // In this Mini App the document title is sometimes only "CA Mate".
    // Look for the active lecture heading/breadcrumb instead.
    const selectors = [
      '[data-lecture-title]', '[data-testid*="lecture-title"]',
      '[class*="lectureTitle"]', '[class*="lecture-title"]',
      '[class*="videoTitle"]', '[class*="video-title"]',
      '[class*="lessonTitle"]', '[class*="lesson-title"]',
      '[aria-current="page"]', 'main h1', 'main h2', 'h1', 'h2',
      '.breadcrumb li:last-child', '[class*="breadcrumb"] [class*="active"]'
    ];
    for (const selector of selectors) {
      for (const el of document.querySelectorAll(selector)) {
        if (el.closest('#'+ID)) continue;
        const box = el.getBoundingClientRect();
        if (box.width <= 0 || box.height <= 0) continue;
        add(el.getAttribute('data-lecture-title') || el.textContent, 90);
      }
    }
    const metaTitle = document.querySelector('meta[property="og:title"]')?.content;
    add(metaTitle, 45);
    add(document.title, 35);
    try {
      add(new URLSearchParams(location.search).get('title'), 55);
      add(new URLSearchParams(location.search).get('lectureName'), 55);
    } catch {}
    candidates.sort((a,b)=>b.score-a.score);
    const best = candidates[0]?.name;
    if (best) {
      console.log('[LectureUpload] Auto title:', best, 'source score:', candidates[0].score);
      return best.slice(0,120);
    }
    // Never use generic CA Mate; preserve unique ID when title unavailable.
    return ('Lecture '+lectureId).slice(0,120);
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
      say('API key saved for this Mini App session only');
    }
  };
  workersButton.onclick=()=>{
    if(active) {
      say('Wait until the current browser-to-VPS transfer finishes to change workers.');
      return;
    }
    const entered=prompt('Parallel browser transfer workers (1-10). Recommended: 3-5. Applies to next lecture.', String(getWorkers()));
    if(entered===null)return;
    const value=Number(entered.trim());
    if(!Number.isInteger(value) || value < 1 || value > 10) {
      alert('Enter a whole number from 1 to 10.');
      return;
    }
    GM_setValue(WORKERS_KEY,value);
    setWorkersLabel();
    say('✅ Browser workers saved: '+value+'. Will apply to the next transfer.');
  };
  stop.onclick=()=>{
    cancel=true;
    for(const controller of sourceControllers) controller.abort();
    say('⏸ Pausing. Bytes already accepted by VPS will remain saved for resume.');
  };
  async function uploadPart(job, i, u, key, knownOffset=0) {
    // Stream the video into bounded 512-KiB VPS writes. No full-MP4 buffering.
    // For a resumed part we read from its start but skip the bytes VPS already has.
    // This is compatible even with origins that do not support HTTP Range.
    const controller=new AbortController();
    sourceControllers.add(controller);
    let reader;
    let received=0, offset=knownOffset;
    let buffer=[], bufferedBytes=0, headerBytes=0;
    const header=new Uint8Array(12);
    const mb=n=>(n/(1024*1024)).toFixed(2);
    const started=Date.now();
    let lastReport=0;
    const report=force=>{
      const now=Date.now();
      if(!force && now-lastReport<1500) return;
      lastReport=now;
      const seconds=Math.max(1,(now-started)/1000);
      say('⬇️ Part '+(i+1)+' | received '+mb(received)+' MB'
        +' | VPS '+mb(offset)+' MB | '+(received/1024/seconds).toFixed(1)+' KB/s');
    };
    const flush=async()=>{
      if(!bufferedBytes)return;
      if(cancel)return;
      const data=new Uint8Array(bufferedBytes);
      let at=0;
      for(const piece of buffer) {data.set(piece,at);at+=piece.byteLength;}
      buffer=[];bufferedBytes=0;
      const resp=await api('PUT','/browser-jobs/'+job+'/parts/'+i+'?offset='+offset,
                           key,data.buffer);
      if(!Number.isFinite(resp.offset) || resp.offset!==offset+data.byteLength)
        throw new Error('Unexpected VPS offset for part '+i);
      offset=resp.offset;
      report(true);
    };
    try {
      const response=await fetch(u,{signal:controller.signal,credentials:'omit'});
      if(!response.ok)throw new Error('Video part '+i+' HTTP '+response.status);
      if(!response.body)throw new Error('Streaming response unavailable in this WebView');
      reader=response.body.getReader();
      while(!cancel) {
        const {done,value}=await reader.read();
        if(done)break;
        if(!value?.byteLength)continue;
        const begin=received;
        if(headerBytes<12) {
          const n=Math.min(12-headerBytes,value.byteLength);
          header.set(value.subarray(0,n),headerBytes);
          headerBytes+=n;
          if(headerBytes>=8 && String.fromCharCode(...header.subarray(4,8))!=='ftyp')
            throw new Error('Invalid MP4 header for part '+i);
        }
        received+=value.byteLength;
        const skip=Math.max(0,Math.min(value.byteLength,knownOffset-begin));
        if(skip<value.byteLength) {
          buffer.push(value.subarray(skip));
          bufferedBytes+=value.byteLength-skip;
          if(bufferedBytes>=CHUNK)await flush();
        }
        report(false);
      }
      if(cancel)return false;
      if(received<12)throw new Error('Empty/invalid video part '+i);
      if(knownOffset>received)throw new Error('VPS saved offset exceeds part size');
      await flush();
      if(cancel)return false;
      if(offset!==received)throw new Error('Incomplete transfer: '+offset+'/'+received);
      await api('POST','/browser-jobs/'+job+'/parts/'+i+'/finish',key,{size:received});
      report(true);
      return true;
    } catch(e) {
      if(cancel && (e.name==='AbortError'||e.name==='TypeError'))return false;
      throw e;
    } finally {
      sourceControllers.delete(controller);
      if(reader) {try {await reader.cancel();} catch {}}
    }
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
      const manifest=await waitForManifest();
      if(!manifest) throw new Error('No fresh signed video request detected. Close player, reopen the lecture and press Play, then retry. Offline-only playback may not request a manifest.');
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
          if(state.total_parts!==count) {
            job='';state=null;
          } else if(['queued','merging','uploading','complete'].includes(state.phase)) {
            updateStoredJob(job, {phase:state.phase, name:state.title||'Lecture '+lectureId});
            showQueue();
            say('✅ This lecture is already '+state.phase+'. Open the NEXT lecture and press Upload Lecture.');
            return;
          } else if(!['receiving','failed'].includes(state.phase)) {
            throw new Error('Unexpected job state: '+state.phase);
          }
        } catch(e) {
          if(String(e.message).includes('HTTP 404')) {job='';state=null;}
          else throw e;
        }
      }
      if(!job) {
        // Prefer real lecture name from manifest or active heading.
        const lectureTitle=autoLectureTitle(metadata,lectureId);
        say('🎬 Detected lecture: '+lectureTitle);
        state=await api('POST','/browser-jobs',key,{
          lecture_id:lectureId,total_parts:count,
          title:lectureTitle
        });
        job=state.job_id;
        GM_setValue(keyName,job);
        rememberJob(job,lectureTitle,lectureId);
        showQueue();
      }
      if(!storedJobs().some(j=>j.id===job))
        rememberJob(job,state.title||'Lecture '+lectureId,lectureId);
      const already=new Set(state.completed_parts||[]);
      const pending=Array.from({length:count},(_,i)=>i).filter(i=>!already.has(i));
      const parallelWorkers=getWorkers();
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
            say('⬇️ '+(uploaded)+'/'+count+' saved | Browser workers: '+parallelWorkers);
            const ok=await uploadPart(job,i,u.toString(),key,Number(state.offsets?.[String(i)]||0));
            if(!ok) return;
            uploaded++;
            say('✅ '+uploaded+'/'+count+' parts | '+speed()+' elapsed');
          } catch(e){
            if(cancel && e.name==='AbortError')return;
            failed=e;
            cancel=true;
            for(const controller of sourceControllers)controller.abort();
            return;
          }
        }
      }
      await Promise.all(Array.from({length:Math.min(parallelWorkers,pending.length)},()=>worker()));
      if(failed) throw failed;
      if(cancel) {say('Paused. Reopen lecture and click Upload Lecture to resume.');return;}
      // /complete only queues the job; it never waits for Telegram delivery.
      const queued = await api('POST','/browser-jobs/'+job+'/complete',key,{});
      updateStoredJob(job,{phase:queued.phase,percent:0});
      showQueue();
      say('✅ Saved on VPS and queued for Telegram! Open the NEXT lecture now and press Upload Lecture. No need to wait for Telegram.');
    } catch(e) {
      say('❌ '+e.message);
      alert('Lecture transfer stopped: '+e.message);
    } finally {
      active=false;send.disabled=false;
      showQueue();
    }
  };
})();
