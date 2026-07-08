"""The web console: a wizard-driven main view (Agents → Goal → Plan → Run → Done)
plus a Console view with the observability panels.

Design language follows the user's Structure Lab console (structure-discovery-lab
webapp): #f5f5f7 canvas, white 16px-radius cards with hairline borders, Newsreader
serif for display headings, Hanken Grotesk for body, IBM Plex Mono for data,
#0071e3 accent, eyebrow labels, left stepper, pill nav, blurred sticky header.
"""

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>metaharness · Console</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,wght@0,500;0,600;1,500&family=Hanken+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{
  --accent:#0071e3; --accent-soft:#e8f0fd; --accent-dark:#0058b0; --accent-dark2:#3a5a8c;
  --bg:#f5f5f7; --card:#fff; --line:#e8e8ed; --line2:#e2e2e7; --hair:#f0f0f3;
  --dark:#1d1d1f; --on-dark:#f5f5f7; --dark-mut:#c9c9ce; --dark-faint:#a1a1a6;
  --ink:#1d1d1f; --ink2:#424245; --mut:#6e6e73; --mut2:#86868b; --faint:#a1a1a6;
  --green:#248a3d; --amber:#b0670a; --red:#c1121f;
  --serif:"Newsreader",Georgia,serif;
  --sans:"Hanken Grotesk",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
*{box-sizing:border-box;margin:0}
body{font-family:var(--sans);background:var(--bg);color:var(--ink);
  font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;
  font-synthesis:none}
button{font-family:inherit;cursor:pointer;border:0;background:none}
.mono{font-family:var(--mono)}

header{position:sticky;top:0;z-index:40;
  background:rgba(245,245,247,.82);backdrop-filter:saturate(160%) blur(16px);
  -webkit-backdrop-filter:saturate(160%) blur(16px);border-bottom:1px solid var(--line)}
.bar{max-width:1080px;margin:0 auto;display:flex;align-items:center;gap:18px;padding:13px 24px}
.logo{display:flex;align-items:center;gap:10px}
.logo .sq{width:29px;height:29px;border-radius:8px;background:var(--accent);color:#fff;
  display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:13px}
.logo .name{font-family:var(--serif);font-weight:600;font-size:18px;letter-spacing:-.01em}
nav.pills{display:flex;gap:2px}
nav.pills button{padding:6px 13px;border-radius:999px;color:var(--mut);font-size:13.5px;font-weight:500}
nav.pills button.on{background:var(--accent-soft);color:var(--accent)}
.spacer{flex:1}
.updated{font-family:var(--mono);font-size:11.5px;color:var(--faint)}

main{max-width:1080px;margin:0 auto;padding:30px 24px 90px}
.view{animation:fadeUp .34s ease}
@keyframes fadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.eyebrow{font-family:var(--mono);text-transform:uppercase;letter-spacing:.08em;
  font-size:12px;color:var(--faint)}
h1.greet{font-family:var(--serif);font-weight:500;font-size:30px;line-height:1.12;
  letter-spacing:-.02em;margin:6px 0 22px;max-width:640px}

/* wizard */
.wiz-grid{display:grid;grid-template-columns:206px 1fr;gap:26px}
.stepper{position:sticky;top:86px;align-self:start;display:flex;flex-direction:column;gap:2px}
.stepper .s{display:flex;align-items:center;gap:11px;padding:9px 11px;border-radius:11px}
.stepper .s.on{background:#fff;border:1px solid var(--line)}
.stepper .s .n{width:24px;height:24px;border-radius:999px;flex:0 0 auto;display:flex;
  align-items:center;justify-content:center;font-size:12px;font-family:var(--mono);
  background:var(--hair);color:var(--mut2)}
.stepper .s.on .n{background:var(--accent);color:#fff}
.stepper .s.done .n{background:var(--green);color:#fff}
.stepper .s .l{font-size:13.5px;color:var(--mut);font-weight:500}
.stepper .s.on .l{color:var(--ink);font-weight:600}
.wiz-body{min-height:380px}
.guide{display:flex;gap:12px;background:var(--accent-soft);border-radius:16px;
  padding:15px 20px;margin-bottom:18px}
.guide b{color:var(--accent);font-size:13.5px;display:block}
.guide p{color:#4a545e;font-size:13px;margin-top:2px}
.wiz-nav{display:flex;justify-content:space-between;gap:12px;margin-top:20px}

.card{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:20px;overflow-x:auto}
.card h2{font-family:var(--serif);font-weight:600;font-size:18px;margin-bottom:2px}
.card .sub{color:var(--mut2);font-size:12.5px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(440px,1fr));gap:16px}
.card.wide{grid-column:1 / -1}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:18px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px}
.tile .val{font-family:var(--mono);font-size:25px;line-height:1.1}
.tile .val.green{color:var(--green)} .tile .val.red{color:var(--red)}
.tile .lab{color:var(--mut2);font-size:12.5px;margin-top:6px}

table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;color:var(--mut2);font-weight:600;padding:5px 10px 5px 0;
  border-bottom:1px solid var(--line);white-space:nowrap;font-size:12px}
td{padding:7px 10px 7px 0;border-bottom:1px solid var(--hair);vertical-align:top}
tr:last-child td{border-bottom:0}

.badge{display:inline-block;padding:3px 11px;border-radius:999px;font-size:11.5px;
  font-weight:600;white-space:nowrap}
.badge.ok{background:#248a3d1c;color:var(--green)}
.badge.warn{background:#b0670a1c;color:var(--amber)}
.badge.bad{background:#c1121f14;color:var(--red)}
.badge.act{background:var(--accent-soft);color:var(--accent)}
.badge.dim{background:#8e8e9322;color:var(--mut2)}
.btn{display:inline-flex;align-items:center;gap:6px;background:var(--accent);color:#fff;
  border-radius:999px;padding:9px 18px;font-size:13px;font-weight:600}
.btn.ghost{background:#fff;color:var(--accent);border:1px solid var(--line2)}
.btn.reject{background:#fff;color:var(--red);border:1px solid var(--line2)}
.btn:disabled{opacity:.45;cursor:default}
.dim{color:var(--mut2)} .faint{color:var(--faint)} .small{font-size:12px}
.green{color:var(--green)} .red{color:var(--red)} .amber{color:var(--amber)}
.bar-h{background:var(--accent);height:8px;border-radius:4px;min-width:2px;display:inline-block;
  vertical-align:middle;margin-right:8px}
.empty{color:var(--mut2);font-size:13.5px;font-style:italic;padding:8px 0}
.chainline{display:flex;align-items:center;gap:10px;margin-bottom:10px;font-size:13.5px}
.headhash{font-family:var(--mono);font-size:11.5px;color:var(--faint)}

/* ledger rows (Console) — one plain-language row per thing */
.lrow{display:flex;gap:14px;align-items:center;padding:12px 2px;
  border-bottom:1px solid var(--hair)}
.lrow:last-child{border-bottom:0}
.runrow{cursor:pointer}
.runrow:hover .rr-title{color:var(--accent)}
.rr-main{flex:1;min-width:0}
.rr-title{font-weight:600;font-size:13.5px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.rr-meta{font-family:var(--mono);font-size:11px;color:var(--faint);margin-top:3px}
.rr-story{font-size:12.5px;color:var(--mut);text-align:right;flex:0 1 auto;max-width:44%}
.rr-detail{padding:2px 0 14px;border-bottom:1px solid var(--hair)}
.rr-out{border:1px solid var(--hair);border-radius:12px;padding:12px 14px;margin:10px 0}
.rr-out-h{display:flex;gap:8px;align-items:center;font-size:13px;font-weight:600}
.guide .fx{font-family:var(--serif);font-style:italic;font-size:19px;
  color:var(--accent);line-height:1.3}
.guide .cta{align-self:center;margin-left:auto;flex:0 0 auto}
.pager{display:flex;align-items:center;justify-content:space-between;gap:10px;
  margin-top:12px;padding-top:10px;border-top:1px solid var(--hair)}

/* Home: the calm landing — a single next-action card answers "what do I do
   right now?" (structure-lab handoff pattern) */
.next-action{background:var(--dark);color:var(--on-dark);border-radius:20px;
  padding:26px 28px;display:flex;align-items:center;gap:18px;flex-wrap:wrap;
  box-shadow:0 12px 40px rgba(0,0,0,.12);margin-bottom:8px}
.next-action .txt{flex:1;min-width:260px}
.next-action .eyebrow{color:var(--dark-faint)}
.next-action h2{font-family:var(--serif);font-weight:600;font-size:24px;
  letter-spacing:-.01em;margin:4px 0 6px}
.next-action p{color:var(--dark-mut);font-size:14px}
.next-action .btn{border-radius:12px;padding:14px 26px}
.also{color:var(--mut2);font-size:12.5px;margin:0 4px 18px}
#home-tiles{margin-top:16px}

/* AI companion: the gradient sparkle is the ONE signal for advisory content —
   everything without it is verified, deterministic data */
.why{width:27px;height:27px;border-radius:999px;flex:0 0 auto;display:inline-flex;
  align-items:center;justify-content:center;cursor:pointer;
  background:linear-gradient(135deg,var(--accent-soft),#8b5cf622);
  border:1px solid #8b5cf630;transition:transform .15s ease,box-shadow .15s ease}
.why svg{width:15px;height:15px;display:block}
.why:hover,.why.on{transform:scale(1.12);box-shadow:0 2px 10px #8b5cf640}
@media (prefers-reduced-motion: reduce){.why,.why:hover{transition:none;transform:none}}
.ai-chip{display:inline-flex;align-items:center;gap:5px;padding:3px 11px;border-radius:999px;
  font-size:11.5px;font-weight:600;white-space:nowrap;
  background:linear-gradient(135deg,var(--accent-soft),#8b5cf626);color:var(--accent)}
.ai-chip svg{width:11px;height:11px}
.advisor{border:1px solid var(--line);border-radius:12px;margin:10px 0 4px;overflow:hidden}
.advisor .facts{padding:12px 14px;border-bottom:1px solid var(--hair)}
.advisor .facts .h,.advisor .takes .h{display:flex;align-items:center;gap:8px;font-size:11px;
  font-weight:600;color:var(--mut2);margin-bottom:7px;text-transform:uppercase;
  letter-spacing:.06em;font-family:var(--mono)}
.advisor .facts ul{margin:0;padding-left:18px;font-size:13px;color:var(--ink2)}
.advisor .takes{padding:12px 14px;background:var(--accent-soft)}
.advisor .takes p{font-size:13.5px;color:var(--ink2);margin-bottom:10px}
.advisor .nba{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.pager .pager-info{font-family:var(--mono);font-size:11.5px;color:var(--faint)}
.pager button:disabled{opacity:.35;cursor:default}

.field{margin-bottom:14px}
.field label{display:block;font-size:12px;font-weight:600;color:var(--mut2);margin-bottom:6px}
.field input,.field textarea,.field select{width:100%;border:1px solid var(--line2);
  border-radius:10px;padding:10px 13px;font-family:inherit;font-size:13.5px;
  background:#fafafc;outline:none;color:var(--ink)}
.field input:focus,.field textarea:focus{border-color:var(--accent);background:#fff}
.field textarea{min-height:90px;resize:vertical}
.field input.mono,.field select{font-family:var(--mono);font-size:12.5px}

.pillrow{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px}
.pill{padding:7px 14px;border-radius:999px;border:1px solid var(--line2);background:#fff;
  font-size:13px;font-weight:500;color:var(--mut)}
.pill.on{background:var(--accent);border-color:var(--accent);color:#fff}
.prov-item{border:1px solid var(--line);border-radius:12px;padding:13px 16px;margin-bottom:10px;
  display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.prov-item .pi-main{flex:1;min-width:200px}
.prov-item .pi-name{font-weight:600;font-size:13.5px}
.kv{font-family:var(--mono);font-size:11.5px;color:var(--mut2)}
.subwiz-steps{display:flex;gap:6px;margin-bottom:16px;flex-wrap:wrap}
.subwiz-steps .t{font-family:var(--mono);font-size:11px;padding:4px 12px;border-radius:999px;
  background:var(--hair);color:var(--mut2)}
.subwiz-steps .t.on{background:var(--accent);color:#fff}
.subwiz-steps .t.done{background:var(--green);color:#fff}
.hint-panel{background:var(--hair);border-radius:12px;padding:13px 16px;font-size:12.5px;
  color:var(--ink2);margin-bottom:14px}
.hint-panel b{display:block;font-size:12px;color:var(--mut);margin-bottom:4px}
.hint-panel ul{margin:4px 0 0 18px}
.pick-list{border:1px solid var(--line);border-radius:10px;max-height:190px;overflow-y:auto;
  margin-top:6px;background:#fff}
.pick-list .pl-row{padding:7px 13px;font-family:var(--mono);font-size:12px;cursor:pointer;
  border-bottom:1px solid var(--hair);color:var(--ink2)}
.pick-list .pl-row:last-child{border-bottom:0}
.pick-list .pl-row:hover{background:var(--accent-soft);color:var(--accent)}
.pick-list .pl-more{padding:6px 13px;font-size:11.5px;color:var(--faint);font-style:italic}
.step-actions{display:flex;gap:4px;margin-left:auto}
.step-actions button{width:26px;height:26px;border-radius:8px;border:1px solid var(--line2);
  background:#fff;color:var(--mut);font-size:12px;line-height:1}
.step-actions button:hover{color:var(--accent);border-color:var(--accent)}
.step-edit{background:var(--hair);border-radius:12px;padding:14px 16px;margin-top:10px}
.step-edit .field{margin-bottom:10px}
.tool-toggle{padding:4px 11px;border-radius:999px;border:1px solid var(--line2);background:#fff;
  font-family:var(--mono);font-size:11px;color:var(--mut)}
.tool-toggle.on{background:var(--accent);border-color:var(--accent);color:#fff}
.yaml-box{width:100%;min-height:320px;font-family:var(--mono);font-size:12px;
  border:1px solid var(--line2);border-radius:12px;padding:14px;background:#fafafc}

.tierrow{display:flex;align-items:center;gap:12px;padding:11px 0;border-bottom:1px solid var(--hair)}
.tierrow:last-child{border-bottom:0}
.tierrow .tn{font-family:var(--mono);font-size:12px;text-transform:uppercase;
  letter-spacing:.05em;width:76px;color:var(--mut)}
.tierrow .tm{font-weight:600;font-size:13.5px}
.tierrow .td{color:var(--mut2);font-size:12px}

.planstep{display:flex;gap:12px;padding:12px 0;border-bottom:1px solid var(--hair)}
.planstep:last-child{border-bottom:0}
.planstep .n{width:24px;height:24px;border-radius:999px;flex:0 0 auto;display:flex;
  align-items:center;justify-content:center;font-size:12px;font-family:var(--mono);
  background:var(--accent-soft);color:var(--accent)}
.planstep .n.done{background:var(--green);color:#fff}
.planstep .n.now{background:var(--accent);color:#fff}
.planstep .n.fail{background:var(--red);color:#fff}
.planstep .pt{font-weight:600;font-size:13.5px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.planstep .pd{color:var(--mut2);font-size:12.5px;margin-top:2px}
.planstep .out{font-size:12.5px;white-space:pre-wrap;background:var(--hair);
  border-radius:10px;padding:10px 12px;margin-top:8px}
.attempts{margin-top:8px;font-size:12px;border-left:3px solid var(--hair);padding-left:10px}
.attempts .att{margin-top:4px;color:var(--mut2)}
.attempts .att b{font-family:var(--mono);font-weight:600}
.attempts .att.fail b{color:var(--red)}
.attempts .att.pass b{color:var(--green)}
/* humanized step output: .out is plain-pre by default; .md/.json variants flow */
.out{font-size:12.5px;white-space:pre-wrap;background:var(--hair);
  border-radius:10px;padding:10px 12px;margin-top:8px;overflow-x:auto}
.out.md{white-space:normal}
.out.md p{margin:5px 0}
.out.md .md-h{font-weight:700;margin:10px 0 4px}
.out.md .md-h1{font-size:15px}.out.md .md-h2{font-size:14px}.out.md .md-h3{font-size:13px}
.out.md ul,.out.md ol{margin:5px 0;padding-left:20px}
.out.md li{margin:2px 0}
.out.md code{font-family:var(--mono);font-size:11.5px;background:rgba(0,0,0,.07);
  border-radius:4px;padding:1px 4px}
.out.md pre.md-code{background:var(--dark);color:var(--on-dark);padding:10px 12px;
  border-radius:8px;overflow-x:auto;white-space:pre;font-family:var(--mono);
  font-size:11.5px;margin:8px 0}
.out.md table{border-collapse:collapse;margin:8px 0;font-size:12px;background:var(--card)}
.out.md th,.out.md td{border:1px solid var(--line2);padding:4px 8px;text-align:left;vertical-align:top}
.out.md th{background:var(--hair)}
.out.md blockquote{border-left:3px solid var(--line2);margin:6px 0;padding:2px 10px;color:var(--mut2)}
.out.md hr{border:none;border-top:1px solid var(--line2);margin:10px 0}
.out.md a{color:var(--accent)}
.out.json{white-space:normal;font-family:var(--mono);font-size:12px}
details.jt{margin:1px 0}
details.jt details.jt,.jrow{margin-left:16px}
.jt summary{cursor:pointer;color:var(--mut2);font-size:11.5px;user-select:none}
.jrow{margin-top:1px}
.jrow > .jk{color:var(--accent-dark);margin-right:6px}
.jrow > .jk::after{content:':'}
.jv.jstr{color:var(--green)}
.jv.jnum{color:var(--amber)}
.jv.jbool{color:var(--red)}
.jv.jnull{color:var(--faint)}
/* step tabs (Run/Done screens) */
.steptabs{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0 4px;border-bottom:1px solid var(--line2);padding-bottom:10px}
.stab{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--line2);
  background:var(--card);border-radius:999px;padding:6px 14px 6px 7px;font-size:12.5px;
  font-family:var(--sans);font-weight:600;color:var(--ink2);cursor:pointer}
.stab:hover{border-color:var(--accent)}
.stab.on{background:var(--dark);color:var(--on-dark);border-color:var(--dark)}
.stab .ticon{width:20px;height:20px;border-radius:999px;display:inline-flex;align-items:center;
  justify-content:center;font-size:11px;font-family:var(--mono);
  background:var(--accent-soft);color:var(--accent)}
.stab .ticon.done{background:var(--green);color:#fff}
.stab .ticon.now{background:var(--accent);color:#fff}
.stab .ticon.fail{background:var(--red);color:#fff}
.steppanel{padding:10px 2px 2px}
.steppanel .pt{font-weight:600;font-size:13.5px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.steppanel .pd{color:var(--mut2);font-size:12.5px;margin-top:4px}
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--accent-soft);
  border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;
  vertical-align:-1px}
@keyframes spin{to{transform:rotate(360deg)}}

.toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%) translateY(8px);
  background:var(--dark);color:var(--on-dark);padding:11px 18px;border-radius:12px;
  font-size:13.5px;box-shadow:0 10px 30px rgba(0,0,0,.22);opacity:0;pointer-events:none;
  transition:opacity .2s,transform .2s;z-index:90;max-width:80vw;text-align:center}
.toast.on{opacity:1;transform:translateX(-50%) translateY(0)}
@media(max-width:820px){.wiz-grid{grid-template-columns:1fr}
  .stepper{position:static;flex-direction:row;overflow-x:auto;gap:6px}
  .stepper .s .l{display:none}}
@media(max-width:960px){.tiles{grid-template-columns:repeat(2,1fr)}}
@media(max-width:600px){.tiles,.grid{grid-template-columns:1fr}h1.greet{font-size:24px}}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
  <linearGradient id="ai-grad" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#0071e3"/><stop offset="1" stop-color="#8b5cf6"/>
  </linearGradient>
  <symbol id="sparkle" viewBox="0 0 24 24">
    <path fill="url(#ai-grad)" d="M12 1.6c.9 5.6 4.8 9.5 10.4 10.4-5.6.9-9.5 4.8-10.4 10.4C11.1 16.8 7.2 12.9 1.6 12 7.2 11.1 11.1 7.2 12 1.6Z"/>
  </symbol>
</defs></svg>
<header><div class="bar">
  <div class="logo"><div class="sq">mh</div><div class="name">metaharness</div></div>
  <nav class="pills">
    <button id="nav-home" class="on" onclick="showView('home')">Home</button>
    <button id="nav-wizard" onclick="showView('wizard')">Run</button>
    <button id="nav-settings" onclick="showView('settings')">Settings</button>
    <button id="nav-console" onclick="showView('console')">Console</button>
    <button id="nav-help" onclick="showView('help')">Help</button>
  </nav>
  <div class="spacer"></div>
  <span class="updated" id="updated"></span>
</div></header>

<main>
<!-- ================= HOME (landing) ================= -->
<div id="view-home" class="view">
  <div class="eyebrow" id="home-date"></div>
  <h1 class="greet">Here’s where things stand.</h1>
  <div id="home-next"></div>
  <div class="tiles" id="home-tiles" style="grid-template-columns:repeat(3,1fr)"></div>
  <div class="grid" id="home-rows"></div>
</div>

<!-- ================= WIZARD ================= -->
<div id="view-wizard" class="view" style="display:none">
  <div class="eyebrow">Meta agent harness</div>
  <h1 class="greet" id="wiz-greet">What should the harness do for you?</h1>
  <div class="wiz-grid">
    <div class="stepper" id="stepper"></div>
    <div class="wiz-body" id="wiz-body"></div>
  </div>
</div>

<!-- ================= SETTINGS ================= -->
<div id="view-settings" class="view" style="display:none">
  <div class="eyebrow">Configuration</div>
  <h1 class="greet">Where completions come from, and who does the work.</h1>
  <div class="guide"><div class="fx">ƒ</div><div><b>Why this page exists</b>
    <p>Three questions set up the whole harness — a wizard walks you through each.
    Anything sensitive stays on this machine and is always shown masked.</p></div></div>
  <div id="settings-body"><div class="card"><div class="empty">loading…</div></div></div>
</div>

<!-- ================= CONSOLE ================= -->
<div id="view-console" class="view" style="display:none">
  <div class="eyebrow">Observability</div>
  <h1 class="greet">Everything the harness knows, live.</h1>
  <div class="guide"><div class="fx">ƒ</div><div><b>Why this page exists</b>
    <p>Every run, every agent, and every lesson the harness has learned — in plain
    language, updating live. Click any run for its full story.</p></div>
    <button class="btn cta" onclick="showView('wizard')">Start a run</button></div>
  <div class="tiles" id="tiles"></div>
  <div class="grid">
    <div class="card"><h2>Runs</h2>
      <div class="sub">Newest first — click one to see what each step produced</div>
      <div id="runs" class="empty">loading…</div></div>
    <div class="card"><h2>Agents</h2>
      <div class="sub">Everyone allowed to work here proved who they are first</div>
      <div id="workers" class="empty">loading…</div></div>
    <div class="card"><h2>Audit trail</h2>
      <div class="sub">Every action is signed and chained to the one before it — re-checked on every refresh</div>
      <div id="provenance" class="empty">loading…</div></div>
    <div class="card"><h2>Who’s good at what</h2>
      <div class="sub">Pass rates observed per agent — this is how work gets routed to the right one</div>
      <div id="matrix" class="empty">loading…</div></div>
    <div class="card"><h2>Lessons learned</h2>
      <div class="sub">Advice the harness gives itself, earned from past runs</div>
      <div id="playbook" class="empty">loading…</div></div>
    <div class="card"><h2>Why runs fail</h2>
      <div class="sub">Failures grouped by what actually went wrong, so fixes target the pattern</div>
      <div id="failures" class="empty">loading…</div></div>
    <div class="card"><h2>Harness tuning</h2>
      <div class="sub">The harness experiments on itself — every claim checked on questions it never trained on</div>
      <div id="tuning" class="empty">loading…</div></div>
    <div class="card wide"><h2>Under the hood</h2>
      <div class="sub">Live timing of recent operations, straight from the tracer — intentionally technical</div>
      <div id="spans" class="empty">loading…</div></div>
  </div>
</div>

<!-- ================= HELP ================= -->
<div id="view-help" class="view" style="display:none">
  <div class="eyebrow">Manual</div>
  <h1 class="greet">How to drive the harness.</h1>
  <div class="guide"><div class="fx">ƒ</div><div><b>The one-paragraph version</b>
    <p>You describe an outcome; the harness plans it, routes each step to the cheapest
    agent likely to succeed, verifies every result against a real check, pauses for your
    approval at the risky moments, and learns from what happened. This page explains each
    screen in that story.</p></div></div>
  <div class="grid">
    <div class="card"><h2>Run — start work</h2>
      <div class="sub">A five-step wizard: Agents → Goal → Plan → Run → Done</div>
      <div class="small" style="line-height:1.7">
      <b>Agents</b> shows who is available to work and which capability tier each one fills.<br>
      <b>Goal</b> is where you describe the outcome, not the steps. Free-form goals get
      decomposed by the most capable agent; picking a workflow type (like the software-
      engineering spine) runs your goal through a fixed, verification-gated process.
      The <b>✦ Improve with AI</b> button rewrites a rough goal into a sharper one with a
      checkable done-signal — you always click to accept, nothing changes silently.<br>
      <b>Plan</b> shows every step with its success check before anything runs — edit,
      reorder, or rebuild it by hand.<br>
      <b>Run</b> executes with live progress; steps marked ⛔ park until you approve them.<br>
      <b>Done</b> sums up what passed, what failed, and why.</div></div>
    <div class="card"><h2>Console — watch and decide</h2>
      <div class="sub">Everything the harness knows, refreshed every 3 seconds</div>
      <div class="small" style="line-height:1.7">
      <b>Runs</b> — every run in plain language; click one for step-by-step outputs.
      Approve/Reject buttons appear when a run is waiting on you.<br>
      <b>Agents</b> — the workers that proved their identity, and their tiers.<br>
      <b>Audit trail</b> — every action, signed and hash-chained; if this ever says
      broken, stop trusting results after the breakpoint.<br>
      <b>Who's good at what</b> — observed pass rates per agent; this evidence is how
      work gets routed.<br>
      <b>Lessons learned</b> — advice the harness wrote for itself from verified failures.<br>
      <b>Why runs fail</b> — failures grouped by root-cause pattern.<br>
      <b>Harness tuning</b> — see the card of the same name, below.<br>
      <b>Under the hood</b> — raw operation timings for debugging.</div></div>
    <div class="card"><h2>Harness tuning — the harness improves itself</h2>
      <div class="sub">An automated search over the harness's own configuration</div>
      <div class="small" style="line-height:1.7">
      Pick a suite and hit <b>Tune harness</b>: a proposer studies raw failure traces,
      forms a hypothesis ("arithmetic answers are wrong — let code do the math"), and each
      experiment is scored on reliability (pass^k) versus token cost. ⭐ marks setups on
      the efficiency frontier. A winner must beat the current setup on questions it
      <i>never saw during the search</i>; even then it only becomes live after you click
      <b>Approve</b>. "What this means" turns the results into plain-language findings.</div></div>
    <div class="card"><h2>The ✦ sparkle — AI insights</h2>
      <div class="sub">One icon, one meaning: this content is advisory</div>
      <div class="small" style="line-height:1.7">
      Anything behind a <span class="ai-chip"><svg style="width:11px;height:11px"><use href="#sparkle"/></svg>sparkle</span>
      is generated by an AI companion reading the same data you see. It explains, it
      suggests next steps — but it never executes anything itself and its words are not
      verified facts. Everything <i>without</i> the sparkle (scores, gates, the audit
      trail) is deterministic, checked data. If the two ever disagree, trust the data.</div></div>
    <div class="card"><h2>Settings — providers, agents, tools</h2>
      <div class="sub">Three wizard-driven questions</div>
      <div class="small" style="line-height:1.7">
      <b>Where do completions come from?</b> Add providers (local or cloud); keys stay on
      this machine and are always shown masked.<br>
      <b>Who does the work?</b> Create agents on a provider, pick their tier and system
      prompt; coding CLIs found on this machine can implement plans in real workspaces.<br>
      <b>What can they touch?</b> The tool catalog and MCP servers — each step only ever
      receives the small subset of tools it needs.</div></div>
    <div class="card"><h2>Safety model, in short</h2>
      <div class="sub">Why you can trust what this screen tells you</div>
      <div class="small" style="line-height:1.7">
      Every worker signs its results with a registered key; unsigned work is rejected.
      Every action lands in a hash-chained audit log. No result counts as success without
      an external check, and nothing irreversible happens without your approval —
      including the harness changing its own configuration.</div></div>
  </div>
</div>
</main>
<div class="toast" id="toast"></div>

<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* ---------- humanized output rendering (safe subset, no raw HTML ever) ----------
   Contract: ALL text is escaped before any transform runs, code spans/fences are
   frozen as text before inline markup, and link hrefs are allowlisted to
   http(s)/mailto — worker output is untrusted input. */
function safeHref(href){
  const clean = href.replace(/&amp;/g, '&').trim();
  return /^(https?:|mailto:)/i.test(clean) ? clean : null;
}
function mdInline(t){  // t is already escaped
  const codes = [];
  t = t.replace(/`([^`]+)`/g, (m, c) => {codes.push(c); return '\\u0000' + (codes.length - 1) + '\\u0000';});
  t = t.replace(/\\*\\*([^*]+)\\*\\*/g, '<b>$1</b>')
       .replace(/(^|[\\s(])\\*([^*\\s][^*]*)\\*(?=[\\s).,;:!?]|$)/g, '$1<i>$2</i>')
       .replace(/\\[([^\\]]+)\\]\\(([^)\\s]+)\\)/g, (m, label, href) => {
         const h = safeHref(href);
         return h ? `<a href="${esc(h)}" target="_blank" rel="noopener noreferrer">${label}</a>` : m;
       });
  return t.replace(/\\u0000(\\d+)\\u0000/g, (m, i) => '<code>' + codes[+i] + '</code>');
}
function mdTable(rows){
  const cells = r => r.replace(/^\\s*\\||\\|\\s*$/g, '').split('|').map(c => c.trim());
  const body = rows.filter(r => !/^\\s*\\|?[\\s:|-]+\\|?\\s*$/.test(r));
  if(!body.length) return '';
  const head = cells(body[0]);
  const rest = body.slice(1).map(cells);
  return '<table><tr>' + head.map(h => '<th>' + mdInline(h) + '</th>').join('') + '</tr>' +
    rest.map(r => '<tr>' + r.map(c => '<td>' + mdInline(c) + '</td>').join('') + '</tr>').join('') + '</table>';
}
function renderMarkdown(src){
  const lines = esc(src).split('\\n');
  const out = []; let list = null;
  const closeList = () => {if(list){out.push('</' + list + '>'); list = null;}};
  for(let i = 0; i < lines.length; i++){
    const l = lines[i];
    if(/^\\s*```/.test(l)){  // fenced code: verbatim text, no transforms inside
      const buf = []; i++;
      while(i < lines.length && !/^\\s*```/.test(lines[i])) buf.push(lines[i++]);
      closeList(); out.push('<pre class="md-code">' + buf.join('\\n') + '</pre>'); continue;
    }
    if(/^\\s*\\|.*\\|\\s*$/.test(l)){
      const rows = [l];
      while(i + 1 < lines.length && /^\\s*\\|.*\\|\\s*$/.test(lines[i + 1])) rows.push(lines[++i]);
      closeList(); out.push(mdTable(rows)); continue;
    }
    const h = l.match(/^(#{1,6})\\s+(.*)$/);
    if(h){ closeList(); out.push(`<div class="md-h md-h${h[1].length}">${mdInline(h[2])}</div>`); continue; }
    const li = l.match(/^\\s*[-*]\\s+(.*)$/);
    if(li){ if(list !== 'ul'){closeList(); out.push('<ul>'); list = 'ul';} out.push('<li>' + mdInline(li[1]) + '</li>'); continue; }
    const oli = l.match(/^\\s*\\d+[.)]\\s+(.*)$/);
    if(oli){ if(list !== 'ol'){closeList(); out.push('<ol>'); list = 'ol';} out.push('<li>' + mdInline(oli[1]) + '</li>'); continue; }
    const q = l.match(/^\\s*&gt;\\s?(.*)$/);   // '>' arrives escaped
    if(q){ closeList(); out.push('<blockquote>' + mdInline(q[1]) + '</blockquote>'); continue; }
    if(/^\\s*([-_*])\\s*\\1\\s*\\1[\\s\\-_*]*$/.test(l)){ closeList(); out.push('<hr>'); continue; }
    if(!l.trim()){ closeList(); continue; }
    closeList(); out.push('<p>' + mdInline(l) + '</p>');
  }
  closeList();
  return out.join('');
}
function jsonTree(v, depth = 0){
  if(v === null) return '<span class="jv jnull">null</span>';
  if(typeof v === 'string') return '<span class="jv jstr">"' + esc(v) + '"</span>';
  if(typeof v === 'number') return '<span class="jv jnum">' + v + '</span>';
  if(typeof v === 'boolean') return '<span class="jv jbool">' + v + '</span>';
  const isArr = Array.isArray(v);
  const keys = isArr ? null : Object.keys(v);
  const n = isArr ? v.length : keys.length;
  if(!n) return '<span class="jv jnull">' + (isArr ? '[]' : '{}') + '</span>';
  const label = isArr ? `[${n} item${n === 1 ? '' : 's'}]` : `{${n} key${n === 1 ? '' : 's'}}`;
  const rows = (isArr ? v.map((x, i) => [i, x]) : keys.map(k => [k, v[k]]))
    .map(([k, x]) => `<div class="jrow"><span class="jk">${esc(k)}</span>${jsonTree(x, depth + 1)}</div>`).join('');
  return `<details class="jt"${depth < 2 ? ' open' : ''}><summary>${label}</summary>${rows}</details>`;
}
function looksMarkdown(s){
  return /(^|\\n)#{1,6}\\s|(^|\\n)\\s*[-*]\\s+\\S|\\*\\*[^*]+\\*\\*|```|(^|\\n)\\s*\\|.+\\|/.test(s);
}
function humanizeOutput(v){
  if(v !== null && v !== undefined && typeof v === 'object')
    return '<div class="out json">' + jsonTree(v) + '</div>';
  const s = String(v ?? '');
  const t = s.trim();
  if(t.startsWith('{') || t.startsWith('[')){
    try{ return '<div class="out json">' + jsonTree(JSON.parse(t)) + '</div>'; }catch(e){}
  }
  if(looksMarkdown(s)) return '<div class="out md">' + renderMarkdown(s) + '</div>';
  return '<div class="out">' + esc(s) + '</div>';
}
const get = async p => (await fetch(p)).json();
const post = (p, body) => fetch(p, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
const badge = (cls, text) => `<span class="badge ${cls}">${esc(text)}</span>`;
const statusBadge = s => badge({completed:'ok', failed:'bad', awaiting_approval:'warn', running:'act'}[s] || 'dim',
  {completed:'done', failed:'failed', awaiting_approval:'needs you', running:'running'}[s] || String(s).replace('_',' '));

/* plain-language helpers: humans read goals and relative time, not ids and epochs */
function ago(ts){
  if(!ts) return '';
  const s = Math.max(0, Date.now()/1000 - ts);
  if(s < 50) return 'just now';
  const m = Math.round(s/60); if(m < 60) return m + ' min ago';
  const h = Math.round(s/3600); if(h < 24) return h + (h === 1 ? ' hour ago' : ' hours ago');
  const d = Math.round(s/86400); if(d < 7) return d + (d === 1 ? ' day ago' : ' days ago');
  return new Date(ts*1000).toLocaleDateString();
}
const stepName = id => String(id || '').replace(/[-_]/g, ' ');
function runTitle(r){
  const goal = (r.context || {}).goal;   // context is arbitrary JSON — only trust strings
  if(typeof goal === 'string' && goal.trim())
    return goal.charAt(0).toUpperCase() + goal.slice(1);
  let t = String(r.workflow || r.run_id || '');
  if(t.includes(':')) t = t.slice(t.indexOf(':') + 1);   // strip template prefix
  if(!t.includes(' ')) t = t.replace(/[-_]/g, ' ');       // slug -> words
  return t.charAt(0).toUpperCase() + t.slice(1);
}
function runKind(r){
  const w = r.workflow || '';
  return w.includes(':') ? w.split(':')[0].replace(/[-_]/g, ' ') : '';
}
function runStory(r){
  const recs = Object.values(r.completed || {});
  const n = recs.length, v = recs.filter(x => x.verdict === 'pass').length;
  const steps = c => c + (c === 1 ? ' step' : ' steps');
  if(r.status === 'running') return n ? `Working — ${steps(n)} done so far` : 'Working — starting up';
  if(r.status === 'awaiting_approval') return `Paused — “${stepName(r.awaiting)}” needs your approval`;
  if(r.status === 'completed') return v === n && n
    ? `Finished — all ${steps(n)} checked out`
    : `Finished — ${steps(n)} done, ${v} verified`;
  if(r.status === 'failed') return r.failed_step
    ? `Stopped at “${stepName(r.failed_step)}” — open it to see why`
    : 'Stopped before it could finish';
  return '';
}
function toast(msg){ const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('on'); setTimeout(() => t.classList.remove('on'), 2600); }

/* ---------- view switching ---------- */
function showView(v){
  for(const name of ['home','wizard','settings','console','help']){
    document.getElementById('view-' + name).style.display = v === name ? '' : 'none';
    document.getElementById('nav-' + name).classList.toggle('on', v === name);
  }
  if(v === 'home') renderHome();
  if(v === 'console') refreshConsole();
  if(v === 'settings') renderSettings(true);
  if(v === 'wizard' && wiz.step === 0) renderAgentsStep();  // agents may have changed
}

/* ---------- home: what needs doing, at a glance ---------- */
function nextActions(runs, workers, prov, tuning){
  const q = [];
  if(!prov.chain.ok) q.push({eyebrow: 'integrity alert',
    title: 'The audit trail is broken',
    detail: 'Something altered the signed action log. Results after the break point cannot be trusted — inspect it before anything else.',
    cta: 'Inspect in Console', go: 'console'});
  runs.filter(r => r.status === 'awaiting_approval').forEach(r =>
    q.push({eyebrow: 'a run is waiting on you',
      title: 'Approve or reject: ' + runTitle(r),
      detail: 'The run is parked at a human gate — nothing proceeds until you decide.',
      cta: 'Review in Console', go: 'console'}));
  tuning.filter(s => s.pending).forEach(s =>
    q.push({eyebrow: 'the harness wants to improve itself',
      title: `Promote ${s.pending.candidate} on the ${s.suite} suite?`,
      detail: 'A tuned setup beat the current one on questions it never saw. It only goes live with your approval.',
      cta: 'Decide in Console', go: 'console'}));
  if(!workers.some(w => w.active)) q.push({eyebrow: 'setup',
    title: 'Give the harness someone to work with',
    detail: 'No agents are registered yet. Add a provider and an agent in Settings — three questions, wizard-guided.',
    cta: 'Open Settings', go: 'settings'});
  if(!runs.length) q.push({eyebrow: 'nothing has run yet',
    title: 'Start your first run',
    detail: 'Describe the outcome you want; the harness plans it, verifies every step, and asks before anything risky.',
    cta: 'Open the Run wizard', go: 'wizard'});
  if(!q.length) q.push({eyebrow: 'all quiet',
    title: 'Nothing needs you right now',
    detail: 'Runs are flowing and every check is green. Start something new, or let the harness tune itself.',
    cta: 'Start a run', go: 'wizard'});
  return q;
}

async function renderHome(){
  try{
    const [runs, workers, prov, playbook, tuning] = await Promise.all([
      get('/api/runs'), get('/api/workers'), get('/api/provenance'),
      get('/api/playbook'), get('/api/optimization')]);
    document.getElementById('home-date').textContent =
      new Date().toLocaleDateString(undefined, {weekday:'long', month:'long', day:'numeric'});
    const q = nextActions(runs, workers, prov, tuning);
    const first = q[0];
    document.getElementById('home-next').innerHTML = `<div class="next-action">
      <div class="txt"><div class="eyebrow">${esc(first.eyebrow)}</div>
      <h2>${esc(first.title)}</h2><p>${esc(first.detail)}</p></div>
      <button class="btn" onclick="showView('${first.go}')">${esc(first.cta)}</button></div>`
      + (q.length > 1 ? `<div class="also">Also waiting: ${q.slice(1).map(x => esc(x.title)).join('  ·  ')}</div>` : '');

    const active = runs.filter(r => ['running','awaiting_approval'].includes(r.status)).length;
    const done = runs.filter(r => r.status === 'completed').length;
    document.getElementById('home-tiles').innerHTML = `
      <div class="tile"><div class="val">${runs.length}</div>
        <div class="lab">runs so far — ${active} active, ${done} finished</div></div>
      <div class="tile"><div class="val">${workers.filter(w => w.active).length}</div>
        <div class="lab">agents ready to work</div></div>
      <div class="tile"><div class="val ${prov.chain.ok ? 'green' : 'red'}">${prov.chain.ok ? '✔' : '✘'} ${prov.total}</div>
        <div class="lab">${prov.chain.ok ? 'signed actions — audit trail intact' : 'audit trail BROKEN'}</div></div>`;

    const latest = runs[runs.length - 1];
    const tuned = tuning.find(s => s.active && s.promoted) || tuning.find(s => s.promoted);
    document.getElementById('home-rows').innerHTML = `
      <div class="card"><h2>Latest result</h2>
        <div class="sub">The most recent thing the harness did</div>
        ${latest ? `<div class="lrow"><div class="rr-main">
            <div class="rr-title">${esc(runTitle(latest))}</div>
            <div class="rr-meta">${esc(latest.run_id)}${latest.updated_at ? ' · ' + esc(ago(latest.updated_at)) : ''}</div></div>
          <div class="rr-story">${esc(runStory(latest))}</div>${statusBadge(latest.status)}</div>`
          : '<div class="empty">nothing yet — your first run will land here</div>'}
        <div style="margin-top:10px"><button class="btn ghost" onclick="showView('console')">Open the Console</button></div></div>
      <div class="card"><h2>Self-tuning</h2>
        <div class="sub">The harness improving its own configuration</div>
        ${tuned ? `<div class="lrow"><div class="rr-main">
            <div class="rr-title">${esc(tuned.promoted.candidate)} ${tuned.active ? 'is live' : 'is promoted'}
              ${badge('ok', tuned.active ? 'live now' : 'applies at restart')}</div>
            <div class="rr-meta">${esc(tuned.suite)} suite · approved</div></div>
          <div class="rr-story">every claim was checked on questions the search never saw</div></div>`
          : '<div class="empty">no tuned configuration live yet</div>'}
        <div style="margin-top:10px"><button class="btn ghost" onclick="showView('console')">Tune the harness</button></div></div>
      <div class="card wide"><h2>New here?</h2>
        <div class="sub">Three ways to get oriented</div>
        <div class="lrow"><div class="rr-main"><div class="rr-title">What am I looking at?</div></div>
          <button class="pill" onclick="showView('help')">Read the manual</button></div>
        <div class="lrow"><div class="rr-main"><div class="rr-title">What does the ✦ sparkle mean?</div></div>
          <button class="pill" onclick="showView('help')">AI insights, explained</button></div>
        <div class="lrow"><div class="rr-main"><div class="rr-title">Ready to try it?</div></div>
          <button class="pill" onclick="showView('wizard')">Start a run</button></div></div>`;
  }catch(e){
    document.getElementById('home-next').innerHTML = '<div class="empty">could not load harness state — is the server healthy?</div>';
  }
}

/* ---------- wizard state machine ---------- */
const STEPS = ['Agents','Goal','Plan','Run','Done'];
const wiz = { step: 0, goal: '', context: {}, workflowType: '', plan: null, planSource: '',
              editingStep: null, builderMode: false, builder: null, yamlMode: false,
              yamlText: '', edited: false, runId: null, run: null, poller: null,
              pinnedStep: null, fallbackReason: '' };

function renderStepper(){
  document.getElementById('stepper').innerHTML = STEPS.map((label, i) =>
    `<div class="s ${i === wiz.step ? 'on' : ''} ${i < wiz.step ? 'done' : ''}">
       <div class="n">${i < wiz.step ? '✓' : i + 1}</div><div class="l">${label}</div></div>`).join('');
}

function setStep(n){
  wiz.step = n;
  renderStepper();
  [renderAgentsStep, renderGoalStep, renderPlanStep, renderRunStep, renderDoneStep][n]();
  document.getElementById('wiz-greet').textContent = [
    'First: which models do the work?',
    'What should the harness do for you?',
    'Review the plan before it runs.',
    'The harness is working.',
    'Done. Here is what came back.',
  ][n];
}

/* ---------- step 1: agents ---------- */
async function renderAgentsStep(){
  const body = document.getElementById('wiz-body');
  body.innerHTML = '<div class="card"><div class="empty">loading agents…</div></div>';
  const workers = await get('/api/workers');
  const agents = workers.filter(w => w.worker_id !== 'orchestrator' && w.active);
  const byTier = {};
  agents.forEach(w => (w.tiers || []).forEach(t => { byTier[t] = w; }));
  const tiers = ['small','mid','frontier'].map(t => {
    const w = byTier[t];
    return `<div class="tierrow"><div class="tn">${t}</div>
      <div style="flex:1">${w
        ? `<div class="tm">${esc(w.display_name)}</div><div class="td mono">${esc(w.worker_id)} · key ${esc(w.public_key_b64.slice(0,12))}…</div>`
        : '<div class="td">no agent — this tier can\\'t take work</div>'}</div>
      ${w ? badge('ok','ready') : badge('dim','empty')}</div>`;
  }).join('');
  body.innerHTML = `
    <div class="guide"><div><b>Agents do the work; tiers set the cost ladder.</b>
      <p>The harness routes each step to the cheapest tier likely to succeed and escalates on verified failure.
      The frontier agent also plans your workflows. Defaults are fine — just continue.</p></div></div>
    <div class="card"><h2>Tier assignments</h2><div class="sub">Who answers when the router calls</div>${tiers}
      <div style="margin-top:14px;display:flex;gap:10px">
        <button class="btn ghost" onclick="openAgentWizard()">+ Add an agent</button>
        <button class="btn ghost" onclick="showView('settings')">Open Settings</button></div></div>
    <div class="wiz-nav"><span></span>
      <button class="btn" ${Object.keys(byTier).length ? '' : 'disabled'} onclick="setStep(1)">Continue →</button></div>`;
}

function openAgentWizard(prefill){
  showView('settings');
  startAgentWizard(prefill || null);
}

/* ---------- step 2: goal ---------- */
let WORKFLOW_TYPES = null;
async function loadWorkflowTypes(){
  if(WORKFLOW_TYPES === null){
    try{ WORKFLOW_TYPES = await get('/api/workflow-types'); }catch(e){ WORKFLOW_TYPES = []; }
  }
  return WORKFLOW_TYPES;
}

function pickWorkflowType(id){
  wiz.workflowType = id;
  renderGoalStep();
}

/* ---------- goal prompt assistant (AI companion) ---------- */
let GOAL_ADVICE = null;
async function adviseGoal(){
  const goal = document.getElementById('goal').value.trim();
  const panel = document.getElementById('goal-advice');
  if(!goal){ panel.innerHTML = '<div class="empty">write a rough goal first — the assistant sharpens it</div>'; return; }
  panel.innerHTML = '<div class="empty">thinking…</div>';
  document.getElementById('advise-goal-btn').disabled = true;
  try{
    const r = await post('/api/advise', {page: 'goal', subject: goal});
    if(!r.ok) throw new Error('advisor unavailable');
    GOAL_ADVICE = await r.json();
    const actions = GOAL_ADVICE.next_actions
      .map((a, i) => a.action === 'prefill_goal'
        ? `<button class="btn small" onclick="applyGoalAdvice(${i})">${esc(a.label)}</button>` : '')
      .join('');
    panel.innerHTML = `<div class="advisor" style="margin-top:10px"><div class="takes">
      <div class="h">Assistant’s rewrite <span class="ai-chip"><svg style="width:11px;height:11px"><use href="#sparkle"/></svg>advisory, not verified</span></div>
      <p>${esc(GOAL_ADVICE.read)}</p><div class="nba">${actions}</div></div></div>`;
  }catch(e){
    panel.innerHTML = '<div class="empty">the assistant is unavailable right now — your goal works as written</div>';
  }
  document.getElementById('advise-goal-btn').disabled = false;
}
function applyGoalAdvice(i){
  const p = (GOAL_ADVICE.next_actions[i] || {}).params || {};
  if(p.goal) document.getElementById('goal').value = p.goal;
  if(p.context) document.getElementById('goalctx').value =
    typeof p.context === 'string' ? p.context : JSON.stringify(p.context);
  toast('Applied — review it, then plan the workflow');
}

async function renderGoalStep(){
  const types = await loadWorkflowTypes();
  const chosen = types.find(t => t.id === wiz.workflowType);
  const typePills = ['<button class="pill ' + (wiz.workflowType ? '' : 'on') + '" onclick="pickWorkflowType(\\'\\')">Free-form (planner)</button>']
    .concat(types.map(t => `<button class="pill ${wiz.workflowType === t.id ? 'on' : ''}" onclick="pickWorkflowType('${esc(t.id)}')">${esc(t.label)}</button>`))
    .concat([`<button class="pill ${wiz.workflowType === '__custom__' ? 'on' : ''}" onclick="pickWorkflowType('__custom__')">Custom (build by hand)</button>`])
    .join('');
  const typeNote = wiz.workflowType === '__custom__'
    ? `<div class="hint-panel"><b>Custom workflow</b>
        No planner involved: you get one empty step and the full editor —
        add steps, wire dependencies, pick tools, set gates. YAML mode available.</div>`
    : chosen
    ? `<div class="hint-panel"><b>${esc(chosen.label)} — deterministic phase spine</b>
        ${esc(chosen.description)}<br>
        <span class="kv">${chosen.phases.map(p => p.id + (p.hitl ? ' ⛔' : '')).join(' → ')}</span>
        <span class="small dim"> (⛔ = waits for your approval)</span></div>`
    : '';
  document.getElementById('wiz-body').innerHTML = `
    <div class="guide"><div><b>Describe the outcome, not the steps.</b>
      <p>Free-form goals are decomposed by the frontier agent; picking a workflow type runs
      your goal through that named process with its built-in verification gates.
      Put any data the task needs into context — steps reference it by key.</p></div></div>
    <div class="card">
      <div class="field"><label>Workflow type</label>
        <div class="pillrow">${typePills}</div>${typeNote}</div>
      <div class="field"><label>Goal</label>
        <textarea id="goal" placeholder="e.g. Read the incident report in context, classify severity as exactly low or high, summarize it for on-call, and draft the page for my approval.">${esc(wiz.goal)}</textarea></div>
      <div class="field"><label>Context (JSON, optional)</label>
        <input id="goalctx" class="mono" placeholder='{"report": "db-1 disk full, checkout failing"}'
          value="${esc(Object.keys(wiz.context).length ? JSON.stringify(wiz.context) : '')}"></div>
      <span class="small dim" id="goalmsg"></span>
      <div style="margin-top:6px"><button class="btn ghost" onclick="adviseGoal()" id="advise-goal-btn">
        <svg style="width:13px;height:13px"><use href="#sparkle"/></svg> Improve with AI</button></div>
      <div id="goal-advice"></div></div>
    <div class="wiz-nav">
      <button class="btn ghost" onclick="setStep(0)">← Agents</button>
      <button class="btn" id="planbtn" onclick="makePlan()">Plan workflow →</button></div>`;
}

async function makePlan(){
  const msg = document.getElementById('goalmsg');
  const btn = document.getElementById('planbtn');
  wiz.goal = document.getElementById('goal').value.trim();
  if(!wiz.goal){ msg.textContent = 'describe a goal first'; return; }
  const ctxRaw = document.getElementById('goalctx').value.trim();
  wiz.context = {};
  if(ctxRaw){ try{ wiz.context = JSON.parse(ctxRaw); }catch(e){ msg.textContent = 'context is not valid JSON'; return; } }
  if(wiz.workflowType === '__custom__'){
    wiz.plan = {name: wiz.goal.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40) || 'custom',
                steps: [{id: 'step-1', task_type: 'general', objective: wiz.goal,
                         inputs: {goal: '$context.goal'}, boundaries: [], tools: [],
                         depends_on: [], hitl: false, success_check: null}]};
    wiz.plan.steps = [];  // wizard-driven: steps are added one by one
    wiz.planSource = 'custom';
    wiz.builderMode = true;
    wiz.builder = null;
    setStep(2);
    return;
  }
  btn.disabled = true;
  msg.innerHTML = wiz.workflowType
    ? '<span class="spin"></span> expanding the workflow template…'
    : '<span class="spin"></span> the frontier agent is planning — this can take a minute on local models…';
  const r = await post('/api/plans', {goal: wiz.goal, context: wiz.context,
                                      workflow_type: wiz.workflowType || ''});
  btn.disabled = false;
  if(!r.ok){ msg.textContent = 'planning failed: ' + (await r.text()).slice(0,140); return; }
  const data = await r.json();
  wiz.plan = data.workflow; wiz.planSource = data.plan_source;
  wiz.fallbackReason = data.fallback_reason || '';
  setStep(2);
}

/* ---------- step 3: plan review + editor + builder ---------- */
const TASK_TYPES = ['classify','extract','summarize','transform','arithmetic',
                    'code_edit','reasoning','planning','general'];
let TOOLS_NAMES = null;
async function loadToolNames(){
  if(TOOLS_NAMES === null){
    try{ TOOLS_NAMES = (await get('/api/tools')).map(t => t.name); }catch(e){ TOOLS_NAMES = []; }
  }
  return TOOLS_NAMES;
}

function checkOf(step){
  const c = step.success_check;
  if(!c) return {kind: 'none', value: ''};
  const kind = Object.keys(c)[0];
  const v = c[kind];
  return {kind, value: Array.isArray(v) ? v.join(', ') : String(v)};
}

function buildWhen(stepId, kind, value){
  if(!stepId || !value.trim()) return null;
  const v = kind === 'one_of'
    ? value.split(',').map(x => x.trim()).filter(Boolean) : value.trim();
  return {step: stepId, [kind]: v};
}

function buildCheck(kind, value){
  if(kind === 'none' || !value.trim()) return null;
  if(kind === 'one_of') return {one_of: value.split(',').map(x => x.trim()).filter(Boolean)};
  return {[kind]: value.trim()};
}

function whenBadge(st){
  if(!st.when) return '';
  const kind = ['equals','contains','one_of'].find(k => k in st.when);
  const v = Array.isArray(st.when[kind]) ? st.when[kind].join(', ') : st.when[kind];
  return badge('dim', `${st.when.negate ? 'unless' : 'if'} ${st.when.step} ${kind} ${v}`);
}

function planNote(){
  const src = wiz.planSource || '';
  const base = src.startsWith('template:')
    ? badge('act', src.replace('template:', '') + ' template')
    : src === 'planner' ? badge('act', 'planned by frontier agent')
    : src === 'custom' ? badge('act', 'custom — built by hand')
    : src === 'followup' ? badge('act', 'follow-up — planned from the last run')
    : badge('warn', 'fallback: single step');
  const why = (src === 'fallback' && wiz.fallbackReason)
    ? `<div class="small dim" style="margin-top:4px">planner fell back: ${esc(wiz.fallbackReason)}</div>` : '';
  return base + (wiz.edited ? ' ' + badge('ok', 'edited by you') : '') + why;
}

async function renderPlanStep(){
  await loadToolNames();
  if(wiz.builderMode){
    if(!wiz.builder) wiz.builder = {sub: 0, draft: newDraft()};
    return renderStepBuilder();
  }
  if(wiz.yamlMode) return renderYamlEditor();
  const p = wiz.plan;
  document.getElementById('wiz-body').innerHTML = `
    <div class="guide"><div><b>Nothing has run yet — and every step is editable.</b>
      <p>✎ edits a step, ↑↓ reorder, ✕ removes, + adds one via the step wizard.
      Steps marked HITL pause for your approval. YAML mode has every advanced field.</p></div></div>
    <div class="card"><h2>${esc(p.name)}</h2><div class="sub">${planNote()}</div>
      ${p.steps.map((st, i) => wiz.editingStep === i ? stepEditForm(st, i) : `
        <div class="planstep"><div class="n">${i + 1}</div>
          <div style="flex:1"><div class="pt">${esc(st.id)}
            ${badge('dim', st.task_type)}${st.hitl ? badge('warn','HITL — waits for you') : ''}
            ${st.success_check ? badge('ok','verifiable') : ''}${whenBadge(st)}
            ${(st.tools || []).map(t => badge('act','🔧 ' + t)).join('')}</div>
          <div class="pd">${esc(st.objective)}</div>
          ${(st.depends_on || []).length ? `<div class="pd mono">after: ${esc(st.depends_on.join(', '))}</div>` : ''}</div>
          <div class="step-actions">
            <button title="edit" onclick="editStep(${i})">✎</button>
            <button title="move up" onclick="moveStep(${i},-1)" ${i ? '' : 'disabled'}>↑</button>
            <button title="move down" onclick="moveStep(${i},1)" ${i < p.steps.length - 1 ? '' : 'disabled'}>↓</button>
            <button title="remove" onclick="deleteStep(${i})">✕</button></div></div>`).join('')}
      <div style="margin-top:14px;display:flex;gap:10px">
        <button class="btn ghost" onclick="openStepBuilder()">+ Add step (wizard)</button>
        <button class="btn ghost" onclick="openYaml()">Edit as YAML</button></div>
      <div class="small red" id="planmsg" style="margin-top:8px"></div></div>
    <div class="wiz-nav">
      <button class="btn ghost" onclick="setStep(1)">← Rephrase goal</button>
      <button class="btn" onclick="runValidatedPlan()" ${p.steps.length ? '' : 'disabled'}>Run this plan →</button></div>`;
}

/* -- inline step editor (works on ANY plan: LLM, template, custom) -- */
function stepEditForm(st, i){
  const check = checkOf(st);
  return `<div class="step-edit">
    <div class="field" style="display:flex;gap:10px">
      <span style="width:180px"><label>Step id</label>
        <input id="se-id" class="mono" value="${esc(st.id)}"></span>
      <span style="flex:1"><label>Task type</label><select id="se-type">
        ${TASK_TYPES.map(t => `<option ${st.task_type === t ? 'selected' : ''}>${t}</option>`).join('')}</select></span></div>
    <div class="field"><label>Objective — the full delegation contract for this step</label>
      <textarea id="se-obj">${esc(st.objective)}</textarea></div>
    <div class="field"><label>Tools this step may call</label>
      <div class="pillrow">${(TOOLS_NAMES || []).map(t =>
        `<button class="tool-toggle ${(st.tools || []).includes(t) ? 'on' : ''}" onclick="this.classList.toggle('on')" data-tool="${esc(t)}">${esc(t)}</button>`).join('')}</div></div>
    <div class="field" style="display:flex;gap:10px">
      <span style="width:160px"><label>Success check</label><select id="se-check">
        ${['none','equals','contains','one_of'].map(k => `<option ${check.kind === k ? 'selected' : ''}>${k}</option>`).join('')}</select></span>
      <span style="flex:1"><label>Check value (one_of: comma-separated)</label>
        <input id="se-checkval" class="mono" value="${esc(check.value)}"></span></div>
    <div class="field" style="display:flex;gap:10px">
      <span style="flex:1"><label>Depends on (comma-separated step ids)</label>
        <input id="se-deps" class="mono" value="${esc((st.depends_on || []).join(', '))}"></span>
      <span style="width:190px;align-self:end"><label style="display:flex;gap:8px;align-items:center">
        <input type="checkbox" id="se-hitl" ${st.hitl ? 'checked' : ''} style="width:auto"> HITL gate</label></span></div>
    <div class="field" style="display:flex;gap:10px">
      <span style="width:220px"><label>Only run when (branch)</label><select id="se-when-step">
        <option value="">— always —</option>
        ${wiz.plan.steps.filter(o => o.id !== st.id).map(o =>
          `<option ${st.when && st.when.step === o.id ? 'selected' : ''}>${esc(o.id)}</option>`).join('')}</select></span>
      <span style="width:140px"><label>Condition</label><select id="se-when-kind">
        ${['equals','contains','one_of'].map(k => {
          const cur = st.when ? ['equals','contains','one_of'].find(x => x in st.when) : 'equals';
          return `<option ${cur === k ? 'selected' : ''}>${k}</option>`;}).join('')}</select></span>
      <span style="flex:1"><label>Value (one_of: comma-separated)</label>
        <input id="se-when-val" class="mono" value="${esc(st.when ? (Array.isArray(st.when[['equals','contains','one_of'].find(x => x in st.when)]) ? st.when[['equals','contains','one_of'].find(x => x in st.when)].join(', ') : st.when[['equals','contains','one_of'].find(x => x in st.when)]) : '')}"></span></div>
    <div style="display:flex;gap:10px">
      <button class="btn" onclick="saveStep(${i})">Save step</button>
      <button class="btn ghost" onclick="wiz.editingStep=null;renderPlanStep()">Cancel</button></div></div>`;
}

function editStep(i){ wiz.editingStep = i; renderPlanStep(); }

function collectStepForm(){
  return {
    id: document.getElementById('se-id').value.trim(),
    task_type: document.getElementById('se-type').value,
    objective: document.getElementById('se-obj').value.trim(),
    tools: [...document.querySelectorAll('.step-edit .tool-toggle.on')].map(b => b.dataset.tool),
    success_check: buildCheck(document.getElementById('se-check').value,
                              document.getElementById('se-checkval').value),
    depends_on: document.getElementById('se-deps').value.split(',').map(x => x.trim()).filter(Boolean),
    hitl: document.getElementById('se-hitl').checked,
    when: buildWhen(document.getElementById('se-when-step').value,
                    document.getElementById('se-when-kind').value,
                    document.getElementById('se-when-val').value),
  };
}

function saveStep(i){
  const edit = collectStepForm();
  if(!edit.id){ toast('the step needs an id'); return; }
  if(!edit.objective){ toast('the step needs an objective'); return; }
  if(wiz.plan.steps.some((st, j) => j !== i && st.id === edit.id)){
    toast(`step id ${edit.id} is already used`); return; }
  wiz.plan.steps[i] = Object.assign({}, wiz.plan.steps[i], edit);
  wiz.editingStep = null; wiz.edited = true;
  renderPlanStep();
}

function deleteStep(i){
  const removed = wiz.plan.steps.splice(i, 1)[0];
  wiz.plan.steps.forEach(st => {
    st.depends_on = (st.depends_on || []).filter(d => d !== removed.id); });
  wiz.editingStep = null; wiz.edited = true;
  renderPlanStep();
}

function moveStep(i, delta){
  const j = i + delta;
  if(j < 0 || j >= wiz.plan.steps.length) return;
  [wiz.plan.steps[i], wiz.plan.steps[j]] = [wiz.plan.steps[j], wiz.plan.steps[i]];
  wiz.edited = true;
  renderPlanStep();
}

async function runValidatedPlan(){
  const msg = document.getElementById('planmsg');
  msg.textContent = '';
  const r = await post('/api/workflows/validate', {workflow: wiz.plan});
  if(!r.ok){
    msg.textContent = 'invalid workflow: ' + JSON.parse(await r.text()).detail;
    return;
  }
  wiz.plan = (await r.json()).workflow;  // normalized
  startRun();
}

/* -- step builder: wizard-driven custom workflow authoring -- */
function newDraft(){
  return {id: `step-${wiz.plan.steps.length + 1}`, task_type: 'general', objective: '',
          tools: [], hitl: false, depends_on: [], check_kind: 'none', check_value: '',
          when_step: '', when_kind: 'equals', when_value: ''};
}

function openStepBuilder(){
  wiz.builderMode = true;
  wiz.builder = {sub: 0, draft: newDraft()};
  renderPlanStep();
}

function renderStepBuilder(){
  const b = wiz.builder;
  const d = b.draft;
  const chips = ['Objective', 'Type & tools', 'Verify & gate'].map((l, i) =>
    `<span class="t ${i === b.sub ? 'on' : ''} ${i < b.sub ? 'done' : ''}">${i + 1} · ${l}</span>`).join('');
  let inner = '';
  if(b.sub === 0){
    inner = `
      <div class="hint-panel"><b>A step is one delegation contract</b>
        Say exactly what the worker must produce — outcome, not process. Mention
        checkable expectations ("respond with exactly one of: low, high") and the
        verify sub-step can enforce them mechanically.</div>
      <div class="field"><label>Step id</label>
        <input id="sb-id" class="mono" value="${esc(d.id)}"></div>
      <div class="field"><label>Objective</label>
        <textarea id="sb-obj" placeholder="e.g. Classify the ticket severity as exactly one of: low, high.">${esc(d.objective)}</textarea></div>`;
  }else if(b.sub === 1){
    inner = `
      <div class="field"><label>Task type — routes the step to the right tier</label>
        <div class="pillrow">${TASK_TYPES.map(t =>
          `<button class="pill ${d.task_type === t ? 'on' : ''}" onclick="wiz.builder.draft.task_type='${t}';renderPlanStep()">${t}</button>`).join('')}</div></div>
      <div class="field"><label>Tools (only what this step truly needs — fewer is better)</label>
        <div class="pillrow">${(TOOLS_NAMES || []).map(t =>
          `<button class="tool-toggle ${d.tools.includes(t) ? 'on' : ''}" onclick="toggleDraftTool('${esc(t)}')">${esc(t)}</button>`).join('')}</div></div>`;
  }else{
    const prior = wiz.plan.steps.map(st => st.id);
    inner = `
      <div class="field" style="display:flex;gap:10px">
        <span style="width:160px"><label>Success check</label><select id="sb-check">
          ${['none','equals','contains','one_of'].map(k => `<option ${d.check_kind === k ? 'selected' : ''}>${k}</option>`).join('')}</select></span>
        <span style="flex:1"><label>Check value (one_of: comma-separated)</label>
          <input id="sb-checkval" class="mono" value="${esc(d.check_value)}"></span></div>
      <div class="field"><label>Runs after (dependencies)</label>
        ${prior.length ? `<div class="pillrow">${prior.map(id =>
          `<button class="pill ${d.depends_on.includes(id) ? 'on' : ''}" onclick="toggleDraftDep('${esc(id)}')">${esc(id)}</button>`).join('')}</div>`
          : '<span class="small dim">first step — nothing to depend on yet</span>'}</div>
      <div class="field"><label style="display:flex;gap:8px;align-items:center">
        <input type="checkbox" id="sb-hitl" ${d.hitl ? 'checked' : ''} style="width:auto">
        HITL gate — pause for my approval before this step runs</label></div>
      ${prior.length ? `<div class="field" style="display:flex;gap:10px">
        <span style="width:200px"><label>Only run when (branch)</label><select id="sb-when-step">
          <option value="">— always —</option>
          ${prior.map(id => `<option ${d.when_step === id ? 'selected' : ''}>${esc(id)}</option>`).join('')}</select></span>
        <span style="width:130px"><label>Condition</label><select id="sb-when-kind">
          ${['equals','contains','one_of'].map(k => `<option ${d.when_kind === k ? 'selected' : ''}>${k}</option>`).join('')}</select></span>
        <span style="flex:1"><label>Value</label>
          <input id="sb-when-val" class="mono" value="${esc(d.when_value || '')}" placeholder="e.g. high"></span></div>` : ''}`;
  }
  const added = wiz.plan.steps.map((st, i) =>
    `<div class="small" style="padding:3px 0"><span class="mono">${i + 1}. ${esc(st.id)}</span>
     ${badge('dim', st.task_type)}${st.hitl ? badge('warn','gate') : ''}</div>`).join('');
  document.getElementById('wiz-body').innerHTML = `
    <div class="card"><h2>Add step ${wiz.plan.steps.length + 1} — ${esc(wiz.plan.name)}</h2>
      <div class="subwiz-steps">${chips}</div>${inner}
      <div class="wiz-nav">
        <button class="btn ghost" onclick="builderNav(-1)">${b.sub === 0 ? 'Cancel' : '← Back'}</button>
        ${b.sub < 2
          ? `<button class="btn" onclick="builderNav(1)">Next →</button>`
          : `<button class="btn" onclick="builderCommit()">Add step to workflow</button>`}</div></div>
    ${wiz.plan.steps.length ? `<div class="card" style="margin-top:16px"><h2>Workflow so far</h2>${added}
      <div style="margin-top:10px"><button class="btn" onclick="wiz.builderMode=false;renderPlanStep()">Done — review workflow →</button></div></div>` : ''}`;
}

function toggleDraftTool(t){
  const tools = wiz.builder.draft.tools;
  tools.includes(t) ? tools.splice(tools.indexOf(t), 1) : tools.push(t);
  renderPlanStep();
}

function toggleDraftDep(id){
  const deps = wiz.builder.draft.depends_on;
  deps.includes(id) ? deps.splice(deps.indexOf(id), 1) : deps.push(id);
  renderPlanStep();
}

function builderCapture(){
  const d = wiz.builder.draft;
  const grab = id => { const el = document.getElementById(id); return el ? el.value : null; };
  const v = grab('sb-id'); if(v !== null) d.id = v.trim();
  const o = grab('sb-obj'); if(o !== null) d.objective = o.trim();
  const c = grab('sb-check'); if(c !== null) d.check_kind = c;
  const cv = grab('sb-checkval'); if(cv !== null) d.check_value = cv;
  const h = document.getElementById('sb-hitl'); if(h) d.hitl = h.checked;
  const ws = grab('sb-when-step'); if(ws !== null) d.when_step = ws;
  const wk = grab('sb-when-kind'); if(wk !== null) d.when_kind = wk;
  const wv = grab('sb-when-val'); if(wv !== null) d.when_value = wv;
}

function builderNav(delta){
  builderCapture();
  const b = wiz.builder;
  if(b.sub === 0 && delta < 0){
    wiz.builderMode = false;
    if(!wiz.plan.steps.length && wiz.planSource === 'custom') setStep(1);
    else renderPlanStep();
    return;
  }
  if(b.sub === 0 && delta > 0 && !b.draft.objective){ toast('give the step an objective'); return; }
  b.sub = Math.max(0, Math.min(2, b.sub + delta));
  renderPlanStep();
}

function builderCommit(){
  builderCapture();
  const d = wiz.builder.draft;
  if(wiz.plan.steps.some(st => st.id === d.id)){ toast(`step id ${d.id} is already used`); return; }
  wiz.plan.steps.push({id: d.id, task_type: d.task_type, objective: d.objective,
    inputs: {goal: '$context.goal'}, boundaries: [], tools: d.tools.slice(),
    depends_on: d.depends_on.slice(), hitl: d.hitl,
    success_check: buildCheck(d.check_kind, d.check_value),
    when: buildWhen(d.when_step, d.when_kind, d.when_value)});
  wiz.edited = true;
  toast(`Added ${d.id}`);
  wiz.builder = {sub: 0, draft: newDraft()};
  renderPlanStep();
}

/* -- YAML power mode -- */
async function openYaml(){
  const r = await post('/api/workflows/validate', {workflow: wiz.plan});
  if(!r.ok){ toast('current plan is invalid: ' + (await r.text()).slice(0, 120)); return; }
  wiz.yamlText = (await r.json()).yaml;
  wiz.yamlMode = true;
  renderPlanStep();
}

async function applyYaml(){
  const text = document.getElementById('yaml-box').value;
  const r = await post('/api/workflows/validate', {workflow_yaml: text});
  if(!r.ok){
    document.getElementById('yamlmsg').textContent =
      'invalid: ' + JSON.parse(await r.text()).detail;
    return;
  }
  wiz.plan = (await r.json()).workflow;
  wiz.yamlMode = false; wiz.edited = true;
  renderPlanStep();
}

function renderYamlEditor(){
  document.getElementById('wiz-body').innerHTML = `
    <div class="card"><h2>${esc(wiz.plan.name)} — YAML</h2>
      <div class="sub">Every field of the DSL is editable here (inputs, boundaries,
        tier_hint, max_attempts…). Apply validates before anything changes.</div>
      <textarea id="yaml-box" class="yaml-box">${esc(wiz.yamlText)}</textarea>
      <div class="small red" id="yamlmsg" style="margin:8px 0"></div>
      <div class="wiz-nav">
        <button class="btn ghost" onclick="wiz.yamlMode=false;renderPlanStep()">Cancel</button>
        <button class="btn" onclick="applyYaml()">Apply YAML</button></div></div>`;
}

async function startRun(){
  const r = await post('/api/runs', {workflow: wiz.plan, context: Object.assign({goal: wiz.goal}, wiz.context), wait: false});
  if(!r.ok){ toast('start failed: ' + (await r.text()).slice(0,120)); return; }
  const state = await r.json();
  wiz.runId = state.run_id; wiz.run = state;
  wiz.pinnedStep = null;   // fresh run → tabs auto-follow again
  setStep(3);
  wiz.poller = setInterval(pollRun, 2000);
}

/* ---------- step 4: run ---------- */
async function pollRun(){
  try{
    const detail = await get('/api/runs/' + wiz.runId);
    wiz.run = detail.state; wiz.journal = detail.journal;
    if(wiz.step === 3) renderRunStep();
    if(['completed','failed'].includes(wiz.run.status)){
      clearInterval(wiz.poller); wiz.poller = null;
      setStep(4);
    }
  }catch(e){ /* transient poll failure */ }
}

function stepStatus(s){
  const run = wiz.run || {completed:{}};
  if(run.skipped && run.skipped[s.id])
    return {cls:'', icon:'⤳', label:badge('dim', 'skipped — ' + run.skipped[s.id])};
  if(run.completed[s.id]) return {cls:'done', icon:'✓', label:badge(run.completed[s.id].verdict === 'pass' ? 'ok' : 'dim', run.completed[s.id].verdict)};
  if(run.failed_step === s.id) return {cls:'fail', icon:'✕', label:badge('bad','failed')};
  if(run.awaiting === s.id) return {cls:'now', icon:'…', label:badge('warn','waiting for you')};
  const started = (wiz.journal || []).some(e => e.kind === 'step.started' && e.step_id === s.id);
  if(started && run.status === 'running') return {cls:'now', icon:'', label:'<span class="spin"></span> <span class="small dim">running…</span>'};
  return {cls:'', icon:'', label:badge('dim','queued')};
}

function attemptRows(stepId){
  // per-attempt verdicts + verifier reasons from the run journal — the
  // "why did this step fail 3 times" panel
  const atts = (wiz.journal || []).filter(e => e.kind === 'step.attempt' && e.step_id === stepId);
  if(!atts.length) return '';
  return `<div class="attempts">${atts.map(e => {
    const p = e.payload || {};
    return `<div class="att ${esc(p.verdict)}"><b>#${p.n} ${esc(p.verdict)}</b> · ${esc(p.model)}${p.scorer ? ' · ' + esc(p.scorer) : ''}${p.detail ? ' — ' + esc(p.detail) : ''}</div>`;
  }).join('')}</div>`;
}

/* ---------- tabbed step display (Run + Done screens) ----------
   Step names sit in a tab strip on top; one step's detail shows at a time.
   The selected tab auto-follows the running/awaiting/failed step until the
   user clicks a tab (pin). Tab ids travel via data-step-id + one delegated
   listener — step ids are planner/user-controlled, never interpolate them
   into inline handlers. */
function activeStepId(){
  const p = wiz.plan, run = wiz.run || {};
  if(wiz.pinnedStep && p.steps.some(s => s.id === wiz.pinnedStep)) return wiz.pinnedStep;
  if(run.awaiting) return run.awaiting;
  if(run.failed_step) return run.failed_step;
  const running = p.steps.find(s => stepStatus(s).cls === 'now');
  if(running) return running.id;
  const done = p.steps.filter(s => (run.completed || {})[s.id]);
  return (done.length ? done[done.length - 1] : p.steps[0]).id;
}

function stepTabs(sel){
  return `<div class="steptabs">` + wiz.plan.steps.map((s, i) => {
    const st = stepStatus(s);
    return `<button class="stab ${s.id === sel ? 'on' : ''}" data-step-id="${esc(s.id)}">
      <span class="ticon ${st.cls}">${st.icon || i + 1}</span>${esc(s.id)}</button>`;
  }).join('') + `</div>`;
}

function stepPanel(s){
  const run = wiz.run || {completed: {}};
  const st = stepStatus(s);
  const rec = (run.completed || {})[s.id];
  return `<div class="steppanel">
    <div class="pt">${esc(s.id)} ${badge('dim', s.task_type)} ${st.label}</div>
    <div class="pd">${esc(s.objective)}</div>
    ${rec ? humanizeOutput(rec.output) : ''}
    ${(run.failed_step === s.id || (rec && rec.attempts > 1)) ? attemptRows(s.id) : ''}
  </div>`;
}

document.addEventListener('click', e => {
  const tab = e.target.closest('.stab[data-step-id]');
  if(!tab || !wiz.plan) return;
  wiz.pinnedStep = tab.dataset.stepId;
  if(wiz.step === 3) renderRunStep();
  else if(wiz.step === 4) renderDoneStep();
});

function renderRunStep(){
  const p = wiz.plan; const run = wiz.run || {};
  const hitl = run.status === 'awaiting_approval'
    ? `<div class="guide"><div><b>Approval needed: ${esc(run.awaiting)}</b>
        <p>This step is gated — it runs only if you approve it.</p>
        <div style="margin-top:10px;display:flex;gap:10px">
          <button class="btn" onclick="resolveHitl(true)">Approve ${esc(run.awaiting)}</button>
          <button class="btn reject" onclick="resolveHitl(false)">Reject</button></div></div></div>`
    : '';
  const selected = p.steps.find(s => s.id === activeStepId()) || p.steps[0];
  document.getElementById('wiz-body').innerHTML = hitl + `
    <div class="card"><h2>${esc(p.name)}</h2>
      <div class="sub">run ${esc(wiz.runId)} · ${statusBadge(run.status || 'running')}</div>
      ${stepTabs(selected.id)}
      ${stepPanel(selected)}</div>`;
}

async function resolveHitl(approved){
  const stepId = wiz.run && wiz.run.awaiting;
  if(!stepId) return;  // gate already resolved (double-click, stale render)
  // optimistic: hide the banner the instant you click — the lingering Approve
  // button was a stale-click 409 trap while the 2s poll caught up
  wiz.run.awaiting = null; wiz.run.status = 'running';
  renderRunStep();
  const r = await post(`/api/runs/${wiz.runId}/approval`, {step_id: stepId, approved, wait: false});
  if(r.ok){
    toast(approved ? 'Approved — continuing' : 'Rejected — run will stop');
  }else if(r.status === 409){
    toast('That gate was already handled — refreshing');
  }else{
    toast('failed: ' + (await r.text()).slice(0,120));
  }
  pollRun();  // resync immediately instead of waiting for the next tick
}

/* ---------- step 5: done ---------- */
function renderDoneStep(){
  const run = wiz.run || {}; const p = wiz.plan;
  const ok = run.status === 'completed';
  document.getElementById('wiz-body').innerHTML = `
    <div class="guide"><div><b>${ok ? 'Run completed.' : 'Run failed at ' + esc(run.failed_step || '?') + '.'}</b>
      <p>${ok ? 'Every step below ran, was signed by its worker, and is journaled — the Console tab shows the provenance chain and what the router learned.'
              : 'The journal in the Console tab shows every attempt and why it failed. Rephrasing the goal often fixes fallback plans.'}</p></div></div>
    <div class="card"><h2>${esc(p.name)}</h2><div class="sub">run ${esc(wiz.runId)} · ${statusBadge(run.status)}</div>
      ${(() => {
        const selected = p.steps.find(s => s.id === activeStepId()) || p.steps[0];
        return stepTabs(selected.id) + stepPanel(selected);
      })()}</div>
    <div class="card" style="margin-top:16px"><h2>Not done yet?</h2>
      <div class="sub">Reviewer said no-ship, or a step failed? Iterate — nothing below runs without your approval.</div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="btn ghost" onclick="runAgain()">↻ Run the same workflow again</button>
        <button class="btn ghost" id="fu-btn" onclick="planFollowup()">Plan follow-up with frontier agent →</button></div>
      <div class="small dim" id="fu-msg" style="margin-top:8px"></div></div>
    <div class="wiz-nav">
      <button class="btn ghost" onclick="showView('console')">Inspect in Console</button>
      <a class="btn ghost" href="/api/runs/${esc(wiz.runId)}/package" download>⬇ Download run package</a>
      <button class="btn" onclick="resetWizard()">Start another run →</button></div>`;
}

function runAgain(){
  toast('Starting a fresh run of the same workflow');
  startRun();
}

async function planFollowup(){
  const msg = document.getElementById('fu-msg');
  const btn = document.getElementById('fu-btn');
  btn.disabled = true;
  msg.innerHTML = '<span class="spin"></span> the frontier agent is reading the run outputs and planning remediation…';
  const r = await post(`/api/runs/${wiz.runId}/followup`, {});
  btn.disabled = false;
  if(!r.ok){ msg.textContent = 'follow-up planning failed: ' + (await r.text()).slice(0, 140); return; }
  const data = await r.json();
  wiz.plan = data.workflow;
  wiz.planSource = data.plan_source;
  wiz.fallbackReason = data.fallback_reason || '';
  wiz.edited = false; wiz.editingStep = null;
  wiz.builderMode = false; wiz.yamlMode = false;
  toast('Follow-up plan ready — review and approve before it runs');
  setStep(2);
}

function resetWizard(){
  wiz.goal = ''; wiz.context = {}; wiz.plan = null; wiz.runId = null; wiz.run = null;
  wiz.editingStep = null; wiz.builderMode = false; wiz.builder = null;
  wiz.yamlMode = false; wiz.edited = false;
  wiz.pinnedStep = null; wiz.fallbackReason = '';
  if(wiz.poller){ clearInterval(wiz.poller); wiz.poller = null; }
  setStep(1);
}

/* ---------- console view ---------- */
const openRuns = new Set();
function toggleRun(runId){ openRuns.has(runId) ? openRuns.delete(runId) : openRuns.add(runId); refreshConsole(); }

/* Pagination, shared by every console card. Page state survives the 3s live
   refresh; the current page self-clamps when the data shrinks. */
const PAGE_SIZE = 8;
const pages = {};
function paginate(key, items, renderItems, opts = {}){
  const size = opts.size || PAGE_SIZE;
  const nPages = Math.max(1, Math.ceil(items.length / size));
  const cur = Math.min(pages[key] || 0, nPages - 1);
  pages[key] = cur;
  const body = renderItems(items.slice(cur * size, (cur + 1) * size));
  if(nPages < 2) return body;
  const [back, fwd] = opts.timeOrdered ? ['← Newer', 'Older →'] : ['← Prev', 'Next →'];
  // keys can carry user/filesystem-derived parts (e.g. suite names) — escape
  // them in the attribute; the click handler splits on the LAST colon.
  return body + `<div class="pager">
    <button class="pill" data-page="${esc(key)}:-1" ${cur === 0 ? 'disabled' : ''}>${back}</button>
    <span class="pager-info">${cur * size + 1}–${Math.min(items.length, (cur + 1) * size)} of ${items.length}</span>
    <button class="pill" data-page="${esc(key)}:1" ${cur >= nPages - 1 ? 'disabled' : ''}>${fwd}</button></div>`;
}

async function resolveApproval(runId, stepId, approved){
  const r = await post(`/api/runs/${runId}/approval`, {step_id: stepId, approved, wait: false});
  toast(r.ok ? `${approved ? 'Approved' : 'Rejected'} ${stepId}`
        : r.status === 409 ? 'That gate was already handled — refreshing'
        : 'Approval failed');
  refreshConsole();
}

function renderTiles(runs, workers, prov, playbook){
  const active = runs.filter(r => ['running','awaiting_approval'].includes(r.status)).length;
  const chainOk = prov.chain.ok;
  return `
    <div class="tile"><div class="val">${runs.length}</div>
      <div class="lab">runs so far — ${active} active, ${runs.filter(r=>r.status==='completed').length} finished</div></div>
    <div class="tile"><div class="val">${workers.filter(w=>w.active).length}</div>
      <div class="lab">agents ready to work</div></div>
    <div class="tile"><div class="val ${chainOk?'green':'red'}">${chainOk?'✔':'✘'} ${prov.total}</div>
      <div class="lab">${chainOk ? 'signed actions — audit trail intact' : 'audit trail BROKEN — do not trust these results'}</div></div>
    <div class="tile"><div class="val">${playbook.filter(b=>b.active).length}</div>
      <div class="lab">lessons guiding new runs</div></div>`;
}

const verdictBadge = v => badge(v === 'pass' ? 'ok' : v === 'fail' ? 'bad' : 'dim',
  v === 'pass' ? 'verified' : v === 'fail' ? 'failed its check' : 'couldn’t be checked');

function renderRuns(runs){
  if(!runs.length) return '<div class="empty">no runs yet — start one from the Run tab</div>';
  return paginate('runs', runs.slice().reverse(), rows => rows.map(r => {
    const open = openRuns.has(r.run_id);
    const kind = runKind(r);
    const hitl = r.status === 'awaiting_approval'
      ? `<button class="btn" data-approve="1" data-run="${esc(r.run_id)}" data-step="${esc(r.awaiting)}">Approve</button>
         <button class="btn reject" data-approve="0" data-run="${esc(r.run_id)}" data-step="${esc(r.awaiting)}">Reject</button>`
      : '';
    const detail = !open ? '' : `<div class="rr-detail">` +
      (Object.entries(r.completed).map(([id, rec]) =>
        `<div class="rr-out"><div class="rr-out-h">${esc(stepName(id))} ${verdictBadge(rec.verdict)}</div>
         ${humanizeOutput(rec.output)}</div>`).join('')
       || '<div class="empty">nothing recorded yet — outputs appear here as steps finish</div>') + '</div>';
    return `<div class="lrow runrow" data-run="${esc(r.run_id)}">
      <div class="rr-main">
        <div class="rr-title">${open ? '▾' : '▸'} ${esc(runTitle(r))}</div>
        <div class="rr-meta">${esc(r.run_id)}${kind ? ' · ' + esc(kind) : ''}${r.updated_at ? ' · ' + esc(ago(r.updated_at)) : ''}</div></div>
      <div class="rr-story">${esc(runStory(r))}</div>
      ${statusBadge(r.status)}${hitl}</div>${detail}`;
  }).join(''), {timeOrdered: true});
}

/* Delegated clicks: step ids are user-authored, so they ride in data-* attributes
   (HTML-escaped) instead of being spliced into inline JS strings. */
document.getElementById('runs').addEventListener('click', ev => {
  const b = ev.target.closest('button[data-approve]');
  if(b){ resolveApproval(b.dataset.run, b.dataset.step, b.dataset.approve === '1'); return; }
  const row = ev.target.closest('.runrow');
  if(row) toggleRun(row.dataset.run);
});

/* Pager buttons, anywhere in the console */
document.getElementById('view-console').addEventListener('click', ev => {
  const b = ev.target.closest('button[data-page]');
  if(!b) return;
  const cut = b.dataset.page.lastIndexOf(':');   // keys may contain ':'
  const key = b.dataset.page.slice(0, cut), delta = b.dataset.page.slice(cut + 1);
  pages[key] = Math.max(0, (pages[key] || 0) + Number(delta));
  refreshConsole();
});

function renderWorkers(ws){
  if(!ws.length) return '<div class="empty">no agents yet — add one in Settings</div>';
  return paginate('workers', ws, rows => rows.map(w => `
    <div class="lrow">
      <div class="rr-main">
        <div class="rr-title" style="white-space:normal">${esc(w.display_name)}
          ${(w.tiers||[]).map(t => badge('act', t + ' tier')).join(' ')}</div>
        <div class="rr-meta">${esc(w.worker_id)} · identity key ${esc(w.public_key_b64.slice(0,10))}…${
          w.key_rotations ? ` · key rotated ×${w.key_rotations}` : ''}</div></div>
      ${badge(w.active ? 'ok' : 'dim', w.active ? 'ready' : 'retired')}</div>`).join(''));
}

const actionPlain = a => String(a || '').replace(/[._]/g, ' ');

function renderProvenance(p){
  const chain = p.chain.ok
    ? `${badge('ok','intact')} <span class="small">All ${p.chain.checked} recorded actions check out — nothing has been altered.</span>`
    : `${badge('bad','broken')} <span class="red small">The chain fails at entry #${p.chain.problem_index} (${esc(p.chain.reason)}) — don’t trust anything after that point.</span>`;
  const rows = paginate('audit', p.entries.slice().reverse(), es => es.map(e =>
    `<div class="lrow" style="padding:7px 2px">
       <div class="rr-main"><div class="small"><b>${esc(e.actor_id)}</b> <span class="dim">·</span> ${esc(actionPlain(e.action))}</div>
         <div class="rr-meta">#${e.index} · ${esc(e.entry_hash.slice(0,12))}…</div></div></div>`).join(''),
    {timeOrdered: true});
  return `<div class="chainline">${chain}</div>` + rows +
    `<div class="headhash" style="margin-top:8px">chain head ${esc(p.head_hash.slice(0,28))}…</div>`;
}

const taskPlain = t => String(t || '').replace(/[._-]/g, ' ');

function renderMatrix(m){
  const models = Object.keys(m);
  if(!models.length) return '<div class="empty">nothing observed yet — the harness learns who’s good at what as runs finish</div>';
  return paginate('matrix', models, ms => ms.map(model => {
    const rows = Object.entries(m[model]).map(([t, c]) =>
      `<tr><td class="small">${esc(taskPlain(t))}</td>
       <td><span class="bar-h" style="width:${Math.round(c.pass_rate*110)}px"></span>
       <span class="mono small">${(c.pass_rate*100).toFixed(0)}%</span></td>
       <td class="small faint">${c.samples === 1 ? 'from 1 try' : `from ${c.samples} tries`}</td></tr>`).join('');
    return `<div class="small" style="margin:8px 0 4px"><b>${esc(model)}</b></div><table>${rows}</table>`;
  }).join(''), {size: 3});
}

function renderPlaybook(bullets){
  if(!bullets.length) return '<div class="empty">no lessons yet — the harness writes them as failure patterns emerge</div>';
  return paginate('playbook', bullets, bs =>
    '<table><tr><th>lesson</th><th>applies to</th><th>track record</th></tr>' + bs.map(b => {
      const score = (b.helpful + 1) / (b.helpful + b.harmful + 2);
      return `<tr${b.active?'':' class="faint"'}><td class="small">${esc(b.text)}
        ${b.active?'':badge('dim','retired')}<br><span class="rr-meta">${esc(b.origin||'added by hand')}</span></td>
        <td class="small">${esc(taskPlain(b.task_type)||'everything')}</td>
        <td class="mono small">${(score*100).toFixed(0)}% <span class="faint">helped ${b.helpful}× · hurt ${b.harmful}×</span></td></tr>`;
    }).join('') + '</table>');
}

/* MAST failure modes, in words a person would actually say */
const MAST_PLAIN = {
  disobey_task_spec: 'ignored what the task asked for',
  disobey_role_spec: 'acted outside its role',
  step_repetition: 'kept repeating a step',
  lose_history: 'lost track of earlier context',
  unaware_termination: 'stopped without noticing work remained',
  ignore_input: 'ignored another agent’s input',
  withheld_info: 'failed to pass information along',
  mismatched_assumption: 'agents assumed different things',
  premature_termination: 'gave up too early',
  no_verification: 'the result was never checked',
  incorrect_verification: 'the check itself was wrong',
  tool_error: 'a tool call failed',
  schema_invalid: 'output didn’t match the required format',
  budget_exceeded: 'ran out of budget',
  unknown: 'cause not identified',
};
const mastPlain = m => MAST_PLAIN[m] || taskPlain(m);

function renderFailures(f){
  const rows = Object.keys(f).flatMap(t =>
    Object.entries(f[t]).map(([mode, n]) => ({t, mode, n})));
  if(!rows.length) return '<div class="empty">no failures yet — when one happens it lands here, labelled</div>';
  return paginate('failures', rows, rs =>
    '<table><tr><th>while doing</th><th>what went wrong</th><th>times</th></tr>' +
    rs.map(({t, mode, n}) =>
      `<tr><td class="small">${esc(taskPlain(t))}</td>
       <td class="small"><span title="${esc(mode)}">${esc(mastPlain(mode))}</span></td>
       <td class="mono small">${n}×</td></tr>`).join('') + '</table>', {size: 10});
}

/* Harness tuning: one plain-language row per experiment the optimizer ran on
   the harness itself (arXiv 2603.28052). ⭐ marks the pass-vs-cost frontier. */
const FINDING_BADGE = {
  pending: ['warn', 'your call'], promotion: ['ok', 'applied'],
  not_worth_it: ['dim', 'dead end'], coverage: ['warn', 'thin evidence'],
  info: ['dim', 'note'],
};
const TUNE = { suite: 'mixed' };
function renderTuning(suites){
  const busy = suites.some(s => s.running);
  const controls = `<div class="chainline">
    <select id="tune-suite" style="border:1px solid var(--line2);border-radius:999px;
      padding:5px 10px;font-family:inherit;font-size:12.5px;background:var(--card);color:var(--ink)">
      ${['mixed','classify','extract','math'].map(n =>
        `<option value="${n}" ${TUNE.suite === n ? 'selected' : ''}>${n} suite</option>`).join('')}</select>
    <button class="btn ghost" data-tune-start="1" ${busy ? 'disabled' : ''}>
      ${busy ? 'searching…' : 'Tune harness'}</button></div>`;
  if(!suites.length) return controls + '<div class="empty">no experiments yet — pick a suite and hit Tune harness (or run <span class="mono">metaharness optimize</span>)</div>';
  return controls + suites.map(s => {
    const pend = s.pending ? (() => {
      const g = s.pending.gate || {};
      return `<div class="guide" style="margin:8px 0">
        <div><b>Promote ${esc(s.pending.candidate)}?</b>
        <p>On questions it never saw during the search it answered
        ${(100 * (g.overall_candidate || 0)).toFixed(0)}% reliably vs
        ${(100 * (g.overall_incumbent || 0)).toFixed(0)}% today, and no task type got worse.
        Approving rewires the live agent immediately.</p></div>
        <div class="cta">
          <button class="btn" data-tune-approve="1" data-suite="${esc(s.suite)}">Approve</button>
          <button class="btn reject" data-tune-approve="0" data-suite="${esc(s.suite)}">Reject</button>
        </div></div>`;
    })() : '';
    const promotedId = s.promoted ? s.promoted.candidate : null;
    // key must stay colon-free: the pager handler splits data-page on ':'
    const rows = !s.candidates.length
      ? '<div class="empty">measuring the current setup first — experiments appear here as each one finishes (minutes per experiment on local models)</div>'
      : paginate('tuning-' + s.suite, s.candidates.slice().reverse(), cs => cs.map(c => {
      const seed = (c.hypothesis || '').startsWith('seed');
      const marks = [
        c.id === promotedId ? badge('ok', s.active ? 'promoted — in use' : 'promoted — applies at restart') : '',
        seed ? badge('dim', 'the original setup') : '',
        c.status === 'rejected' ? badge('bad', 'rejected')
          : c.frontier ? '<span title="on the pass-vs-cost frontier">⭐</span>'
          : badge('dim', 'not worth it'),
      ].filter(Boolean).join(' ');
      const meta = (c.scores
        ? `pass^${c.scores.k} ${c.scores.pass_hat_k.toFixed(2)} · pass@1 ${c.scores.pass_at_1.toFixed(2)}`
          + ` · ${c.scores.tokens_total.toLocaleString()} tokens${c.parent ? ' · builds on ' + esc(c.parent) : ''}`
        : 'never evaluated') + (c.created_at ? ' · ' + esc(ago(c.created_at)) : '');
      const adviceKey = s.suite + '/' + c.id;
      return `<div class="lrow"><div class="rr-main">
        <div class="rr-title">${esc(c.id)} ${marks}</div>
        <div class="rr-meta">${meta}</div></div>
        <div class="rr-story">${esc(c.hypothesis || '')}${c.rejected_reason ? ' — ' + esc(c.rejected_reason) : ''}</div>
        <button class="why ${ADVICE[adviceKey] ? 'on' : ''}" data-advise="${esc(c.id)}"
          data-suite="${esc(s.suite)}" title="AI insight — explain this result">
          <svg><use href="#sparkle"/></svg></button></div>`
        + (ADVICE[adviceKey] ? renderAdvicePanel(adviceKey, c) : '');
    }).join(''), {timeOrdered: true});
    const g = s.report && s.report.gate;
    const gate = g ? `<div class="chainline" style="margin-top:10px">
        ${badge(g.go ? 'ok' : 'bad', g.go ? 'GO' : 'NO-GO')}
        <span class="small dim">held-out check: ${esc(g.incumbent_model)} vs ${esc(g.candidate_model)}
        · ${(g.overall_incumbent).toFixed(2)} → ${(g.overall_candidate).toFixed(2)}
        · ${g.wins}W/${g.losses}L/${g.ties}T</span></div>` : '';
    const findings = (s.findings || []).map(f => {
      const [cls, label] = FINDING_BADGE[f.kind] || ['dim', f.kind];
      return `<div class="lrow" style="padding:8px 2px">
        <div class="rr-main"><div class="small">${esc(f.story)}</div>
        ${f.evidence && !f.story.includes(f.evidence) ? `<div class="rr-meta">${esc(f.evidence)}</div>` : ''}</div>
        ${badge(cls, label)}</div>`;
    }).join('');
    // plain-language wrap-up of the last search, with freshness
    let summary = '';
    if(s.report && !s.running){
      const rep = s.report;
      const experiments = s.candidates.filter(c => c.status === 'evaluated').length;
      const outcome = s.pending ? `${esc(s.pending.candidate)} is waiting for your decision`
        : rep.promoted ? `${esc(rep.best_id)} won and was promoted`
        : rep.stopped === 'error' ? 'the search crashed — see the note below'
        : 'nothing beat the current setup';
      const g2 = rep.gate;
      summary = `<div class="small dim" style="margin:2px 0 8px">Last search${
        rep.finished_at ? ' finished <b>' + esc(ago(rep.finished_at)) + '</b>' : ''}:
        ${experiments} experiment${experiments === 1 ? '' : 's'} over ${rep.rounds_run} round${rep.rounds_run === 1 ? '' : 's'}
        — ${outcome}${g2 ? ` (held-out ${g2.overall_incumbent.toFixed(2)} → ${g2.overall_candidate.toFixed(2)})` : ''}.</div>`;
    }
    return `<div class="small" style="margin:8px 0 4px"><b>${esc(s.suite)} suite</b>
      ${s.running ? badge('act', 'searching…') : ''}</div>` + summary + pend + rows + gate
      + (findings ? `<div class="small" style="margin:12px 0 2px"><b>What this means</b></div>` + findings : '');
  }).join('');
}

/* Advisor panels on tuning rows: verified facts render instantly; the
   companion's read streams in under its own advisory chip. */
const ADVICE = {};
function renderAdvicePanel(key, c){
  const a = ADVICE[key];
  const facts = `<div class="facts"><div class="h">What happened <span class="badge dim">verified facts</span></div><ul>`
    + (c.scores ? `<li>pass^${c.scores.k} ${c.scores.pass_hat_k.toFixed(2)} at ${c.scores.tokens_total.toLocaleString()} tokens — ${
        c.frontier ? 'on the efficiency frontier' : 'a cheaper setup does the same or better'}</li>` : '')
    + `<li>${esc(c.hypothesis || 'no hypothesis recorded')}</li>`
    + (c.rejected_reason ? `<li>rejected: ${esc(c.rejected_reason)}</li>` : '') + '</ul></div>';
  const body = a.loading ? '<div class="empty">thinking…</div>'
    : a.error ? `<div class="empty">${esc(a.error)}</div>`
    : `<p>${esc(a.read)}</p><div class="nba">${a.next_actions.filter(x => x.action !== 'none')
        .map(x => `<button class="btn small" data-advise-act="${esc(x.action)}">${esc(x.label)}</button>`).join('')}</div>`;
  return `<div class="rr-detail"><div class="advisor">${facts}<div class="takes">
    <div class="h">Advisor’s read <span class="ai-chip"><svg style="width:11px;height:11px"><use href="#sparkle"/></svg>AI companion — advisory, not verified</span></div>
    ${body}</div></div></div>`;
}

document.getElementById('tuning').addEventListener('click', async ev => {
  const w = ev.target.closest('button[data-advise]');
  if(w){
    const key = w.dataset.suite + '/' + w.dataset.advise;
    if(ADVICE[key]){ delete ADVICE[key]; refreshConsole(); return; }
    ADVICE[key] = {loading: true};
    refreshConsole();
    try{
      const r = await post('/api/advise', {page: 'tuning', subject: w.dataset.advise, suite: w.dataset.suite});
      ADVICE[key] = r.ok ? await r.json() : {error: 'the advisor is unavailable right now — the facts above still stand'};
    }catch(e){ ADVICE[key] = {error: 'the advisor is unavailable right now — the facts above still stand'}; }
    refreshConsole(); return;
  }
  const act = ev.target.closest('button[data-advise-act]');
  if(act){
    if(act.dataset.adviseAct === 'start_tune'){
      const r = await post('/api/optimization/runs', {suite: TUNE.suite});
      toast(r.ok ? `Tuning started on the ${TUNE.suite} suite` : 'Could not start — a search may already be running');
      refreshConsole();
    } else if(act.dataset.adviseAct === 'approve_promotion'){
      toast('Scroll up to the Promote banner — the decision buttons live there');
    } else {
      toast('That suggestion needs a human — see the Help tab for how');
    }
    return;
  }
  const a = ev.target.closest('button[data-tune-approve]');
  if(a){
    const ok = a.dataset.tuneApprove === '1';
    const r = await post(`/api/optimization/${encodeURIComponent(a.dataset.suite)}/approval`, {approved: ok});
    toast(r.ok ? (ok ? 'Promoted — the live agent now uses this setup' : 'Rejected — keeping the current setup')
               : 'That decision was already handled — refreshing');
    refreshConsole(); return;
  }
  if(ev.target.closest('button[data-tune-start]')){
    const r = await post('/api/optimization/runs', {suite: TUNE.suite});
    toast(r.ok ? `Tuning started on the ${TUNE.suite} suite — watch it think out loud here`
               : 'Could not start — a search may already be running');
    refreshConsole();
  }
});
document.getElementById('tuning').addEventListener('change', ev => {
  if(ev.target.id === 'tune-suite') TUNE.suite = ev.target.value;
});

function renderSpans(spans){
  if(!spans.length) return '<div class="empty">quiet right now — operations appear here as they happen</div>';
  return paginate('spans', spans.slice().reverse(), ss =>
    '<table><tr><th>operation</th><th>took</th><th>details</th></tr>' +
    ss.map(s => {
      const attrs = Object.entries(s.attributes).map(([k,v]) => `${esc(k)}=${esc(v)}`).join('  ');
      return `<tr><td class="mono small">${esc(s.name)}</td>
        <td><span class="bar-h" style="width:${Math.min(130, Math.max(2, s.duration_ms))}px"></span>
        <span class="mono small">${s.duration_ms.toFixed(1)}ms</span></td>
        <td class="mono small faint">${attrs}</td></tr>`;
    }).join('') + '</table>', {size: 10, timeOrdered: true});
}

async function refreshConsole(){
  try{
    const [runs, workers, prov, matrix, playbook, failures, spans, tuning] = await Promise.all([
      get('/api/runs'), get('/api/workers'), get('/api/provenance'),
      get('/api/matrix'), get('/api/playbook'), get('/api/failures'), get('/api/spans'),
      get('/api/optimization'),
    ]);
    document.getElementById('tiles').innerHTML = renderTiles(runs, workers, prov, playbook);
    document.getElementById('runs').innerHTML = renderRuns(runs);
    document.getElementById('workers').innerHTML = renderWorkers(workers);
    document.getElementById('provenance').innerHTML = renderProvenance(prov);
    document.getElementById('matrix').innerHTML = renderMatrix(matrix);
    document.getElementById('playbook').innerHTML = renderPlaybook(playbook);
    document.getElementById('failures').innerHTML = renderFailures(failures);
    document.getElementById('tuning').innerHTML = renderTuning(tuning);
    document.getElementById('spans').innerHTML = renderSpans(spans);
    document.getElementById('updated').textContent = 'updated ' + new Date().toLocaleTimeString();
  }catch(e){ document.getElementById('updated').textContent = 'refresh failed'; }
}

setInterval(() => {
  if(document.getElementById('view-console').style.display !== 'none') refreshConsole();
  if(document.getElementById('view-home').style.display !== 'none') renderHome();
}, 3000);

/* ================= SETTINGS: wizard-driven configuration ================= */
const SET = { cfg: null, tools: [], provWiz: null, agentWiz: null };

async function renderSettings(refetch){
  const body = document.getElementById('settings-body');
  if(refetch || !SET.cfg){
    try{
      const [cfg, tools] = await Promise.all([get('/api/config'), get('/api/tools')]);
      SET.cfg = cfg; SET.tools = tools;
    }catch(e){ body.innerHTML = '<div class="card"><div class="empty">failed to load config</div></div>'; return; }
  }
  if(SET.provWiz){ body.innerHTML = renderProvWizard(); return; }
  if(SET.agentWiz){ body.innerHTML = renderAgentWizard(); return; }
  body.innerHTML = renderSettingsHome();
}

function renderSettingsHome(){
  const cfg = SET.cfg;
  const providers = Object.values(cfg.providers || {});
  const provRows = providers.length ? providers.map(p => `
    <div class="prov-item"><div class="pi-main">
      <div class="pi-name">${esc(p.label || p.id)} ${p.configured ? badge('ok','✓ connected') : badge('dim','no key yet')}</div>
      <div class="kv">${esc(p.base_url)} · key ${esc(p.api_key || '—')} ${p.default_model ? '· ' + esc(p.default_model) : ''}</div></div>
      <button class="pill" data-act="prov_edit" data-id="${esc(p.id)}">Edit</button>
      <button class="pill" data-act="prov_del" data-id="${esc(p.id)}">Remove</button></div>`).join('')
    : '<div class="empty">none yet — add one to give your agents a brain in the cloud</div>';

  const agents = cfg.agents || [];
  const agentRows = agents.length ? agents.map((a, i) => `
    <div class="prov-item"><div class="pi-main">
      <div class="pi-name">${esc(a.worker_id)} ${badge('act', a.tier + ' tier')} ${badge('dim', {
        openai_compat: 'LLM endpoint', coding_cli: 'coding CLI',
        subscription_cli: 'subscription', mock: 'mock'}[a.kind] || a.kind)}</div>
      <div class="kv">${a.kind === 'coding_cli' ? 'CLI: ' + esc(a.cli) : esc(a.provider ? 'provider: ' + a.provider : a.base_url)}
        ${a.model ? ' · ' + esc(a.model) : ''}${a.system_prompt ? ' · has its own instructions' : ''}</div></div>
      <button class="pill" data-act="agent_edit" data-id="${i}">Edit</button>
      <button class="pill" data-act="agent_del" data-id="${esc(a.worker_id)}">Remove</button></div>`).join('')
    : '<div class="empty">no saved agents — the ones running now were auto-discovered and vanish when the server restarts</div>';

  const clis = Object.entries(cfg.coding_clis || {});
  const cliChips = clis.length
    ? clis.map(([name, path]) => `<span class="badge ok">${esc(name)}</span> <span class="kv">${esc(path)}</span>`).join('<br>')
    : '<span class="empty">none found on this machine (looked for pi, codex, opencode, claude)</span>';

  const mcp = Object.values(cfg.mcp_servers || {});
  const mcpRows = mcp.length ? mcp.map(s => `
    <div class="prov-item"><div class="pi-main">
      <div class="pi-name">${esc(s.name)} ${badge('dim', s.transport)}</div>
      <div class="kv">${esc(s.transport === 'http' ? s.url : s.command + ' ' + (s.args || []).join(' '))}</div></div>
      <button class="pill" data-act="mcp_del" data-id="${esc(s.name)}">Remove</button></div>`).join('')
    : '<div class="empty">none configured — fine for most workflows</div>';

  const toolsBySource = {};
  SET.tools.forEach(t => (toolsBySource[t.source] = toolsBySource[t.source] || []).push(t));
  const toolRows = Object.entries(toolsBySource).map(([src, ts]) =>
    `<div class="kv" style="margin:8px 0 4px">${esc(src)} — ${ts.length} tool${ts.length === 1 ? '' : 's'}</div>` +
    ts.map(t => `<div class="small" style="padding:2px 0"><b class="mono">${esc(t.name)}</b> <span class="dim">${esc(t.description)}</span></div>`).join('')
  ).join('');

  return `
    <div class="card"><h2>1 · Where do completions come from?</h2>
      <div class="sub">Providers are the LLM services behind your agents. Keys are stored obfuscated on this machine and never shown unmasked.</div>
      ${provRows}
      <button class="btn ghost" onclick="startProvWizard('')">+ Add a provider</button></div>
    <div class="card" style="margin-top:16px"><h2>2 · Who does the work?</h2>
      <div class="sub">Your saved agents. Each one is rebuilt and re-verifies its identity every time the server starts.</div>
      ${agentRows}
      <button class="btn ghost" onclick="startAgentWizard(null)">+ Add an agent</button></div>
    <div class="grid" style="margin-top:16px">
      <div class="card"><h2>3 · What can they use?</h2>
        <div class="sub">Coding CLIs found on this machine — they give agents hands, or answer through a subscription you’re already signed in to.</div>
        ${cliChips}
        <div class="kv" style="margin:12px 0 6px">SUBSCRIPTIONS</div>
        ${Object.entries(cfg.subscriptions || {}).map(([name, st]) => `
          <div class="small" style="padding:3px 0">${
            st.installed && st.authenticated ? badge('ok','signed in')
            : st.installed ? badge('warn','not signed in')
            : badge('dim','not installed')} <b>${esc(st.label)}</b>
            ${st.installed && !st.authenticated ? `<span class="dim"> — ${esc(st.login_hint)}</span>` : ''}</div>`).join('')}</div>
      <div class="card"><h2>Extra tool servers</h2>
        <div class="sub">MCP servers add tools your workflows can call — they load when the server starts.</div>${mcpRows}
        <div class="field" style="margin-top:10px"><label>Add: name</label><input id="mcp-name" class="mono" placeholder="files"></div>
        <div class="field"><label>Transport</label><select id="mcp-transport" onchange="document.getElementById('mcp-stdio').style.display = this.value === 'stdio' ? '' : 'none'; document.getElementById('mcp-http').style.display = this.value === 'http' ? '' : 'none'">
          <option value="stdio">stdio (command)</option><option value="http">http (url)</option></select></div>
        <div id="mcp-stdio"><div class="field"><label>Command + args</label>
          <input id="mcp-cmd" class="mono" placeholder="npx -y @modelcontextprotocol/server-filesystem /tmp"></div></div>
        <div id="mcp-http" style="display:none"><div class="field"><label>URL</label>
          <input id="mcp-url" class="mono" placeholder="http://localhost:8000/mcp"></div></div>
        <button class="btn ghost" onclick="addMcp()">Add MCP server</button>
        <div class="small dim" style="margin-top:6px">restart the server (or re-run) to load its tools</div></div>
    </div>
    <div class="card" style="margin-top:16px">
      <details><summary style="cursor:pointer"><b>Browse the full tool catalog</b>
        <span class="dim">— ${SET.tools.length} tool${SET.tools.length === 1 ? '' : 's'} the planner can hand to a step; each step only ever gets a small, relevant subset</span></summary>
      ${toolRows || '<div class="empty">no tools loaded</div>'}</details></div>`;
}

/* Delegated clicks for user-authored ids (providers, agents, MCP servers, model
   picks) — ids ride in HTML-escaped data-* attributes, never inline JS strings. */
document.getElementById('settings-body').addEventListener('click', ev => {
  const el = ev.target.closest('[data-act]');
  if(!el) return;
  const id = el.dataset.id;
  ({prov_edit: () => startProvWizard(id),
    prov_del:  () => deleteProvider(id),
    agent_edit:() => startAgentWizardIdx(+id),
    agent_del: () => removeAgent(id),
    mcp_del:   () => deleteMcp(id),
    pick:      () => pickModel(el.dataset.input, el.dataset.value),
  })[el.dataset.act]?.();
});

async function deleteMcp(name){
  const r = await fetch('/api/config/mcp/' + encodeURIComponent(name), {method:'DELETE'});
  toast(r.ok ? 'Removed tool server ' + name : 'Failed: ' + (await r.text()).slice(0,140));
  renderSettings(true);
}

async function deleteProvider(pid){
  const r = await fetch('/api/config/providers/' + encodeURIComponent(pid), {method:'DELETE'});
  toast(r.ok ? 'Removed ' + pid : 'Failed: ' + (await r.text()).slice(0,140));
  renderSettings(true);
}

async function removeAgent(id){
  const r = await fetch('/api/workers/' + encodeURIComponent(id), {method:'DELETE'});
  toast(r.ok ? 'Retired ' + id : 'Failed: ' + (await r.text()).slice(0,140));
  renderSettings(true);
}

async function addMcp(){
  const name = document.getElementById('mcp-name').value.trim();
  const transport = document.getElementById('mcp-transport').value;
  if(!name){ toast('MCP server needs a name'); return; }
  const body = {name, transport};
  if(transport === 'stdio'){
    const parts = document.getElementById('mcp-cmd').value.trim().split(/\\s+/);
    if(!parts[0]){ toast('give a command'); return; }
    body.command = parts[0]; body.args = parts.slice(1);
  }else{
    body.url = document.getElementById('mcp-url').value.trim();
    if(!body.url){ toast('give a URL'); return; }
  }
  const r = await post('/api/config/mcp', body);
  toast(r.ok ? 'Saved MCP server ' + name : 'Failed: ' + (await r.text()).slice(0,140));
  renderSettings(true);
}

/* ---------- visible model pick-list (datalists hide until you type) ---------- */
const PICK_CAP = 60;
function modelPickList(listId, inputId, models, filter){
  if(!models || !models.length) return '';
  const needle = (filter || '').toLowerCase();
  const hits = needle ? models.filter(m => m.toLowerCase().includes(needle)) : models;
  const rows = hits.slice(0, PICK_CAP).map(m =>
    `<div class="pl-row" data-act="pick" data-input="${esc(inputId)}" data-value="${esc(m)}">${esc(m)}</div>`).join('');
  const more = hits.length > PICK_CAP
    ? `<div class="pl-more">…${hits.length - PICK_CAP} more — type to narrow</div>` : '';
  const none = hits.length ? '' : '<div class="pl-more">no model matches the filter</div>';
  return `<div class="pick-list" id="${esc(listId)}">${rows}${more}${none}</div>`;
}

function pickModel(inputId, value){
  const el = document.getElementById(inputId);
  if(el){ el.value = value; el.dispatchEvent(new Event('input')); }
}

function filterPickList(inputId, listId, modelsGetter){
  const el = document.getElementById(inputId);
  const list = document.getElementById(listId);
  if(!el || !list) return;
  const html = modelPickList(listId, inputId, modelsGetter(), el.value);
  list.outerHTML = html || `<div class="pick-list" id="${listId}" style="display:none"></div>`;
}

/* ---------- provider wizard: pick → connect → test & save ---------- */
function startProvWizard(pid){
  const existing = pid ? SET.cfg.providers[pid] : null;
  SET.provWiz = { step: existing ? 1 : 0, id: pid || '',
    base_url: existing ? existing.base_url : '', api_key: '',
    default_model: existing ? (existing.default_model || '') : '',
    models: null, test: null };
  renderSettings();
  if(existing) provFetchModels();  // stored key/keyless: list is one call away
}

function provWizSteps(){
  const labels = ['Pick provider','Connect','Test & save'];
  return '<div class="subwiz-steps">' + labels.map((l, i) =>
    `<span class="t ${i === SET.provWiz.step ? 'on' : ''} ${i < SET.provWiz.step ? 'done' : ''}">${i + 1} · ${l}</span>`).join('') + '</div>';
}

function renderProvWizard(){
  const w = SET.provWiz;
  const catalog = SET.cfg.catalog || [];
  let inner = '';
  if(w.step === 0){
    inner = `<div class="sub">Where do this agent's completions come from?</div>
      <div class="pillrow">${catalog.map(c =>
        `<button class="pill ${w.id === c.id ? 'on' : ''}" onclick="provPick('${esc(c.id)}')">${esc(c.label)}</button>`).join('')}</div>`;
  }else if(w.step === 1){
    const entry = catalog.find(c => c.id === w.id) || {};
    const keyless = entry.keyless;
    inner = `
      <div class="field"><label>Provider id</label><input id="pw-id" class="mono" value="${esc(w.id)}" ${catalog.some(c => c.id === w.id) && w.id !== 'custom' ? 'disabled' : ''}></div>
      <div class="field"><label>Base URL</label><input id="pw-base" class="mono" value="${esc(w.base_url || entry.base_url || '')}"></div>
      ${keyless ? '<div class="hint-panel">Local server — no API key needed. Make sure it is running.</div>' : `
      <div class="field"><label>API key ${entry.get ? `— <a href="${esc(entry.get)}" target="_blank">get one ↗</a>` : ''}</label>
        <input id="pw-key" class="mono" type="password" placeholder="${esc((SET.cfg.providers[w.id] || {}).api_key || 'sk-…')}"></div>`}
      <div class="field"><label>Default model</label>
        <div style="display:flex;gap:8px">
          <input id="pw-model" class="mono" value="${esc(w.default_model)}" placeholder="model id" style="flex:1"
            oninput="filterPickList('pw-model','pw-pick', () => (SET.provWiz.models && SET.provWiz.models.length) ? SET.provWiz.models : ${JSON.stringify((entry.models || []))}.slice())">
          <button class="btn ghost" onclick="provFetchModels()">List models</button></div>
        ${modelPickList('pw-pick', 'pw-model', (w.models && w.models.length) ? w.models : (entry.models || []), w.default_model)}
        <span class="small dim" id="pw-models-msg">${
          w.models === null ? 'press List models to fetch what this provider actually serves'
          : w.models.length ? w.models.length + ' model(s) live from the endpoint — pick from the list'
          : 'endpoint did not list models — check the key and base URL'}</span></div>`;
  }else{
    inner = `
      <div class="hint-panel"><b>About to save</b>
        <span class="kv">${esc(w.id)} → ${esc(w.base_url)}${w.default_model ? ' · ' + esc(w.default_model) : ''}</span></div>
      <div style="display:flex;gap:10px;align-items:center">
        <button class="btn ghost" onclick="provTest()">Test connection</button>
        <span class="small" id="pw-test">${w.test === null ? '' : w.test.ok
          ? `<span class="green">✔ ok · ${w.test.latency_ms}ms</span>`
          : `<span class="red">✘ ${esc(w.test.error || w.test.detail || 'failed')}</span>`}</span></div>`;
  }
  return `<div class="card"><h2>${SET.cfg.providers[w.id] ? 'Edit' : 'Add'} provider</h2>
    ${provWizSteps()}${inner}
    <div class="wiz-nav">
      <button class="btn ghost" onclick="provWizNav(-1)">${w.step === 0 ? 'Cancel' : '← Back'}</button>
      ${w.step < 2
        ? `<button class="btn" onclick="provWizNav(1)" ${w.step === 0 && !w.id ? 'disabled' : ''}>Next →</button>`
        : `<button class="btn" onclick="provSave()">Save provider</button>`}</div></div>`;
}

function provPick(id){
  const entry = (SET.cfg.catalog || []).find(c => c.id === id) || {};
  SET.provWiz.id = id;
  SET.provWiz.base_url = entry.base_url || '';
  SET.provWiz.default_model = (SET.cfg.providers[id] || {}).default_model || '';
  renderSettings();
}

function provCapture(){
  const w = SET.provWiz;
  if(w.step === 1){
    const idEl = document.getElementById('pw-id');
    if(idEl && !idEl.disabled) w.id = idEl.value.trim();
    w.base_url = document.getElementById('pw-base').value.trim();
    const keyEl = document.getElementById('pw-key');
    if(keyEl && keyEl.value.trim()) w.api_key = keyEl.value.trim();
    w.default_model = document.getElementById('pw-model').value.trim();
  }
}

function provWizNav(delta){
  provCapture();
  const w = SET.provWiz;
  if(w.step === 0 && delta < 0){ SET.provWiz = null; renderSettings(); return; }
  if(w.step === 1 && delta > 0 && (!w.id || !w.base_url)){ toast('id and base URL are required'); return; }
  const entering = w.step === 0 && delta > 0;
  w.step = Math.max(0, Math.min(2, w.step + delta));
  renderSettings();
  if(entering && w.models === null) provFetchModels();  // keyless/stored-key just works
}

async function provFetchModels(){
  provCapture();
  const w = SET.provWiz;
  const msg = document.getElementById('pw-models-msg');
  if(msg) msg.innerHTML = '<span class="spin"></span> fetching model list…';
  const r = await post('/api/probe', {provider: w.id, base_url: w.base_url, api_key: w.api_key});
  if(SET.provWiz !== w) return;  // wizard closed meanwhile
  w.models = r.ok ? (await r.json()).models : [];
  renderSettings();
}

async function provTest(){
  const w = SET.provWiz;
  document.getElementById('pw-test').innerHTML = '<span class="spin"></span> testing…';
  const r = await post('/api/test_worker', {kind: 'openai_compat', provider: w.id,
    base_url: w.base_url, api_key: w.api_key, model: w.default_model});
  w.test = r.ok ? await r.json() : {ok: false, error: (await r.text()).slice(0,140)};
  renderSettings();
}

async function provSave(){
  provCapture();
  const w = SET.provWiz;
  const entry = (SET.cfg.catalog || []).find(c => c.id === w.id) || {};
  const body = {id: w.id, base_url: w.base_url, default_model: w.default_model,
                label: entry.label || w.id, keyless: !!entry.keyless};
  if(w.api_key) body.api_key = w.api_key;
  const r = await post('/api/config/providers', body);
  if(!r.ok){ toast('save failed: ' + (await r.text()).slice(0,140)); return; }
  toast('Provider ' + w.id + ' saved');
  SET.provWiz = null;
  renderSettings(true);
}

/* ---------- agent wizard: kind → connection → role & prompt → test & save ---------- */
const PROMPT_ARCHETYPES = {
  solver: {label: 'Solver', prompt:
`You are a focused task-solver. Work only on the task given; never invent extra scope.
Method: read every input fully, reason step by step, then answer.
Output discipline: give exactly what the task asks for — no preamble, no commentary.
If the task is impossible with the given inputs, say so explicitly instead of guessing.`},
  reviewer: {label: 'Reviewer', prompt:
`You are an adversarial reviewer. Treat every claim in the input as unverified.
Hunt for: requirements gamed rather than met, missing evidence, edge cases, contradictions.
Report findings ranked by severity, each with the exact location and why it fails.
End with a single verdict line: SHIP or NO-SHIP with one-sentence justification.
You did not produce the work you are reviewing — judge it with fresh eyes.`},
  planner: {label: 'Planner', prompt:
`You are a planning specialist. Decompose objectives into small, verifiable steps.
Each step must name its inputs, its output, and how to check it succeeded.
Prefer few well-scoped steps; flag any step whose success cannot be checked mechanically.
Never begin executing — your only output is the plan.`},
  extractor: {label: 'Extractor', prompt:
`You are a precise data extractor. Return only what is literally present in the input.
Never infer, summarize, or normalize unless the task says to.
If a requested field is absent, return it as null — never fabricate a value.
Match the requested output format exactly.`},
  coder: {label: 'Coding agent', prompt:
`You are a careful software engineer working inside a scoped workspace.
Follow the plan you are given; note any forced deviation explicitly.
Write tests alongside code — never defer them. Never weaken an existing test to make it pass.
Keep changes minimal and consistent with the surrounding code style.`},
};

function startAgentWizardIdx(i){ startAgentWizard((SET.cfg.agents || [])[i] || null); }

function startAgentWizard(a){
  SET.agentWiz = {
    step: 0, editing: !!a,
    kind: a ? a.kind : 'openai_compat',
    worker_id: a ? a.worker_id : '', tier: a ? a.tier : 'small',
    provider: a ? a.provider : '', base_url: a ? a.base_url : '',
    model: a ? a.model : '', cli: a ? a.cli : '',
    system_prompt: a ? a.system_prompt : '', archetype: '',
    probed: [], test: null };
  if(SET.cfg) renderSettings(); else showView('settings');
}

function agentWizSteps(){
  const labels = ['Kind','Connection','Role & prompt','Test & save'];
  return '<div class="subwiz-steps">' + labels.map((l, i) =>
    `<span class="t ${i === SET.agentWiz.step ? 'on' : ''} ${i < SET.agentWiz.step ? 'done' : ''}">${i + 1} · ${l}</span>`).join('') + '</div>';
}

function renderAgentWizard(){
  const w = SET.agentWiz;
  let inner = '';
  if(w.step === 0){
    inner = `<div class="sub">What kind of agent is this?</div>
      <div class="pillrow">
        <button class="pill ${w.kind === 'openai_compat' ? 'on' : ''}" onclick="agentSet('kind','openai_compat')">LLM endpoint</button>
        <button class="pill ${w.kind === 'coding_cli' ? 'on' : ''}" onclick="agentSet('kind','coding_cli')">Coding CLI</button>
        <button class="pill ${w.kind === 'subscription_cli' ? 'on' : ''}" onclick="agentSet('kind','subscription_cli')">Subscription (Claude Code / Codex)</button>
        <button class="pill ${w.kind === 'mock' ? 'on' : ''}" onclick="agentSet('kind','mock')">Mock (testing)</button></div>
      <div class="hint-panel">${{
        openai_compat: '<b>LLM endpoint</b>Any OpenAI-compatible API: a configured provider (Anthropic, OpenAI, …) or a local server (Ollama, LM Studio). Does text-work steps.',
        coding_cli: '<b>Coding CLI</b>A full coding harness (Pi, Codex, OpenCode, Claude Code) run headless per task in a workspace. Gives the harness hands: it can implement plans and write real code.',
        subscription_cli: '<b>Subscription access</b>LLM completions through your signed-in Claude Code (Anthropic subscription) or Codex CLI (OpenAI subscription). No API key stored — the CLI login is the credential. Codex runs read-only; these agents answer, they do not edit.',
        mock: '<b>Mock</b>Deterministic offline worker for demos and tests.'}[w.kind]}</div>`;
  }else if(w.step === 1){
    if(w.kind === 'subscription_cli'){
      const subs = Object.entries(SET.cfg.subscriptions || {});
      const chosen = (SET.cfg.subscriptions || {})[w.cli];
      inner = `<div class="sub">Which subscription answers for this agent?</div>
        <div class="pillrow">${subs.map(([name, st]) =>
          `<button class="pill ${w.cli === name ? 'on' : ''}" ${st.installed ? '' : 'disabled'}
             onclick="agentSet('cli','${esc(name)}')">${esc(st.label)}</button>`).join('')}</div>
        ${chosen ? (chosen.authenticated
          ? `<div class="hint-panel"><b>Signed in</b><span class="kv">${esc(chosen.path)}</span></div>`
          : `<div class="hint-panel"><b>Not signed in yet</b>${esc(chosen.login_hint)} — then come back and Test.</div>`) : ''}
        <div class="field" style="margin-top:10px"><label>Model (optional — CLI default otherwise)</label>
          <input id="aw-model" class="mono" value="${esc(w.model)}" placeholder="e.g. ${chosen && chosen.models.length ? esc(chosen.models[0]) : 'default'}">
          ${modelPickList('aw-pick', 'aw-model', (chosen && chosen.models) || [], '')}</div>`;
    }else if(w.kind === 'coding_cli'){
      const clis = Object.keys(SET.cfg.coding_clis || {});
      const keyHint = w.cli ? (SET.cfg.cli_key_hints || {})[w.cli] : '';
      inner = clis.length ? `<div class="sub">Installed coding CLIs (detected on PATH)</div>
        <div class="pillrow">${clis.map(c =>
          `<button class="pill ${w.cli === c ? 'on' : ''}" onclick="agentSet('cli','${esc(c)}')">${esc(c)}</button>`).join('')}</div>
        ${keyHint ? `<div class="hint-panel"><b>This harness brings its own credentials</b>
          ${esc(keyHint)} — the meta-harness never stores or proxies them.</div>` : ''}
        <div class="field" style="margin-top:10px"><label>Model (optional — CLI default otherwise)</label>
          <div style="display:flex;gap:8px">
            <input id="aw-model" class="mono" value="${esc(w.model)}" placeholder="provider/model-id" style="flex:1"
              oninput="filterPickList('aw-model','aw-pick', () => SET.agentWiz.probed)">
            <button class="btn ghost" onclick="cliFetchModels()" ${w.cli ? '' : 'disabled'}>List models</button></div>
          ${modelPickList('aw-pick', 'aw-model', w.probed, '')}
          <span class="small dim" id="aw-cli-msg">${w.probed.length ? w.probed.length + ' model(s) — pick from the list' : ''}</span></div>`
        : '<div class="hint-panel"><b>No coding CLIs found</b>Install pi, codex, opencode or claude and reopen this wizard.</div>';
    }else if(w.kind === 'mock'){
      inner = '<div class="hint-panel">Mock workers need no connection.</div>';
    }else{
      const provs = Object.values(SET.cfg.providers || {});
      inner = `<div class="sub">Configured provider, or a direct endpoint URL</div>
        <div class="pillrow">
          <button class="pill ${!w.provider ? 'on' : ''}" onclick="agentSet('provider','')">Direct URL</button>
          ${provs.map(p => `<button class="pill ${w.provider === p.id ? 'on' : ''}" onclick="agentSet('provider','${esc(p.id)}')">${esc(p.label || p.id)}</button>`).join('')}</div>
        ${w.provider ? `<div class="kv" style="margin:8px 0">${esc((SET.cfg.providers[w.provider] || {}).base_url)}</div>` : `
        <div class="field"><label>Base URL</label>
          <input id="aw-base" class="mono" value="${esc(w.base_url || 'http://localhost:1234/v1')}"></div>`}
        <div class="field"><label>Model</label>
          <div style="display:flex;gap:8px">
            <input id="aw-model" class="mono" value="${esc(w.model || (SET.cfg.providers[w.provider] || {}).default_model || '')}" placeholder="model id" style="flex:1"
              oninput="filterPickList('aw-model','aw-pick', () => SET.agentWiz.probed)">
            <button class="btn ghost" onclick="agentFetchModels()">List models</button></div>
          ${modelPickList('aw-pick', 'aw-model', w.probed, '')}
          <span class="small dim" id="aw-probe-msg">${w.probed.length ? w.probed.length + ' model(s) live from the endpoint — pick from the list' : ''}</span></div>`;
    }
  }else if(w.step === 2){
    inner = `
      <div class="field" style="display:flex;gap:10px">
        <span style="flex:1"><label>Worker id</label><input id="aw-id" class="mono" value="${esc(w.worker_id)}" placeholder="review-bot" ${w.editing ? 'disabled' : ''}></span>
        <span style="width:140px"><label>Tier</label><select id="aw-tier">
          ${['small','mid','frontier'].map(t => `<option value="${t}" ${w.tier === t ? 'selected' : ''}>${t}</option>`).join('')}</select></span></div>
      <div class="field"><label>System prompt — start from an archetype, then tailor it</label>
        <div class="pillrow">${Object.entries(PROMPT_ARCHETYPES).map(([k, a]) =>
          `<button class="pill ${w.archetype === k ? 'on' : ''}" onclick="agentArchetype('${k}')">${a.label}</button>`).join('')}</div>
        <textarea id="aw-prompt" style="min-height:150px" placeholder="Leave empty for the neutral default worker prompt.">${esc(w.system_prompt)}</textarea></div>
      <div class="hint-panel"><b>What a good agent system prompt does</b>
        <ul>
          <li><b>Role + method</b> — what it is and HOW it should work, not a personality.</li>
          <li><b>Output discipline</b> — exactly-what-was-asked; the harness verifies literal answers.</li>
          <li><b>Honesty valves</b> — say-so-if-impossible beats confident guessing; UNVERIFIED answers stop the pipeline safely.</li>
          <li><b>No scope creep</b> — the task contract (objective, boundaries, schema) is appended after this prompt and always wins.</li>
        </ul></div>`;
  }else{
    inner = `
      <div class="hint-panel"><b>About to ${w.editing ? 'update' : 'register'}</b>
        <span class="kv">${esc(w.worker_id)} · ${esc(w.tier)} · ${esc(w.kind)}
        ${w.kind === 'coding_cli' ? '· ' + esc(w.cli) : '· ' + esc(w.provider || w.base_url) + (w.model ? ' · ' + esc(w.model) : '')}</span></div>
      <div style="display:flex;gap:10px;align-items:center">
        <button class="btn ghost" onclick="agentTest()">Test</button>
        <span class="small" id="aw-test">${w.test === null ? '' : w.test.ok
          ? `<span class="green">✔ ok${w.test.latency_ms ? ' · ' + w.test.latency_ms + 'ms' : ''}${w.test.reply ? ' · “' + esc(w.test.reply.slice(0,60)) + '”' : ''}</span>`
          : `<span class="red">✘ ${esc(w.test.error || w.test.detail || 'failed')}</span>`}</span></div>
      <div class="small dim" style="margin-top:10px">Saving registers the agent's signed identity, routes its tier to it, and persists it to config.</div>`;
  }
  return `<div class="card"><h2>${w.editing ? 'Edit' : 'Add'} agent</h2>
    ${agentWizSteps()}${inner}
    <div class="wiz-nav">
      <button class="btn ghost" onclick="agentWizNav(-1)">${w.step === 0 ? 'Cancel' : '← Back'}</button>
      ${w.step < 3
        ? `<button class="btn" onclick="agentWizNav(1)">Next →</button>`
        : `<button class="btn" onclick="agentSave()">${w.editing ? 'Update' : 'Register'} agent</button>`}</div></div>`;
}

function agentSet(key, value){
  SET.agentWiz[key] = value;
  if(key === 'provider'){ SET.agentWiz.model = ''; SET.agentWiz.probed = []; }
  if(key === 'cli'){ SET.agentWiz.probed = []; }
  renderSettings();
  if(key === 'provider') agentFetchModels();  // stored key/keyless: fetch instantly
  if(key === 'cli' && SET.agentWiz.kind === 'coding_cli') cliFetchModels();
}

async function cliFetchModels(){
  agentCapture();
  const w = SET.agentWiz;
  const msg = document.getElementById('aw-cli-msg');
  if(msg) msg.innerHTML = '<span class="spin"></span> asking the CLI for its models…';
  const r = await post('/api/cli_models', {cli: w.cli});
  if(SET.agentWiz !== w) return;
  w.probed = r.ok ? (await r.json()).models : [];
  renderSettings();
}

function agentArchetype(k){
  agentCapture();
  SET.agentWiz.archetype = k;
  SET.agentWiz.system_prompt = PROMPT_ARCHETYPES[k].prompt;
  renderSettings();
}

function agentCapture(){
  const w = SET.agentWiz;
  const grab = id => { const el = document.getElementById(id); return el ? el.value : null; };
  const base = grab('aw-base'); if(base !== null) w.base_url = base.trim();
  const model = grab('aw-model'); if(model !== null) w.model = model.trim();
  const id = grab('aw-id'); if(id !== null && !w.editing) w.worker_id = id.trim();
  const tier = grab('aw-tier'); if(tier !== null) w.tier = tier;
  const prompt = grab('aw-prompt'); if(prompt !== null) w.system_prompt = prompt;
}

async function agentFetchModels(){
  agentCapture();
  const w = SET.agentWiz;
  const msg = document.getElementById('aw-probe-msg');
  if(msg) msg.innerHTML = '<span class="spin"></span> fetching model list…';
  const r = await post('/api/probe', {provider: w.provider, base_url: w.provider ? '' : w.base_url});
  if(SET.agentWiz !== w) return;
  const data = r.ok ? await r.json() : {reachable: false, models: []};
  w.probed = data.models || [];
  renderSettings();
  if(!data.reachable){
    const m2 = document.getElementById('aw-probe-msg');
    if(m2) m2.textContent = 'endpoint did not list models — check the key, URL, and that the server is running';
  }
}

function agentWizNav(delta){
  agentCapture();
  const w = SET.agentWiz;
  if(w.step === 0 && delta < 0){ SET.agentWiz = null; renderSettings(); return; }
  if(delta > 0){
    if(w.step === 1 && w.kind === 'coding_cli' && !w.cli){ toast('pick a coding CLI'); return; }
    if(w.step === 1 && w.kind === 'subscription_cli' && !w.cli){ toast('pick a subscription CLI'); return; }
    if(w.step === 1 && w.kind === 'openai_compat' && !w.provider && !w.base_url){ toast('provider or base URL needed'); return; }
    if(w.step === 2 && !w.worker_id){ toast('give the agent a worker id'); return; }
  }
  const entering = delta > 0 && w.step === 0;
  w.step = Math.max(0, Math.min(3, w.step + delta));
  renderSettings();
  if(entering && w.step === 1 && w.kind === 'openai_compat' && !w.probed.length) agentFetchModels();
}

async function agentTest(){
  agentCapture();
  const w = SET.agentWiz;
  document.getElementById('aw-test').innerHTML = '<span class="spin"></span> testing…';
  const r = await post('/api/test_worker', {kind: w.kind, provider: w.provider,
    base_url: w.provider ? '' : w.base_url,  // provider ref wins; never a stale direct URL
    model: w.model, system_prompt: w.system_prompt, cli: w.cli});
  w.test = r.ok ? await r.json() : {ok: false, error: (await r.text()).slice(0,140)};
  renderSettings();
}

async function agentSave(){
  agentCapture();
  const w = SET.agentWiz;
  if(w.editing){  // replace: retire old identity first, then re-register
    await fetch('/api/workers/' + encodeURIComponent(w.worker_id), {method:'DELETE'});
  }
  const r = await post('/api/workers', {worker_id: w.worker_id, tier: w.tier, kind: w.kind,
    provider: w.provider, base_url: w.provider ? '' : w.base_url, model: w.model,
    system_prompt: w.system_prompt, cli: w.cli, persist: true});
  if(!r.ok){ toast('failed: ' + (await r.text()).slice(0,140)); return; }
  toast(`${w.editing ? 'Updated' : 'Registered'} ${w.worker_id} on ${w.tier} tier`);
  SET.agentWiz = null;
  renderSettings(true);
}

setStep(0);        // pre-render the wizard so switching to Run is instant
showView('home');  // the calm landing answers "what do I do right now?"
</script>
</body>
</html>
"""
