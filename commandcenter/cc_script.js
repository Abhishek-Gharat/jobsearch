
"use strict";
const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const NA='<span class="unavail" title="Not produced by the existing system">unavailable</span>';
function api(path,opts){return fetch(path,opts?{headers:{"Content-Type":"application/json"},...opts}:undefined).then(async r=>{let d=null;try{d=await r.json()}catch(e){} return {ok:r.ok,status:r.status,data:d||{error:"http_"+r.status}}})}
function toast(m,ms=4500){const t=document.createElement("div");t.className="toast";t.textContent=m;$("#toasts").appendChild(t);setTimeout(()=>{t.classList.add("hide");setTimeout(()=>t.remove(),350)},ms)}
function fmtD(s){if(!s)return"—";const d=new Date(s);return isNaN(d)?String(s):d.toLocaleDateString(undefined,{day:"numeric",month:"short",year:"numeric"})}
function fmtDT(s){if(!s)return"—";const d=new Date(s);return isNaN(d)?String(s):d.toLocaleString(undefined,{day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"})}
const bdg=s=>`<span class="bdg ${esc(String(s||"neutral"))}">${esc(String(s||"—").replace(/_/g," "))}</span>`;
function stagger(sel){
  document.querySelectorAll(sel).forEach((el,i)=>{
    if(el.dataset.animated)return; el.dataset.animated="1";
    el.style.opacity="0"; el.style.transform="translateY(8px)";
    el.style.transition="opacity .35s var(--ease), transform .35s var(--ease)";
    el.style.transitionDelay=Math.min(i*28,360)+"ms";
    requestAnimationFrame(()=>requestAnimationFrame(()=>{el.style.opacity="1";el.style.transform="none";
      setTimeout(()=>{el.style.transition="";el.style.transitionDelay="";},600+i*28)}));
  });
}
function avatar(name){const h=[...String(name||"?")].reduce((a,c)=>(a*31+c.charCodeAt(0))>>>0,7)%360;
  return `<span class="avatar" style="background:hsl(${h},52%,44%)">${esc(String(name||"?").trim()[0]?.toUpperCase()||"?")}</span>`}

/* ---------------- navigation registry ---------------- */
const NAV=[
 ["overview","Overview"],["applications","Applications"],["priority","Priority Jobs"],
 ["discovery","Job Discovery"],["outreach","Recruiter Outreach"],["gmail","Gmail"],
 ["interviews","Interviews"],["assessments","Assessments"],["followups","Follow-ups"],
 ["analytics","Analytics"],["resume","Resume Intelligence"],["companies","Companies"],
 ["health","Automation Health"]];
const TITLES=Object.fromEntries(NAV);

let OVERVIEW_CACHE=null;

function route(){
  let h=location.hash.replace(/^#/,"")||"overview";
  const [page,arg]=h.split("/");
  const known=NAV.some(n=>n[0]===page)||page==="application";
  renderNav(page==="application"?"applications":page);
  $("#crumb").textContent=TITLES[page]||(page==="application"?"Application":page);
  const v=$("#view");
  v.innerHTML=`<div class="skel"></div><div class="skel"></div>`;
  if(page==="application")return openApplication(arg,true);
  Promise.resolve((PAGES[page]||PAGES.overview)(v,arg))
    .then(()=>stagger(".card, .kpi"))
    .catch(e=>{v.innerHTML=`<div class="errbox">Failed to load: ${esc(e.message)}</div>`});
}
function renderNav(active){
  $("#nav").innerHTML=NAV.map(([k,l])=>{
    let ic={overview:"M3 12h7V3H3zM14 21h7v-9h-7zM14 8h7V3h-7zM3 21h7v-6H3z",
      applications:"M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
      priority:"m12 2 3 7h7l-5.5 4.5L18 21l-6-4-6 4 1.5-7.5L2 9h7z",
      discovery:"circle:11 11 8|arrow",gmail:"M4 4h16v16H4zM4 7l8 6 8-6",
      interviews:"M8 2v4M16 2v4M3 9h18M5 5h14a2 2 0 0 1 2 2v13H3V7a2 2 0 0 1 2-2z",
      assessments:"M9 11l3 3 8-8M21 12v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h11",
      followups:"M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
      analytics:"M18 20V10M12 20V4M6 20v-6"}[k]||"M3 12h18";
    if(k==="discovery")ic="M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm10 2-4.3-4.3";
    if(k==="outreach")ic="M22 2 11 13M22 2 15 22l-4-9-9-4z";
    if(k==="resume")ic="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M9 15h6M9 11h3";
    if(k==="companies")ic="M3 21h18M5 21V7l7-4 7 4v14M9 21v-4h6v4M9 11h.01M15 11h.01";
    if(k==="health")ic="M22 12h-4l-3 9L9 3l-3 9H2";
    if(k==="gmail")ic="M4 4h16v16H4zM4 7l8 6 8-6";
    return `<a href="#${k}" class="${k===active?"active":""}" title="${l}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${ic}"/></svg>
      <span class="txt">${l}</span></a>`}).join("");
}
window.addEventListener("hashchange",route);

/* ---------------- shared UI ---------------- */
function kpi(v,l,cls="",title=""){return `<div class="kpi ${cls}" ${title?`title="${esc(title)}"`:""}><div class="v">${v==null?"—":v}</div><div class="l">${esc(l)}${v==null?' <span class="unavail">unavailable</span>':""}</div></div>`}
function card(title,bodyHTML,extra=""){return `<section class="card"><div class="hd">${title}${extra}</div><div class="bd">${bodyHTML}</div></section>`}
function openDrawer(title,html){$("#drawer-title").textContent=title;$("#drawer-body").innerHTML=html;$("#drawer").classList.add("open");$("#drawer-bg").classList.add("open")}
window.closeDrawer=function(){$("#drawer").classList.remove("open");$("#drawer-bg").classList.remove("open");if(location.hash.startsWith("#application/"))history.replaceState(null,"","#applications")}
$("#drawer-bg").onclick=closeDrawer;
function dlgOpen(title,html){$("#dlg-title").textContent=title;$("#dlg-body").innerHTML=html;$("#dlg").showModal()}
window.dlgClose=()=>$("#dlg").close();

/* ================= PAGE: OVERVIEW ================= */
async function pgOverview(view){
  const {ok,data}=await api("/api/overview");
  if(!ok)throw new Error(data.error||"load failed");
  OVERVIEW_CACHE=data;
  updateSupPill(data.system.supervisor);
  const k=data.kpis;
  view.innerHTML=`
   <div class="pagehead"><h1>Good ${new Date().getHours()<12?"morning":new Date().getHours()<17?"afternoon":"evening"}, Alex</h1>
   <p>Everything below is generated from your existing engine data · refreshed ${fmtDT(data.generated_at)}</p></div>

   <section class="card in" style="border-color:#fecfc9">
     <div class="hd" style="color:var(--red)">Action required <span class="bdg neutral">${data.actions.length} item${data.actions.length===1?"":"s"}</span></div>
     <div class="bd" style="padding-top:4px">
      ${data.actions.length?data.actions.map(a=>`
        <div class="act-item" onclick="location.hash='${a.link.replace(/^#/,"")}'">
          <span class="pb ${a.priority}">${a.priority}</span>
          <div><b>${esc(a.title)}</b><span>${esc(a.detail)}</span></div>
          <span class="spacer"></span><span style="color:var(--ink3)">›</span>
        </div>`).join(""):`<div class="empty">Nothing needs you right now.</div>`}
     </div>
   </section>

   <div class="kpis">
     ${kpi(k.submitted,"Submitted","good")}
     ${kpi(k.applications_today!=null?k.applications_today:null,"Applied today","",k._labels?.applications_today)}
     ${kpi(k.applications_this_week,"This week","",k._labels?.applications_today)}
     ${kpi(k.pending+k.in_progress,"Pending queue")}
     ${kpi(k.review_required,"Review needed",k.review_required?"warn":"")}
     ${kpi(k.recruiter_replies,"Recruiter replies",k.recruiter_replies?"good":"")}
     ${kpi(k.followups_due_today,"Follow-ups due today",k.followups_due_today?"warn":"")}
     ${kpi(k.new_jobs_discovered_week,"Discovered (week)")}
     ${kpi(k.interview_kits,"Interview kits ready")}
     ${kpi(null,"Scheduled interviews","",k._labels?.scheduled_interviews)}
     ${kpi(k.assessments_pending,"Assessments pending")}
     ${kpi(k.offers,"Offers")}
   </div>

   ${card(`System status`,`
     <div class="sysgrid">
       ${sysItem("Supervisor",data.system.supervisor,data.system.supervisor==="running"?"ok":"bad")}
       ${sysItem("Phase",data.system.phase||"—")}
       ${sysItem("Worker",data.system.worker_id||"—")}
       ${sysItem("Current job",(data.system.current_job&&data.system.current_job.step)||"—")}
       ${sysItem("Current batch",data.system.current_batch||"—")}
       ${sysItem("Queue size",data.system.queue_size)}
       ${sysItem("Heartbeat age",data.system.heartbeat_age_sec!=null?Math.round(data.system.heartbeat_age_sec)+"s":NA)}
       ${sysItem("Watchdog",data.system.watchdog_state||"—")}
       ${sysItem("Gmail last sync",data.system.gmail_last_sync?fmtDT(data.system.gmail_last_sync):"—")}
       ${sysItem("BrowserOS",null,"","not tracked by the existing system")}
       ${sysItem("Last discovery",data.system.last_discovery?fmtDT(data.system.last_discovery):"—")}
       ${sysItem("Last Gmail scan",data.system.gmail_last_scan?fmtDT(data.system.gmail_last_scan):"—")}
     </div>`,
     `<span class="spacer"></span><a class="btn sm" href="#health">Details</a>`)}
  `;
  stagger(".kpi");
  pollWhileVisible(()=>location.hash.replace(/^#/,"").split("/")[0]==="overview",pgOverviewRefresh,20000);
}
function sysItem(k,v,cls="",title=""){
  let val=v==null?NA:(cls==="ok"||cls==="bad"?`<span class="pill ${cls}"><span class="led"></span>${esc(v)}</span>`:esc(v));
  return `<div class="sysitem" ${title?`title="${esc(title)}"`:""}><div class="k">${esc(k)}</div><div class="v">${val}</div></div>`;
}
async function pgOverviewRefresh(){
  try{
    const {ok,data}=await api("/api/overview"); if(!ok)return;
    OVERVIEW_CACHE=data; updateSupPill(data.system.supervisor);
    const acts=$("#view .action-panel, #view section.card");
    // lightweight refresh: re-render only action list counts via bell
    updateBell(data.actions.filter(a=>["P0","P1"].includes(a.priority)).length,
      data.actions.filter(a=>["P0","P1"].includes(a.priority)));
  }catch(e){}
}
function updateSupPill(state){$("#sup-txt").textContent=state==="running"?"Automation running":"Automation stopped";$("#sup-pill").className="pill "+(state==="running"?"ok":"bad")}

/* ================= PAGE: APPLICATIONS ================= */
let APP_DATA=null;
async function pgApplications(view){
  const {ok,data}=await api("/api/applications");if(!ok)throw new Error(data.error);
  APP_DATA=data.rows;
  const filters=["all","submitted","pending","review_required","in_progress","skipped","failed"];
  view.innerHTML=`
   <div class="pagehead"><h1>Applications</h1><p>${data.rows.length} tracked jobs · click a row for the full history</p></div>
   ${card("",`
     <div class="toolbar" style="border:none;padding:0 0 12px">
       <input type="text" id="app-q" placeholder="Search company, role, ID, URL…" style="flex:1;min-width:180px">
       <div class="chips" id="app-filters">${filters.map(f=>`<button class="fchip" data-f="${f}" aria-pressed="${f==="all"}">${f.replace(/_/g," ")}</button>`).join("")}</div>
     </div>
     <div class="tblwrap"><table id="app-tbl"><thead><tr>
       <th>Company</th><th>Role</th><th>Portal</th><th>Status</th><th>Last activity</th><th>Resume</th><th>Gmail ✓</th><th>Evidence</th><th></th>
     </tr></thead><tbody></tbody></table></div>`)}
  `;
  let filter="all";
  const draw=()=>{
    const q=($("#app-q").value||"").toLowerCase();
    const rows=APP_DATA.filter(r=>filter==="all"||r.status===filter)
      .filter(r=>!q||["company","role","id","url"].some(k=>String(r[k]||"").toLowerCase().includes(q)));
    $("#app-tbl tbody").innerHTML=rows.length?rows.map(r=>{
      const ev=r.evidence||{};
      const evSnip=ev.confirmation||ev.notes||ev.failureReason||"";
      return `<tr class="rowclick" onclick="openApplication('${r.id}')">
        <td><span class="company-cell">${avatar(r.company)}<span><span class="cname">${esc(r.company)}</span><span class="sub">${esc(r.location||"")}</span></span></span></td>
        <td>${esc(r.role)}</td>
        <td>${esc(r.portal||"—")}</td>
        <td>${bdg(r.status)}</td>
        <td>${fmtD(r.updated_at)}</td>
        <td>${esc(r.resume_variant)}<span class="sub">${esc(r.resume_source)}</span></td>
        <td>${r.gmail_confirmed?`<span title="${esc(r.gmail_confirmed_at||"")}">✅</span>`:"—"}</td>
        <td>${evSnip?`<span class="evid-snip" title="${esc(Object.values(ev).join(" | "))}">${esc(evSnip)}</span>`:"—"}</td>
        <td>${r.url?`<a href="${esc(r.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">↗</a>`:""}</td>
      </tr>`}).join(""):`<tr><td colspan="9"><div class="empty">No applications match.</div></td></tr>`;
  };
  $("#app-q").oninput=draw;
  $("#app-filters").onclick=e=>{const b=e.target.closest(".fchip");if(!b)return;
    filter=b.dataset.f;document.querySelectorAll("#app-filters .fchip").forEach(x=>x.setAttribute("aria-pressed",x.dataset.f===filter));draw()};
  draw();
}
window.openApplication=async function(id,noHash){
  if(!noHash&&location.hash!=="#application/"+id){location.hash="#application/"+id;return}
  if(!APP_DATA){await api("/api/applications").then(({data})=>APP_DATA=data.rows)}
  const r=APP_DATA.find(x=>x.id===id);
  if(!r){toast("Application "+id+" not found");return}
  const fu=(await api("/api/followups")).data;
  const fuItems=[...(fu.overdue||[]),...(fu.due_today||[]),...(fu.upcoming||[])].filter(i=>i.job_id===id);
  const timeline=[
    r.updated_at&&["Last engine update",fmtDT(r.updated_at)],
    r.gmail_confirmed_at&&["Gmail confirmation",fmtDT(r.gmail_confirmed_at)],
  ].filter(Boolean);
  openDrawer(`${r.company} — ${r.role}`,`
    <div class="sect"><h4>Original job</h4>
      <dl class="kv">
        <dt>Job ID</dt><dd>${esc(r.id)}</dd>
        <dt>Portal</dt><dd>${esc(r.portal||"—")}</dd>
        <dt>Location</dt><dd>${esc(r.location||"—")}</dd>
        <dt>URL</dt><dd>${r.url?`<a href="${esc(r.url)}" target="_blank" rel="noopener">open job ↗</a>`:"—"}</dd>
        <dt>Match score</dt><dd>${r.match_score??"—"}</dd>
      </dl></div>
    <div class="sect"><h4>Status & outcome</h4>
      <dl class="kv"><dt>Status</dt><dd>${bdg(r.status)}</dd>
      <dt>Outcome</dt><dd>${r.response_detected?("response "+fmtD(r.response_detected)):(r.status==="submitted"?"awaiting response":"—")}</dd></dl></div>
    <div class="sect"><h4>Timeline</h4>
      ${timeline.length?timeline.map(([k,v])=>`<div class="kv"><dt>${k}</dt><dd>${v}</dd></div>`).join(""):`<span class="unavail">unavailable — engine records only the last update per job</span>`}
    </div>
    <div class="sect"><h4>Resume used</h4>${bdg(r.resume_variant)} <span class="sub">${r.resume_source==="outcome"?"recorded in outcomes.json":r.resume_source==="selection"?"chosen by resume intelligence":"recommended (no recorded selection)"}</span></div>
    <div class="sect"><h4>Submission evidence</h4>
      ${r.evidence&&Object.keys(r.evidence).length?`<div class="evid">${esc(JSON.stringify(r.evidence,null,1))}</div>`:`<span class="unavail">none recorded</span>`}
      ${r.error?`<div class="errbox" style="margin-top:8px">${esc(String(r.error))}</div>`:""}
    </div>
    <div class="sect"><h4>Gmail confirmation</h4>${r.gmail_confirmed?`✅ confirmed ${fmtDT(r.gmail_confirmed_at)}`:`<span class="unavail">no confirmation email indexed</span>`}</div>
    <div class="sect"><h4>Recruiter outreach</h4>${r.outreach_status?`${bdg(r.outreach_status)}${r.outreach_reply?` · reply: ${bdg(r.outreach_reply)}`:""}`:`<span class="unavail">no outreach yet</span>`}</div>
    <div class="sect"><h4>Follow-up history</h4>${fuItems.length?fuItems.map(i=>`Day-${i.day} · due ${fmtD(i.due_date)} · ${bdg(i.status)}`).join("<br>"):`<span class="unavail">none scheduled</span>`}</div>
    ${r.kit_slug?`<div class="sect"><h4>Interview kit</h4><button class="btn" onclick="openKit('${r.kit_slug}')">Open interview kit</button></div>`:""}
  `);
}

/* ================= PAGE: PRIORITY JOBS ================= */
let PRIO_DATA=[];
async function pgPriority(view){
  const {ok,data}=await api("/api/priority");if(!ok)throw new Error(data.error);
  PRIO_DATA=data.rows;
  view.innerHTML=`<div class="pagehead"><h1>Priority Jobs</h1><p>Ranked from pending queue × hiring intelligence · ${esc(data.formula)} · click any row for full job intel</p></div>
  ${card("",`<div class="tblwrap"><table><thead><tr>
    <th>#</th><th>Company</th><th>Role</th><th>Match</th><th>Hiring score</th><th>Freshness</th><th>Location</th><th>ATS</th><th>Resume rec.</th><th>Priority</th><th></th>
  </tr></thead><tbody>
  ${data.rows.map((r,i)=>`<tr class="rowclick" onclick="openPriority(${i})">
    <td>${i+1}</td>
    <td><span class="company-cell">${avatar(r.company)}<span><span class="cname">${esc(r.company)}</span>${r.tier===1?' <span title="tier 1 company" style="color:var(--orange)">★</span>':""}<span class="sub">${esc(r.id)}</span></span></span></td>
    <td>${esc(r.role)}${r.notes?`<span class="evid-snip" title="${esc(r.notes)}">${esc(r.notes)}</span>`:""}</td>
    <td><b>${r.match_score||"—"}</b></td>
    <td>${r.hiring_score||`<span class="unavail" title="company not in the ATS-scan universe">—</span>`}</td>
    <td>${r.freshness_days!=null?r.freshness_days+"d avg":'<span class="unavail" title="no ATS postings scanned for this company">n/a</span>'}</td>
    <td>${esc(r.location||"—")}</td><td>${esc(r.ats||"—")}</td>
    <td><span class="bdg neutral">${esc(r.resume_recommendation)}</span></td>
    <td><b style="color:var(--accent)">${r.priority_score}</b></td>
    <td>${r.url?`<a class="btn sm" href="${esc(r.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Open ↗</a>`:""}</td>
  </tr>`).join("")||`<tr><td colspan="11"><div class="empty">No pending jobs.</div></td></tr>`}
  </tbody></table></div>`)}
  `;
}
window.openPriority=async function(i){
  const r=PRIO_DATA[i];if(!r)return;
  if(!APP_DATA){await api("/api/applications").then(({data})=>APP_DATA=data.rows)}
  const history=(APP_DATA||[]).filter(a=>normC(a.company)===normC(r.company)&&a.id!==r.id);
  const chip=(on,label)=>`<span class="minichip ${on?"hot":""}">${on?"✓ ":""}${label}</span>`;
  openDrawer(`${r.company} — ${r.role}`,`
    <div class="sect"><h4>Fit breakdown</h4>
      <div class="scorebig">
        <div class="sb"><div class="n">${r.priority_score}</div><div class="t">Priority</div></div>
        <div class="sb"><div class="n">${r.match_score||"—"}</div><div class="t">Profile match</div></div>
        <div class="sb"><div class="n">${r.hiring_score||(r.in_universe?"0":NA)}</div><div class="t">Hiring score</div></div>
      </div>
      ${!r.in_universe?`<div class="whyline">Hiring score & freshness unavailable — this job came from LinkedIn discovery and the company is not yet in the ATS-scan universe. Rank is driven by profile match.</div>`:""}
      ${r.in_universe&&r.freshness_days==null?`<div class="whyline">No recent ATS postings scanned for this company, so freshness is not scored.</div>`:""}
    </div>
    <div class="sect"><h4>Job</h4>
      <dl class="kv">
        <dt>Job ID</dt><dd>${esc(r.id)}</dd>
        <dt>Location</dt><dd>${esc(r.location||"—")}</dd>
        <dt>ATS / portal</dt><dd>${esc(r.ats||"—")}</dd>
        <dt>Queued</dt><dd>${fmtD(r.updated_at)}</dd>
        ${r.url?`<dt>Posting</dt><dd><a href="${esc(r.url)}" target="_blank" rel="noopener">open job ↗</a></dd>`:""}
      </dl></div>
    <div class="sect"><h4>Company intelligence</h4>
      ${r.in_universe?`
        <div class="minichips">
          ${chip(r.tier===1,"tier "+(r.tier??"?"))}
          ${chip(r.is_hiring,"hiring now")}
          ${chip(r.india,"India hiring")}
          ${chip(r.remote,"remote hiring")}
          ${chip(r.endpoint_verified,"ATS endpoint verified")}
        </div>
        <div class="minichips" style="margin-top:6px">
          <span class="minichip">${r.postings_seen} postings seen</span>
          ${r.fresh_7d?`<span class="minichip hot">${r.fresh_7d} fresh this week</span>`:""}
          ${r.react_seen?`<span class="minichip">react ${r.react_seen}</span>`:""}
          ${r.nextjs_seen?`<span class="minichip">next.js ${r.nextjs_seen}</span>`:""}
          ${r.fullstack_seen?`<span class="minichip">full-stack ${r.fullstack_seen}</span>`:""}
          ${r.frontend_seen?`<span class="minichip">frontend ${r.frontend_seen}</span>`:""}
          ${r.mern_seen?`<span class="minichip">mern ${r.mern_seen}</span>`:""}
          ${r.js_seen?`<span class="minichip">javascript ${r.js_seen}</span>`:""}
        </div>
        <div class="kv" style="margin-top:8px">
          <dt>Last scan</dt><dd>${fmtDT(r.last_scan)}</dd>
          <dt>Your pipeline</dt><dd>${r.company_discovered??0} discovered · ${r.company_submitted??0} submitted</dd>
        </div>`
      :`<div class="whyline">No scan data yet — the discovery pipeline has not reached this company's careers page.</div>`}
    </div>
    <div class="sect"><h4>Resume recommendation</h4>
      <span class="bdg neutral">${esc(r.resume_recommendation)}</span>
      ${r.resume_why?`<div class="whyline">${esc(r.resume_why)}</div>`:`<div class="whyline">Suggested from role keywords — engine will confirm via resume intelligence at apply time.</div>`}
    </div>
    ${r.notes?`<div class="sect"><h4>Job notes</h4><div class="evid">${esc(r.notes)}</div></div>`:""}
    ${r.evidence&&Object.keys(r.evidence).length?`<div class="sect"><h4>Raw evidence</h4><div class="evid">${esc(JSON.stringify(r.evidence,null,1))}</div></div>`:""}
    ${history.length?`<div class="sect"><h4>Your history with this company</h4>
      ${history.map(a=>`<div style="display:flex;gap:8px;align-items:center;padding:4px 0;font-size:13px">${bdg(a.status)} <span>${esc(a.role)}</span><span class="sub">${esc(a.id)}</span></div>`).join("")}</div>`:""}
    <div class="sect" style="display:flex;gap:8px;align-items:center">
      ${r.url?`<a class="btn primary" href="${esc(r.url)}" target="_blank" rel="noopener">Open job &amp; apply ↗</a>`:""}
      <span style="font-size:11.5px;color:var(--ink3)">The engine auto-applies to pending jobs while the supervisor runs — this opens the posting for manual review.</span>
    </div>
  `);
}

/* ================= PAGE: DISCOVERY ================= */
async function pgDiscovery(view){
  const {ok,data}=await api("/api/discovery");if(!ok)throw new Error(data.error);
  const m=data.metrics;
  const bars=obj=>Object.entries(obj||{}).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`
    <div class="hbar"><span>${esc(k)}</span><div class="track"><div class="fill" style="width:${Math.round(100*v/Math.max(...Object.values(obj)))}%"></div></div><span class="num">${v}</span></div>`).join("");
  view.innerHTML=`<div class="pagehead"><h1>Job Discovery</h1><p>Produced by harvest_ats + discovery pipeline · window: ${esc(m.window||"—")}</p></div>
   <div class="kpis">
     ${kpi(m.jobs_discovered_total,"Jobs discovered")}
     ${kpi(m.companies_scanned,"Companies scanned (snapshot)")}
     ${kpi(m.with_matches,"Companies with matches")}
     ${kpi(m.universe_size,"Universe size")}
     ${kpi(m.verified_endpoints,"Verified ATS endpoints")}
   </div>
   <div class="grid2">
     ${card("Jobs by source",bars(m.by_source))}
     ${card("Jobs by portal",bars(m.by_portal))}
   </div>
   ${card("Recent discoveries (pending queue)",`
     <div class="toolbar" style="border:none;padding:0 0 10px">
       <input type="text" id="d-q" placeholder="Filter location / company…" style="min-width:170px">
       <select id="d-portal"><option value="">All portals</option>${[...new Set(data.recent.map(r=>r.portal).filter(Boolean))].map(p=>`<option>${esc(p)}</option>`).join("")}</select>
       <select id="d-role"><option value="">All role types</option><option>react</option><option>nextjs</option><option>fullstack/mern</option><option>frontend</option><option>javascript</option></select>
       <select id="d-age"><option value="">Any age</option><option value="1">≤ 1 day*</option><option value="3">≤ 3 days*</option><option value="7">≤ 7 days*</option></select>
       <span class="sub" style="font-size:11px;color:var(--ink3)">*by engine update time — true posting age unavailable</span>
     </div>
     <div class="tblwrap"><table><thead><tr><th>Company</th><th>Role</th><th>Location</th><th>Portal</th><th>Updated</th><th></th></tr></thead>
     <tbody id="d-body"></tbody></table></div>`)}
   ${card("Scan skip reasons (why some companies yield nothing)",Object.entries(m.skip_reasons||{}).map(([k,v])=>`<span class="bdg neutral" style="margin:2px">${esc(k)}: ${v}</span>`).join(" ")||"<span class='empty'>none recorded</span>")}
  `;
  const draw=()=>{
    const q=($("#d-q").value||"").toLowerCase(),po=$("#d-portal").value,ro=$("#d-role").value,ag=$("#d-age").value;
    const now=Date.now();
    const rows=data.recent.filter(r=>{
      if(q&&!((r.location||"")+(r.company||"")).toLowerCase().includes(q))return false;
      if(po&&r.portal!==po)return false;
      if(ro&&!(r.title||"").toLowerCase().replace(/[^a-z]/g,"").includes(ro.split("/")[0].replace(/[^a-z]/g,"")))return false;
      if(ag){const t=new Date(r.updated_at).getTime();if(isNaN(t)||now-t>ag*864e5)return false}
      return true});
    $("#d-body").innerHTML=rows.map(r=>`<tr><td><b>${esc(r.company)}</b></td><td>${esc(r.title)}</td><td>${esc(r.location||"—")}</td><td>${esc(r.portal)}</td><td>${fmtD(r.updated_at)}</td><td>${r.url?`<a class="btn sm" target="_blank" rel="noopener" href="${esc(r.url)}">↗</a>`:""}</td></tr>`).join("")||`<tr><td colspan="6"><div class="empty">No matches.</div></td></tr>`;
  };
  ["d-q","d-portal","d-role","d-age"].forEach(id=>$("#"+id).oninput=draw);
  draw();
}

/* ================= PAGE: RECRUITER OUTREACH ================= */
let OSU_STATE={records:[],mailConfigured:false,dryrun:false};
async function pgOutreach(view){
  const {status,data}=await api("/api/outreach/outreach");
  if(status===503){view.innerHTML=`<div class="errbox"><b>Outreach server offline.</b> Start it in another terminal:<br><code>cd D:\\newjobs\\outreach && python recruiter_outreach.py serve</code><br><br>This Command Center never sends emails itself — every send is proxied to the approved outreach service which enforces the 12/day cap and 4-minute gap.</div>`;return}
  if(!ok2(status))throw new Error(data.error||"failed");
  OSU_STATE=data;
  const parents=data.records.filter(r=>r.kind!=="followup");
  view.innerHTML=`<div class="pagehead"><h1>Recruiter Outreach</h1><p>Draft → review → manual send. All protections of the existing outreach server apply.</p></div>
   <div class="kpis">
     ${kpi(`${data.sent_today}/${data.daily_limit}`,"Sent today",data.sent_today>=data.daily_limit?"bad":"good")}
     ${kpi(data.replied,"Replies")}
     ${kpi(parents.filter(p=>!p.message_id).length,"Drafts / awaiting send")}
   </div>
   ${card("",`<div class="tblwrap"><table><thead><tr>
     <th>Recruiter</th><th>Company / Role</th><th>Email</th><th>Application</th><th>Status</th><th>Sent</th><th>Reply</th><th>Next follow-up</th><th>Draft</th><th></th>
   </tr></thead><tbody>
   ${parents.map(r=>`<tr>
     <td><b>${esc(r.recruiter_name||"—")}</b></td>
     <td>${esc(r.company)}<span class="sub">${esc(r.job_title||"")}</span></td>
     <td>${esc(r.recruiter_email||"—")}</td>
     <td>${esc(r.job_id||"—")}<span class="sub">${esc(r.application_status||"")}</span></td>
     <td>${bdg(r.display_status||r.status)}</td>
     <td>${r.sent_at?fmtDT(r.sent_at):"—"}</td>
     <td>${r.reply_status?bdg(r.reply_status):"—"}</td>
     <td>${r.next_fu_due?fmtD(r.next_fu_due):(r.followup_due&&r.status==="sent"?fmtD(r.followup_due):"—")}</td>
     <td>${r.draft?`<button class="btn sm" onclick="osuDraft('${r.id}')">View</button>`:"—"}</td>
     <td style="white-space:nowrap">
       ${r.message_id?`<span class="bdg submitted">sent ✓</span>`:`
         <button class="btn sm" onclick="osuEdit('${r.id}')">Edit</button>
         <button class="btn sm primary" onclick="osuSend('${r.id}')" ${r.recruiter_email&&r.draft?"":"disabled"}>Send</button>
         <button class="btn sm danger" onclick="osuSkip('${r.id}')">Skip</button>`}
     </td></tr>`).join("")||`<tr><td colspan="10"><div class="empty">No recruiter records yet — seed them from the outreach dashboard.</div></td></tr>`}
   </tbody></table></div>`)}
  `;
}
const ok2=s=>s>=200&&s<300;
window.osuDraft=function(id){const r=OSU_STATE.records.find(x=>x.id===id);if(!r)return;
  dlgOpen("Draft — "+(r.company||id),`<pre class="md" style="max-height:50vh;overflow:auto">${esc(r.draft||"(no draft)")}</pre>`)};
window.osuSkip=async function(id){if(!confirm("Mark as skipped?"))return;const {ok,data}=await api("/api/outreach/skip",{method:"POST",body:JSON.stringify({id})});toast(ok?"Skipped.":"Skip failed: "+(data.error||""));pgOutreach($("#view"))};
window.osuEdit=function(id){const r=OSU_STATE.records.find(x=>x.id===id);if(!r)return;
  dlgOpen("Edit — "+r.company,`
   <label style="font-size:11px;font-weight:700;color:var(--ink2)">RECRUITER NAME</label><input id="oe-n" type="text" style="width:100%" value="${esc(r.recruiter_name)}">
   <label style="font-size:11px;font-weight:700;color:var(--ink2)">EMAIL</label><input id="oe-e" type="text" style="width:100%" value="${esc(r.recruiter_email)}">
   <label style="font-size:11px;font-weight:700;color:var(--ink2)">SUBJECT</label><input id="oe-s" type="text" style="width:100%" value="${esc(r.subject)}">
   <label style="font-size:11px;font-weight:700;color:var(--ink2)">DRAFT</label><textarea id="oe-d" style="width:100%;min-height:200px">${esc(r.draft)}</textarea>
   <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
     <button class="btn" onclick="osuRegen('${r.id}')">Regenerate from profile</button>
     <button class="btn primary" onclick="osuSave('${r.id}')">Save</button>
   </div>`)};
window.osuSave=async function(id){
  const body={id,recruiter_name:$("#oe-n").value,recruiter_email:$("#oe-e").value.trim(),subject:$("#oe-s").value,draft:$("#oe-d").value};
  const {ok,data}=await api("/api/outreach/draft",{method:"POST",body:JSON.stringify(body)});
  toast(ok?"Saved.":"Save failed.");dlgClose();pgOutreach($("#view"))};
window.osuRegen=async function(id){
  await api("/api/outreach/draft",{method:"POST",body:JSON.stringify({id,recruiter_name:$("#oe-n").value,recruiter_email:$("#oe-e").value.trim(),generate:true})});
  toast("Regenerated.");dlgClose();pgOutreach($("#view"))};
window.osuSend=async function(id){
  const pf=await api(`/api/outreach/preflight?id=${encodeURIComponent(id)}`);
  if(pf.status===503)return toast("Outreach server offline.");
  if(!pf.ok){toast(pf.data.error==="already_sent"?"Already sent — never re-sends.":"Preflight failed.");return}
  const p=pf.data,dup=p.duplicate||{};
  dlgOpen("Review before sending",`
   <dl class="kv"><dt>To</dt><dd><b>${esc(p.to)}</b></dd><dt>Subject</dt><dd>${esc(p.subject)}</dd></dl>
   <pre class="md" style="max-height:240px;overflow:auto">${esc(p.body)}</pre>
   <div style="border:1px solid var(--line);border-radius:8px;margin:12px 0;overflow:hidden">
     <div style="padding:8px 12px;font-size:12.5px;border-bottom:1px solid var(--line2)">${dup.blocked?`<b style="color:var(--red)">STOP</b> — ${esc(dup.reasons[0]?.detail||"duplicate")}${dup.reasons[0]?.previous_date?` on <b>${esc(fmtDT(dup.reasons[0].previous_date))}</b>`:""}`:`<b style="color:var(--green)">OK</b> — no previous outreach found`}</div>
     <div style="padding:8px 12px;font-size:12.5px;border-bottom:1px solid var(--line2)">${p.attachment?`Attachment: <code>${esc(p.attachment)}</code>`:"No resume file configured"}</div>
     ${(dup.warnings||[]).map(w=>`<div style="padding:8px 12px;font-size:12.5px">note — ${esc(w)}</div>`).join("")}
   </div>
   <div style="display:flex;gap:8px;justify-content:flex-end">
     <button class="btn" onclick="dlgClose()">Cancel</button>
     <button class="btn approve" id="osu-go" ${p.can_send&&(OSU_STATE.mailConfigured||OSU_STATE.dryrun)?"":"disabled"}>Approve — send this one email</button>
   </div>`);
  $("#osu-go").onclick=async()=>{
    const res=await api("/api/outreach/send",{method:"POST",body:JSON.stringify({id,confirm:true})});
    if(res.ok){dlgClose();toast(`Sent ✓ ${res.data.message_id||""}`,7000);pgOutreach($("#view"))}
    else if(res.status===429){toast(res.data.detail||"Pacing limit.",9000)}
    else if((res.data.duplicate||{}).blocked){dlgClose();toast("Blocked: duplicate ("+res.data.duplicate.reasons[0]?.previous_date+")",9000);pgOutreach($("#view"))}
    else toast("Send failed: "+(res.data.detail||res.data.error||""));
  };
};

/* ================= PAGE: GMAIL ================= */
async function pgGmail(view){
  const {ok,data}=await api("/api/gmail");if(!ok)throw new Error(data.error);
  const bucket=b=>b==="confirmations"?data.confirmations:b==="recruiter"?data.recruiter:[];
  view.innerHTML=`<div class="pagehead"><h1>Gmail Intelligence</h1><p>Read-only view over gmail_truth_index + scan logs · last scan ${fmtDT(data.scan.scan_started)} · ${data.scan.emails_scanned} emails scanned, ${data.scan.confirmations_found} confirmations</p></div>
   <div class="chips" id="gm-tabs">
     <button class="fchip" aria-pressed="true" data-b="confirmations">Application confirmations (${data.confirmations.length})</button>
     <button class="fchip" aria-pressed="false" data-b="recruiter">Recruiter (${data.recruiter.length})</button>
     <button class="fchip" aria-pressed="false" data-b="other">Other</button>
   </div><div style="height:12px"></div>
   <div id="gm-body"></div>`;
  const draw=b=>{
    $("#gm-body").innerHTML=
      b==="other"?card("",`<div class="empty">${esc(data.other_note)}</div><div class="empty" style="padding-top:4px">${esc(data.action_required_note)}</div>`):
      card("",(()=>{
        const items=bucket(b);
        return items.length?`<div class="tblwrap"><table><thead><tr><th>Company</th><th>Role</th><th>Date</th><th>Confidence</th><th>Related app</th><th>Note</th></tr></thead><tbody>
        ${items.map(e=>{const app=APP_DATA?.find(a=>normC(a.company)===normC(e.company));
          return `<tr ${app?`class="rowclick" onclick="openApplication('${app.id}')"`:""}>
          <td><b>${esc(e.company)}</b></td><td>${esc(e.role_hint||"—")}</td><td>${esc(e.date||"—")}</td>
          <td>${e.confidence!=null?e.confidence:"—"}</td><td>${app?esc(app.id)+" "+bdg(app.status):"—"}</td>
          <td><span class="sub">${esc(e.bucket_reason||"indexed confirmation")}</span></td></tr>`}).join("")}
        </tbody></table></div>`:`<div class="empty">No emails in this bucket yet.</div>`;
      })());
  };
  $("#gm-tabs").onclick=e=>{const b=e.target.closest(".fchip");if(!b)return;
    document.querySelectorAll("#gm-tabs .fchip").forEach(x=>x.setAttribute("aria-pressed",x===b));draw(b.dataset.b)};
  draw("confirmations");
}
const normC=s=>String(s||"").toLowerCase().replace(/[^a-z0-9]/g,"");

/* ================= PAGE: INTERVIEWS ================= */
window.openKit=async function(slug){
  const {data}=await api("/api/interviews");
  const kit=(data.kits||[]).find(k=>k.slug===slug);if(!kit)return;
  dlgOpen("Interview kit — "+slug,`
    <div class="chips" style="margin-bottom:10px">${kit.files.map(f=>`<button class="fchip" onclick="kitFile('${slug}','${esc(f)}',this)">${esc(f.replace(".md","").replace(/_/g," "))}</button>`).join("")}</div>
    <div id="kit-body"><div class="empty">Pick a section above.</div></div>`)};
window.kitFile=async function(slug,f,btn){
  document.querySelectorAll("#dlg .fchip").forEach(x=>x.setAttribute("aria-pressed",x===btn));
  const {ok,data}=await api(`/api/kit?slug=${encodeURIComponent(slug)}&file=${encodeURIComponent(f)}`);
  $("#kit-body").innerHTML=ok?`<pre class="md" style="max-height:46vh;overflow:auto">${esc(data.content)}</pre>`:`<div class="errbox">Could not load file.</div>`};
async function pgInterviews(view){
  const {ok,data}=await api("/api/interviews");if(!ok)throw new Error(data.error);
  view.innerHTML=`<div class="pagehead"><h1>Interviews</h1><p>${esc(data.note)}</p></div>
   ${card("Scheduled interviews",`<div class="empty">None detected — the existing Gmail pipeline has not indexed any interview invitation.<br>${data.scheduled.length===0?'<span class="unavail">dates/times/meeting links: unavailable until invitations arrive</span>':""}</div>`)}
   ${card(`Preparation kits (${data.kits_prepared ?? data.kits.length})`,`
     <div class="grid3">${data.kits.map(k=>`
       <div style="border:1px solid var(--line2);border-radius:10px;padding:11px 13px;display:flex;align-items:center;gap:8px">
         <b style="text-transform:capitalize">${esc(k.slug.replace(/_/g," "))}</b>
         <span class="spacer"></span>
         <button class="btn sm" onclick="openKit('${k.slug}')">Open kit</button>
       </div>`).join("")||"<div class='empty'>No kits generated yet.</div>"}
   `)}
  `;
}

/* ================= PAGE: ASSESSMENTS ================= */
async function pgAssessments(view){
  const {ok,data}=await api("/api/assessments");if(!ok)throw new Error(data.error);
  view.innerHTML=`<div class="pagehead"><h1>Assessments</h1><p>Assessment invitations and deadlines will appear here once the Gmail pipeline indexes them.</p></div>
   ${card("",`<div class="empty">🔴🟡🟢 deadline tracking starts when the first assessment is detected.<br><br>Pending right now: <b>${data.pending??"0"}</b><br><span class="unavail">${esc(data.note)}</span></div>`)}`;
}

/* ================= PAGE: FOLLOW-UPS ================= */
async function pgFollowups(view){
  const {ok,data}=await api("/api/followups");if(!ok)throw new Error(data.error);
  FU_DATA=[...data.overdue,...data.due_today,...(data.upcoming||[])];
  const sect=(title,list,tone)=>card(`${title} (${list.length})`,`<div class="tblwrap"><table><thead><tr><th>Due</th><th>Company</th><th>Role ref</th><th>Day</th><th>Status</th><th>Draft</th><th></th></tr></thead><tbody>
    ${list.map(i=>{const idx=FU_DATA.indexOf(i);return `<tr>
      <td>${fmtD(i.due_date)}</td>
      <td><b>${esc(i.company)}</b>${i._recruiter?`<span class="sub">${esc(i._recruiter)}</span>`:""}</td>
      <td>${esc(i.job_id||"—")}</td><td>Day-${esc(i.day)}</td><td>${bdg(i.status)}</td>
      <td><button class="btn sm" onclick="fuDraft(${idx})">View</button></td>
      <td>${i._rec_id?`<button class="btn sm primary" onclick="osuSend('${i._rec_id}')">Send</button>`:`<span class="unavail" title="Add a recruiter contact in Outreach first">send n/a</span>`}</td>
    </tr>`}).join("")||`<tr><td colspan="7"><div class="empty">Nothing here.</div></td></tr>`}
    </tbody></table></div>`);
  view.innerHTML=`<div class="pagehead"><h1>Follow-ups</h1><p>Policy: ${esc(data.policy||"drafts only - never auto-send")} · sending respects the outreach 12/day + gap limits</p></div>
   ${sect("Overdue",data.overdue,"overdue")}
   ${sect("Due today",data.due_today)}
   ${sect("Upcoming",data.upcoming.slice(0,25))}`;
}
let FU_DATA=[];
window.fuDraft=function(idx){const i=FU_DATA[idx];if(!i)return;
  dlgOpen(`Follow-up draft — ${i.company} (Day-${i.day}, due ${fmtD(i.due_date)})`,
   `<pre class="md">${esc(i.draft||"(no draft)")}</pre>`)};
window.dlgOpen=dlgOpen;

/* ================= PAGE: ANALYTICS ================= */
async function pgAnalytics(view){
  const res=await api("/api/analytics");if(!res.ok)throw new Error("fail");
  const d=res.data;
  const f=d.funnel||{};const steps=["discovered","queued","applied","interview","assessment","offer"];
  const max=Math.max(1,...steps.map(s=>f[s]||0));
  view.innerHTML=`<div class="pagehead"><h1>Analytics</h1><p>Generated by analytics.py ${fmtDT(d.generated_at)} · only metrics supported by real data are shown</p></div>
   <div class="kpis">
     ${kpi(d.conversions?.queued_to_applied_pct!=null?d.conversions.queued_to_applied_pct+"%":null,"Queued → applied")}
     ${kpi(d.rates?.response_rate_pct!=null?d.rates.response_rate_pct+"%":null,"Response rate")}
     ${kpi(d.rates?.interview_rate_pct!=null?d.rates.interview_rate_pct+"%":null,"Interview rate")}
     ${kpi(d.rates?.offer_count??"0","Offers")}
     ${kpi(d.rates?.avg_response_time_hours!=null?d.rates.avg_response_time_hours+"h":null,"Avg response time", "", d.rates?.avg_response_time_note||"")}
   </div>
   ${card("Funnel",steps.map(s=>`<div class="funnel-step"><div class="lbl"><span>${s}</span><span>${f[s]??"0"}</span></div><div class="track hbar" style="grid-template-columns:1fr"><div class="fill" style="width:${Math.round(100*(f[s]||0)/max)}%"></div></div></div>`).join("")
     +(f.rejected?`<div class="sub" style="margin-top:8px;color:var(--ink3)">rejected: ${f.rejected} · ghosted: ${f.ghosted??"—"}</div>`:""))}
   <div class="grid2">
   ${card("By ATS provider",(d.ats_performance||[]).length?`<div class="tblwrap"><table><thead><tr><th>Provider</th><th>Disc.</th><th>Apps</th><th>Submits</th><th>Fails</th><th>Rate</th></tr></thead><tbody>
     ${d.ats_performance.map(p=>`<tr><td>${esc(p.provider)}</td><td>${p.discovered}</td><td>${p.applications}</td><td>${p.submissions}</td><td>${p.failures}</td><td>${p.submission_rate_pct}%</td></tr>`).join("")}
   </tbody></table></div>`:"<div class='empty'>No provider data yet.</div>")}
   ${card("By resume variant",tblBreakdown(d.breakdowns.by_resume_variant))}
   </div>
   <div class="grid2">
   ${card("Top companies (applied)",tblBreakdown(d.breakdowns.by_company.slice(0,10)))}
   ${card("By location",tblBreakdown(d.breakdowns.by_location.slice(0,10)))}
   </div>
   ${card("By role bucket",tblBreakdown(d.breakdowns.by_role_bucket))}
  `;
}
function tblBreakdown(rows){
  if(!rows||!rows.length)return "<div class='empty'>No data yet.</div>";
  return `<div class="tblwrap"><table><thead><tr><th>Group</th><th>Applied</th><th>Responses</th></tr></thead><tbody>
   ${rows.map(r=>`<tr><td>${esc(r.group)}</td><td>${r.applied}</td><td>${r.responses||0}</td></tr>`).join("")}
  </tbody></table></div>`;
}

/* ================= PAGE: RESUME INTELLIGENCE ================= */
async function pgResume(view){
  const {ok,data}=await api("/api/resume");if(!ok)throw new Error(data.error);
  const totalSel=Object.values(data.selection_counts||{}).reduce((a,b)=>a+b,0);
  view.innerHTML=`<div class="pagehead"><h1>Resume Intelligence</h1><p>Which variant the engine picks per job · content is never auto-modified</p></div>
   <div class="grid3">
   ${(data.variants||[]).map(v=>card(esc(v.variant||v.Variant),`
     <div class="kv"><dt>Applied</dt><dd>${v.applied??v.Applied??"—"}</dd><dt>Submitted</dt><dd>${v.submitted??v.Submitted??"—"}</dd>
     <dt>Conversion</dt><dd>${v.conversion_pct??v.Conversion_pct??"—"}</dd></dl>`)).join("")||"<div class='empty'>No variant stats yet.</div>"}
   </div>
   ${card("Engine selections",totalSel?`
     ${Object.entries(data.selection_counts).map(([k,v])=>`<div class="hbar"><span>${esc(k)}</span><div class="track"><div class="fill" style="width:${Math.round(100*v/totalSel)}%"></div></div><span class="num">${v}</span></div>`).join("")}
     <div style="margin-top:10px"><table><thead><tr><th>Job</th><th>Chosen</th><th>Why</th></tr></thead><tbody>
     ${(data.recent_selections||[]).slice(0,8).map(s=>`<tr><td>${esc(s.company)}<span class="sub">${esc(s.title)}</span></td><td><b>${esc(s.chosen)}</b></td><td><span class="sub">${esc(s.why)}</span></td></tr>`).join("")}
     </tbody></table></div>`:"<div class='empty'>No selections recorded yet.</div>")}
   ${card("Recommendations",(data.recommendations||[]).map(r=>`• ${esc(r)}`).join("<br>")||"<div class='empty'>none</div>")}
  `;
}

/* ================= PAGE: COMPANIES ================= */
let CC_STATE={q:"",flt:new Set(),page:1};
async function pgCompanies(view){
  CC_STATE.page=CC_STATE.page||1;
  view.innerHTML=`<div class="pagehead"><h1>Companies</h1><p>Universe from company_universe + hiring intelligence · search runs server-side over thousands of companies</p></div>
   ${card("",`
    <div class="toolbar" style="border:none;padding:0 0 10px">
      <input type="text" id="cc-q" placeholder="Search company…" value="${esc(CC_STATE.q)}" style="min-width:200px">
      <div class="chips" id="cc-flts">${["dream","hiring","india","remote","react","nextjs","fullstack"].map(f=>`<button class="fchip" data-f="${f}" aria-pressed="${CC_STATE.flt.has(f)}">${f}</button>`).join("")}</div>
      <span class="spacer"></span><span class="sub" id="cc-count" style="color:var(--ink3)"></span>
    </div>
    <div class="tblwrap"><table><thead><tr>
      <th>Company</th><th>Hiring score</th><th>React</th><th>Next.js</th><th>Full stack</th><th>India</th><th>Remote</th><th>ATS</th><th>In queue</th><th>Submitted</th><th>Gmail ✓</th><th>Last scan</th>
    </tr></thead><tbody id="cc-body"></tbody></table></div>
    <div style="display:flex;gap:8px;justify-content:center;padding:12px"><button class="btn sm" id="cc-prev">‹ Prev</button><span class="sub" id="cc-page" style="align-self:center"></span><button class="btn sm" id="cc-next">Next ›</button></div>`)}
  `;
  const draw=async()=>{
    const flt=[...CC_STATE.flt].join(",");
    const {ok,data}=await api(`/api/companies?q=${encodeURIComponent(CC_STATE.q)}&flt=${flt}&page=${CC_STATE.page}`);
    if(!ok)return;
    $("#cc-count").textContent=`${data.total} companies`;
    $("#cc-page").textContent=`page ${data.page}/${Math.max(1,data.pages)}`;
    $("#cc-body").innerHTML=data.rows.map(r=>`<tr>
      <td><b>${esc(r.company)}</b>${r.tier===1?' <span title="tier 1" style="color:var(--orange)">★</span>':""}<span class="sub">rank ${r.rank??"—"}</span></td>
      <td>${r.hiring_score??"—"}</td>
      <td>${r.react||"—"}</td><td>${r.nextjs||"—"}</td><td>${r.fullstack||"—"}</td>
      <td>${r.india_hiring?"✓":""}</td><td>${r.remote_hiring?"✓":""}</td>
      <td>${esc(r.ats||"—")}</td><td>${r.jobs_in_queue}</td><td>${r.submitted}</td><td>${r.gmail_confirms||"—"}</td>
      <td>${fmtD(r.last_scan)}</td></tr>`).join("")||`<tr><td colspan="12"><div class="empty">No companies match.</div></td></tr>`;
    $("#cc-prev").disabled=data.page<=1;$("#cc-next").disabled=data.page>=data.pages;
  };
  let deb;$("#cc-q").oninput=e=>{clearTimeout(deb);deb=setTimeout(()=>{CC_STATE.q=e.target.value;CC_STATE.page=1;draw()},350)};
  $("#cc-flts").onclick=e=>{const b=e.target.closest(".fchip");if(!b)return;
    CC_STATE.flt.has(b.dataset.f)?CC_STATE.flt.delete(b.dataset.f):CC_STATE.flt.add(b.dataset.f);
    b.setAttribute("aria-pressed",CC_STATE.flt.has(b.dataset.f));CC_STATE.page=1;draw()};
  $("#cc-prev").onclick=()=>{CC_STATE.page--;draw()};$("#cc-next").onclick=()=>{CC_STATE.page++;draw()};
  draw();
}

/* ================= PAGE: AUTOMATION HEALTH ================= */
async function pgHealth(view){
  const {ok,data}=await api("/api/health");if(!ok)throw new Error(data.error);
  const q=data.queue||{},wd=data.watchdog||{};
  const hb=data.heartbeat.age_sec;
  const alerts=(data.alerts||[]).filter(a=>a&&a.toLowerCase().indexOf("none")===-1);
  const recos=(data.optimization_recommendations||[]);
  const stuck=data.unknown_state_jobs||[];
  const restartRows=(data.restart_history||[]).map(h=>
    `<tr><td>${fmtDT(h.ts)}</td><td>${esc(h.batch_id||"—")}</td><td><span style="color:var(--red)">${esc(h.reason||"")}</span></td></tr>`).join("");
  const hbLines=(data.heartbeat.recent||[]).map(h=>[h.ts,h.batch,h.event].filter(Boolean).join(" | ")).join("\n")||"—";
  view.innerHTML=`<div class="pagehead"><h1>Automation Health</h1><p>Live mirror of supervisor state · refreshed automatically</p></div>
   <div class="kpis">
     ${kpi(data.supervisor.running?"Running":"Stopped","Supervisor",data.supervisor.running?"good":"bad")}
     ${kpi(q.pending??"—","Pending")}
     ${kpi(q.in_progress??"—","In progress",q.in_progress?"warn":"")}
     ${kpi(q.submitted??"—","Submitted","good")}
     ${kpi(q.review_required??"—","Review")}
     ${kpi(q.failed??"—","Failed",q.failed?"bad":"")}
     ${kpi(hb!=null?Math.round(hb)+"s":NA,"Heartbeat age",hb!=null&&hb<300?"good":hb==null?"bad":"warn")}
   </div>
   ${(!data.supervisor.running)?`<div class="errbox"><b>Supervisor is not running.</b> Resume with <code>resume.bat</code> in D:\\newjobs\\autoapply — orphaned jobs are re-queued automatically.</div>`:""}
   ${(data.unknown_state_jobs||[]).length?`<div class="errbox"><b>${data.unknown_state_jobs.length} jobs stuck in in_progress</b> (orphaned by killed workers): ${data.unknown_state_jobs.slice(0,8).map(j=>j.id).join(", ")}${data.unknown_state_jobs.length>8?"…":""}<br>Fix: set their status back to "pending" in jobs.json, or run resume.bat.</div>`:""}
   <div class="grid2">
   ${card("Worker / current batch",`
     <dl class="kv"><dt>Worker</dt><dd>${esc(data.worker.worker_id||"—")}</dd>
     <dt>Phase</dt><dd>${esc(data.worker.phase||"—")}</dd>
     <dt>Batch</dt><dd>${esc(data.current_batch||"—")}</dd>
     <dt>Current step</dt><dd>${esc((data.current_job&&data.current_job.step)||"—")}</dd>
     <dt>Watchdog</dt><dd>${bdg(wd.state||"unknown")} <span class="sub">restarts: ${wd.restart_count??"—"} · last reason: ${esc(wd.last_restart_reason||"—")}</span></dd></dl>`)}
   ${card("Gmail / reliability",`
     <dl class="kv"><dt>Gmail last sync</dt><dd>${fmtDT(data.gmail.last_sync)}</dd>
     <dt>Emails scanned</dt><dd>${data.gmail.emails_scanned??"—"}</dd>
     <dt>Total restarts</dt><dd>${data.reliability?.total_restarts??"—"}</dd>
     <dt>Crash recoveries</dt><dd>${data.reliability?.crash_recoveries??"—"}</dd>
     <dt>Dedupe events</dt><dd>${data.reliability?.dedupe_events??"—"}</dd>
     <dt>Corruption events</dt><dd>${data.reliability?.state_corrupt_events??"—"}</dd>
     <dt>BrowserOS</dt><dd>${NA}</dd></dl>
     ${(data.alerts||[]).filter(a=>a&&!"none".includes(a.toLowerCase())).length?`<div class="errbox" style="margin-top:10px">${data.alerts.map(esc).join("<br>")}</div>`:""}`)}
   </div>
   ${card("Performance",`
     <div class="sysgrid">
       ${sysItem("Batches completed",data.performance?.batches_completed)}
       ${sysItem("Avg batch time",data.performance?.avg_batch_time_s!=null?data.performance.avg_batch_time_s.toFixed(1)+"s":null)}
       ${sysItem("Avg application time",data.performance?.avg_application_time_s!=null?data.performance.avg_application_time_s.toFixed(1)+"s":null)}
       ${sysItem("Throughput/day",data.performance?.queue_throughput_submissions_per_day)}
       ${sysItem("ETA remaining",data.eta_minutes!=null?Math.round(data.eta_minutes)+" min":null)}
     </div>
     ${(data.optimization_recommendations||[]).length?`<div style="margin-top:10px;font-size:12.5px;color:var(--ink2)">${data.optimization_recommendations.map(esc).join("<br>")}</div>`:""}`)}
   ${card("Recent restarts",data.restart_history.length?`<div class="tblwrap"><table><thead><tr><th>When</th><th>Batch</th><th>Reason</th></tr></thead><tbody>
     ${data.restart_history.map(h=>`<tr><td>${fmtDT(h.ts)}</td><td>${esc(h.batch_id||"—")}</td><td><span style="color:var(--red)">${esc(h.reason||"")}</span></td></tr>`).join("")}
   </tbody></table></div>`:"<div class='empty'>No restarts recorded.</div>")}
   ${card("Heartbeat tail",`<pre class="md">${esc((data.heartbeat.recent||[]).map(h=>[h.ts,h.batch,h.event].filter(Boolean).join(" | ")).join("\n")||"—")}</pre>`)}
  `;
  pollWhileVisible(()=>location.hash==="#health",pgHealth,15000,view);
}

/* polling helper that stops when tab hidden or navigated away */
let _pollTimer=null;
function pollWhileVisible(stillThere,fn,ms,view){
  clearTimeout(_pollTimer);
  _pollTimer=setTimeout(async()=>{
    if(document.visibilityState!=="visible"||!stillThere())return;
    await fn(view||$("#view"));
  },ms);
}

/* ---------------- global search / bell / quick actions ---------------- */
let gsDeb;
$("#gs").addEventListener("input",e=>{clearTimeout(gsDeb);const q=e.target.value;gsDeb=setTimeout(async()=>{
  const box=$("#gresults");
  if(q.trim().length<2){box.classList.remove("open");return}
  const {ok,data}=await api("/api/search?q="+encodeURIComponent(q));if(!ok)return;
  box.innerHTML=Object.entries(data.groups||{}).map(([g,items])=>`<div class="ggroup">${esc(g)}</div>`+
    items.map(i=>`<a class="gitem" href="${esc(i.link)}" onclick="document.getElementById('gresults').classList.remove('open')"><b>${esc(i.title)}</b><span>${esc(i.sub||"")}</span></a>`).join("")).join("")||`<div class="empty">No matches.</div>`;
  box.classList.add("open");
},250)});
document.addEventListener("click",e=>{if(!e.target.closest(".gsearch"))$("#gresults").classList.remove("open");
  if(!e.target.closest(".menu-wrap"))document.querySelectorAll(".menu").forEach(m=>m.classList.remove("open"))});
$("#gs").addEventListener("keydown",e=>{if(e.key==="Enter"){const a=$("#gresults .gitem");if(a){a.click();$("#gs").value=""}}});

function updateBell(count,items){
  const c=$("#bell-cnt");
  if(count>0){c.style.display="flex";c.textContent=count}else c.style.display="none";
  $("#bell-menu").innerHTML=(items&&items.length?items:[]).map(a=>`<button onclick="location.hash='${a.link.replace(/^#/,"")}';document.getElementById('bell-menu').classList.remove('open')"><span class="pb ${a.priority}" style="margin-right:6px">${a.priority}</span>${esc(a.title)}</button>`).join("")||`<button disabled>No urgent notifications.</button>`;
}
$("#bell-btn").onclick=e=>{e.stopPropagation();$("#bell-menu").classList.toggle("open");$("#qa-menu").classList.remove("open")};
$("#qa-menu").innerHTML=[
 ["Open pending applications","#applications"],["Open action-required items","#overview"],
 ["Open today's follow-ups","#followups"],["Open interviews","#interviews"],["Open recruiter drafts","#outreach"]]
 .map(([l,h])=>`<button onclick="location.hash='${h}';document.getElementById('qa-menu').classList.remove('open')">${l}</button>`).join("");
$("#qa-btn").onclick=e=>{e.stopPropagation();$("#qa-menu").classList.toggle("open");$("#bell-menu").classList.remove("open")};

/* keep bell fed on first load + every minute */
(async function initBell(){try{const {data}=await api("/api/overview");const urgent=(data.actions||[]).filter(a=>["P0","P1"].includes(a.priority));updateBell(urgent.length,urgent);updateSupPill(data.system.supervisor)}catch(e){}})();
setInterval(async()=>{try{if(document.visibilityState!=="visible")return;const {data}=await api("/api/overview");const urgent=(data.actions||[]).filter(a=>["P0","P1"].includes(a.priority));updateBell(urgent.length,urgent);updateSupPill(data.system.supervisor)}catch(e){}},60000);

const PAGES={
  overview:pgOverview, applications:pgApplications, priority:pgPriority,
  discovery:pgDiscovery, outreach:pgOutreach, gmail:pgGmail,
  interviews:pgInterviews, assessments:pgAssessments, followups:pgFollowups,
  analytics:pgAnalytics, resume:pgResume, companies:pgCompanies, health:pgHealth};

route();
