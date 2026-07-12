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

/* card arranging: ‹ › appear on hover, order persists per browser */
.card-move{float:right;display:inline-flex;gap:2px;opacity:0;transition:opacity .15s}
.card:hover .card-move{opacity:1}
@media (prefers-reduced-motion: reduce){.card-move{transition:none}}
.card-move button{width:22px;height:22px;border-radius:7px;background:var(--hair);
  color:var(--mut2);font-size:14px;line-height:1;font-family:var(--sans)}
.card-move button:hover{background:var(--accent-soft);color:var(--accent)}

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
.library-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:16px}
.harness-card .origin{display:flex;gap:7px;align-items:center;margin-bottom:8px}
.harness-card .actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:15px}
.tool-list{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.library-filter{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px}
.version-list{border-top:1px solid var(--hair);margin-top:14px;padding-top:10px}

/* AI companion: the gradient sparkle is the ONE signal for advisory content —
   everything without it is verified, deterministic data */
.why{width:27px;height:27px;border-radius:999px;flex:0 0 auto;display:inline-flex;
  align-items:center;justify-content:center;cursor:pointer;
  background:linear-gradient(135deg,var(--accent-soft),#8b5cf622);
  border:1px solid #8b5cf630;transition:transform .15s ease,box-shadow .15s ease}
.why svg{width:15px;height:15px;display:block}
.why:hover,.why.on{transform:scale(1.12);box-shadow:0 2px 10px #8b5cf640}
.card h2 .card-advise{float:right;width:24px;height:24px;margin-left:8px}
.card h2 .card-advise svg{width:14px;height:14px}
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
.tierrow .poolmember{padding:4px 0}
.tierrow .poolmember + .poolmember{border-top:1px solid var(--hair)}
.tierrow .poolmember .badge{margin-left:6px}

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
.evidence-panel{margin-top:14px;border:1px solid var(--line);border-radius:12px;padding:12px 14px;background:#fff}
.evidence-panel h3{font-size:13px;margin:0 0 4px}
.evidence-item{border-top:1px solid var(--hair);padding-top:8px;margin-top:8px}
.evidence-item summary{cursor:pointer;font-size:12.5px;font-weight:600;color:var(--ink2)}
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
dialog{border:1px solid var(--line);border-radius:14px;padding:0;background:var(--card);
  box-shadow:0 24px 80px rgba(22,25,32,.28);max-width:calc(100vw - 32px)}
dialog::backdrop{background:rgba(20,24,34,.28)}
.modal-body{width:min(520px,calc(100vw - 48px));padding:24px 26px}
.modal-body h2{margin-top:0}
.modal-actions{display:flex;justify-content:space-between;gap:12px;margin-top:20px}
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
    <button id="nav-library" onclick="showView('library')">Library</button>
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

<!-- ================= HARNESS LIBRARY ================= -->
<div id="view-library" class="view" style="display:none">
  <div class="eyebrow">Reusable workflows</div>
  <h1 class="greet">Harness Library</h1>
  <div class="library-filter">
    <div class="small dim">Built-ins stay immutable. Your drafts and published versions persist across restarts.</div>
    <div style="display:flex;gap:10px;align-items:center"><button class="btn" onclick="startFreshHarness()">+ Create a harness</button>
    <label class="small"><input id="library-archived" type="checkbox" style="width:auto" onchange="renderLibrary()"> Show archived</label></div>
  </div>
  <div id="library-body"><div class="card"><div class="empty">loading harnesses…</div></div></div>
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
  <div id="settings-return" style="display:none;margin-bottom:14px"><button class="btn" onclick="returnToHarnessRepair()">← Return to harness repair</button></div>
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
      <label class="small" style="display:flex;gap:8px;align-items:center;margin:10px 0">
        <input id="runs-show-archived" type="checkbox" style="width:auto"> Show archived</label>
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
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<dialog id="fork-dialog"><form method="dialog" class="modal-body"><h2>Fork this harness</h2>
  <div class="field"><label for="fork-id">Harness id</label><input id="fork-id"></div>
  <div class="field"><label for="fork-name">Harness name</label><input id="fork-name"></div>
  <div class="small red" id="fork-msg"></div><div class="modal-actions"><button class="btn ghost" value="cancel">Cancel</button>
  <button class="btn" type="button" onclick="submitForkDialog()">Create fork</button></div></form></dialog>
<dialog id="library-action-dialog"><div class="modal-body" id="library-action-body"></div></dialog>

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
let currentView = 'home';
function showView(v, force=false){
  if(currentView === 'wizard' && wiz.step === 2 && v !== 'wizard' && !syncOpenStepEditor()) return false;
  if(!force && currentView === 'wizard' && v !== 'wizard' && wiz.dirty &&
     !confirm('You have unsaved harness changes. Leave and discard them?')){
    if(wiz.step === 2) renderPlanStep();
    return false;
  }
  if(!force && currentView === 'wizard' && v !== 'wizard' && wiz.dirty) resetWizard(false, true);
  currentView = v;
  for(const name of ['home','library','wizard','settings','console','help']){
    document.getElementById('view-' + name).style.display = v === name ? '' : 'none';
    document.getElementById('nav-' + name).classList.toggle('on', v === name);
  }
  if(v === 'home') renderHome();
  if(v === 'library') renderLibrary();
  if(v === 'console') refreshConsole();
  if(v === 'settings') renderSettings(true);
  document.getElementById('settings-return').style.display = v === 'settings' && wiz.repairReturn ? '' : 'none';
  if(v === 'wizard' && wiz.step === 0) renderAgentsStep();  // agents may have changed
  return true;
}
window.addEventListener('beforeunload', e => {
  if(!wiz.dirty) return;
  e.preventDefault(); e.returnValue = '';
});

/* ---------- reusable Harness Library ---------- */
const LIB = {items: [], versions: {}, forkPending:null};
async function libraryGet(path, label){
  const r = await libraryRequest(path, {}, label);
  return r ? await r.json() : null;
}
async function libraryRequest(path, options, label, allowed=[]){
  try{
    const r = await fetch(path, options || {});
    if(!r.ok && !allowed.includes(r.status)){
      const detail = (await r.text()).slice(0,240);
      if(r.status === 409){ toast(`${label}: this draft changed elsewhere. Reload it or fork your edits.`); return null; }
      if(r.status === 422 || r.status === 400){ toast(`${label}: ${humanApiError(detail)}`); return null; }
      throw new Error(detail);
    }
    return r;
  }catch(e){ toast(`${label} failed — retry when the server is available`); return null; }
}
function humanApiError(text){
  try{ const value=JSON.parse(text); return typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail); }
  catch(e){ return text || 'check the highlighted values'; }
}
function actionButton(item, action){
  const id = `'${item.id}'`, version = item.latest_version || 1;
  const editVersion = item.latest_version === null ? 'null' : item.latest_version;
  const handlers = {
    run: `libraryRun(${id},${version},'${item.origin}')`, edit: `libraryEdit(${id},${editVersion},'${item.origin}')`,
    fork: `libraryFork(${id},${version})`, versions: `libraryVersions(${id})`,
    archive: `libraryArchive(${id})`, restore: `libraryRestore(${id})`,
    publish: `libraryPublish(${id})`, delete_draft: `libraryDeleteDraft(${id})`
  };
  if(!handlers[action]) return '';
  const labels = {run:'Run',edit:'Edit',fork:'Fork',versions:'Versions',archive:'Archive',
    restore:'Restore',publish:'Publish',delete_draft:'Delete draft'};
  return `<button class="pill" data-action="${esc(action)}" onclick="${handlers[action]}">${labels[action]}</button>`;
}
async function renderLibrary(){
  const body = document.getElementById('library-body');
  const archived = !!document.getElementById('library-archived')?.checked;
  body.innerHTML = '<div class="card"><div class="empty">loading harnesses…</div></div>';
  try{
    LIB.items = await get('/api/blueprints?include_archived=' + archived);
    const runs = await get('/api/runs');
    const details = {};
    await Promise.all(LIB.items.map(async item=>{
      try{
        const record=item.has_draft ? await get('/api/blueprint-drafts/'+encodeURIComponent(item.id))
          : item.latest_version ? await get(`/api/blueprints/${encodeURIComponent(item.id)}/versions/${item.latest_version}`) : null;
        if(record) details[item.id]=record;
      }catch(e){}
    }));
    body.innerHTML = `<div class="library-grid">${LIB.items.map(item => {
      const state = item.archived ? badge('dim','archived') : item.has_draft
        ? badge('warn', item.latest_version ? 'draft changes' : 'draft') : badge('ok','published');
      const versions = LIB.versions[item.id];
      const detail=details[item.id] || {}, lastRun=[...runs].reverse().find(r=>r.blueprint_ref && r.blueprint_ref.id===item.id);
      const friendlyCaps=capabilityLabels(item.tool_ids || []);
      return `<div class="card harness-card" data-harness-id="${esc(item.id)}">
        <div class="origin">${badge(item.origin === 'builtin' ? 'act' : 'dim', item.origin)} ${state}</div>
        <h2>${esc(item.display_name)}</h2>
        <div class="sub mono">${esc(item.id)}${item.latest_version ? ' · v' + item.latest_version : ''}</div>
        <p class="small">${esc(detail.description || 'No description yet.')}</p>
        <div class="small">${item.stage_count} stage${item.stage_count === 1 ? '' : 's'}</div>
        <div class="tool-list">${friendlyCaps.map(t => badge('act',t)).join('') || '<span class="small dim">No external capabilities</span>'}</div>
        <div class="small dim">${detail.published_at ? 'Updated '+ago(detail.published_at) : detail.updated_at ? 'Updated '+ago(detail.updated_at) : 'Update time unavailable'} · ${lastRun ? 'Last run '+ago(lastRun.updated_at || lastRun.started_at) : 'Never run'} · Eval status unavailable</div>
        <div class="actions">${(item.supported_actions || []).map(a => actionButton(item,a)).join('')}</div>
        ${item.latest_version ? `<div class="actions"><button class="pill" onclick="libraryEvaluate('${esc(item.id)}',${item.latest_version})">Evaluate</button><button class="pill" onclick="libraryTune('${esc(item.id)}',${item.latest_version})">Tune</button><button class="pill" onclick="libraryPackage('${esc(item.id)}',${item.latest_version})">Package</button></div>` : ''}
        ${versions ? `<div class="version-list"><div class="kv">Immutable versions</div>${versions.map(v =>
          `<div class="lrow"><span class="mono">v${v.version}</span><span class="small dim">${ago(v.published_at)}</span>
           ${item.archived ? '' : `<button class="pill" onclick="libraryRun('${esc(item.id)}',${v.version},'${item.origin}')">Run</button>
           <button class="pill" onclick="libraryEdit('${esc(item.id)}',${v.version},'${item.origin}')">Edit</button>
           <button class="pill" onclick="libraryFork('${esc(item.id)}',${v.version})">Fork</button>`}</div>`).join('')}</div>` : ''}
      </div>`;
    }).join('')}</div>`;
  }catch(e){ body.innerHTML = '<div class="card"><div class="empty">Harness Library is unavailable.</div><button class="btn ghost" onclick="renderLibrary()">Retry</button></div>'; }
}
function closeLibraryAction(){ document.getElementById('library-action-dialog').close(); }
async function libraryPackage(id, version){
  const url=`/api/blueprints/${encodeURIComponent(id)}/versions/${version}/package`;
  let r; try{ r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({targets:['local']})}); }
  catch(e){ toast('Package failed — retry when the server is available'); return; }
  if(!r.ok){ toast('Package failed: '+humanApiError(await r.text())); return; }
  const blob=await r.blob(), a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download=`${id}-v${version}-portable.zip`; a.click(); URL.revokeObjectURL(a.href);
  toast(`Packaged ${id} v${version} for local use`);
}
async function libraryEvaluate(id, version){
  const record=await libraryGet(`/api/blueprints/${encodeURIComponent(id)}/versions/${version}`,'Open evaluation'); if(!record) return;
  const refs=record.eval_suites || [], body=document.getElementById('library-action-body');
  body.innerHTML=`<h2>Evaluate ${esc(record.name || id)} · v${version}</h2>${refs.length ? `
    <div class="field"><label>Evaluation suite</label><select id="la-eval-ref">${refs.map((ref,i)=>`<option value="${i}">${esc(ref.id)} · v${ref.version}</option>`).join('')}</select></div>
    <div class="field"><label>Report id</label><input id="la-report-id" value="${esc(slugify(id+'-'+Date.now()))}"></div>
    <div class="field"><label>Runner command</label><input id="la-runner-command" class="mono" placeholder="metaharness-eval-runner"></div>
    <div class="small dim">The command runs in the evaluation sandbox. No evaluation starts until you submit it.</div>
    <div class="wiz-nav"><button class="btn ghost" onclick="closeLibraryAction()">Cancel</button><button class="btn" onclick="submitLibraryEvaluate('${esc(id)}',${version})">Start evaluation</button></div>`
    : `<div class="empty">This exact version has no evaluation suite attached. Edit or fork the harness, attach a published suite, then publish a new version.</div><div class="wiz-nav"><button class="btn" onclick="closeLibraryAction()">Close</button></div>`}`;
  document.getElementById('library-action-dialog').showModal();
  LIB.actionRecord=record;
}
async function submitLibraryEvaluate(id,version){
  const refs=LIB.actionRecord?.eval_suites || [], ref=refs[+document.getElementById('la-eval-ref').value];
  const reportId=document.getElementById('la-report-id').value.trim(), command=document.getElementById('la-runner-command').value.trim();
  if(!reportId || !command){ toast('Give the report a name and runner command'); return; }
  const r=await post(`/api/blueprints/${encodeURIComponent(id)}/versions/${version}/evaluate`,{report_id:reportId,eval_ref:ref,split:'development',runner:{runner_id:'dashboard-runner',argv:[command]}});
  if(!r.ok){ toast('Evaluation failed: '+humanApiError(await r.text())); return; }
  closeLibraryAction(); toast(`Evaluation report ${reportId} created`);
}
async function libraryTune(id,version){
  const record=await libraryGet(`/api/blueprints/${encodeURIComponent(id)}/versions/${version}`,'Open tuning'); if(!record) return;
  const body=document.getElementById('library-action-body');
  body.innerHTML=`<h2>Tune ${esc(record.name || id)} · v${version}</h2>
    <div class="field"><label>Evaluation report references (JSON)</label><textarea id="la-report-refs" class="mono" placeholder='[{"id":"report-1","content_digest":"…","split":"development"}]'></textarea></div>
    <div class="field"><label>Safe changes (JSON)</label><textarea id="la-patches" class="mono" placeholder='[{"op":"set_description","value":"Improved description"}]'></textarea></div>
    <div class="field"><label>Why these changes?</label><textarea id="la-rationale"></textarea></div>
    <div class="small dim">This creates an inert proposal for human review; it does not publish or apply changes.</div>
    <div class="wiz-nav"><button class="btn ghost" onclick="closeLibraryAction()">Cancel</button><button class="btn" onclick="submitLibraryTune('${esc(id)}',${version})">Create proposal</button></div>`;
  document.getElementById('library-action-dialog').showModal();
}
async function submitLibraryTune(id,version){
  let reportRefs, patches; try{ reportRefs=JSON.parse(document.getElementById('la-report-refs').value); patches=JSON.parse(document.getElementById('la-patches').value); }
  catch(e){ toast('Report references and safe changes must be valid JSON'); return; }
  const rationale=document.getElementById('la-rationale').value.trim(); if(!rationale){ toast('Explain why these changes are proposed'); return; }
  const proposalId=slugify(`${id}-proposal-${Date.now()}`);
  const r=await post(`/api/blueprints/${encodeURIComponent(id)}/versions/${version}/tune`,{proposal_id:proposalId,report_refs:reportRefs,patches,rationale,human_approved:false});
  if(!r.ok){ toast('Tuning proposal failed: '+humanApiError(await r.text())); return; }
  closeLibraryAction(); toast(`Tuning proposal ${proposalId} created for review`);
}
function capabilityLabels(tools){
  const labels=[]; const set=new Set(tools);
  if(['read_file','list_files'].some(t=>set.has(t))) labels.push('Read workspace');
  if(['edit_file','write_file'].some(t=>set.has(t))) labels.push('Change files');
  if(set.has('grep')) labels.push('Search workspace'); if(set.has('web_fetch')) labels.push('Use the web'); if(set.has('calculator')) labels.push('Calculate');
  [...new Set(tools.filter(t=>t.includes('.')).map(t=>`Connected: ${t.split('.')[0]}`))].forEach(x=>labels.push(x)); return labels;
}
function slugify(s){ return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,80); }
function blueprintContent(record){
  return {schema_version: record.schema_version || 1, name: record.name,
    description: record.description || '', workflow: record.workflow,
    inputs: record.inputs || [], default_context: record.default_context || {},
    eval_suites: record.eval_suites || [], source: record.source || null};
}
function loadBlueprintEditor(record, mode, origin='owned'){
  resetWizard(false, true);
  wiz.blueprintMode = mode; wiz.blueprintId = record.id;
  wiz.blueprintVersion = record.version || record.base_version || null;
  wiz.blueprintRevision = record.revision || null;
  wiz.blueprintSource = record.source || null;
  wiz.blueprintOrigin = origin;
  wiz.blueprintContent = blueprintContent(record);
  wiz.harnessName = record.name || ''; wiz.harnessDescription = record.description || '';
  wiz.harnessSlug = record.id || '';
  wiz.inputDefs = (record.inputs || []).filter(i=>i.name !== 'goal').map(i=>({name:i.name,type:schemaType(i),required:!!i.required,secret:!!i.secret,default:i.secret && i.default ? i.default.binding : (i.default ?? '')}));
  wiz.blueprintOriginal = JSON.stringify(wiz.blueprintContent);
  wiz.plan = structuredClone(record.workflow); wiz.planSource = `blueprint:${record.id}`;
  wiz.goal = ''; wiz.context = {};
  wiz.dirty = false; wiz.edited = false;
  wiz.step = mode === 'run' ? 1 : 2;
  showView('wizard', true);
}
async function libraryRun(id, version, origin='owned'){
  const record = await libraryGet(`/api/blueprints/${encodeURIComponent(id)}/versions/${version}`, 'Load harness');
  if(!record) return;
  loadBlueprintEditor(record, 'run', origin);
  setStep(1); // collect inputs first; no run starts until the review screen is confirmed
}
async function libraryEdit(id, version, origin){
  if(origin === 'builtin') return libraryFork(id, version);
  let r = await libraryRequest(`/api/blueprint-drafts/${encodeURIComponent(id)}`, {}, 'Load draft', [404]);
  if(!r) return;
  if(r.status === 404){
    if(version === null){ toast('The draft no longer exists — refresh the Library'); return; }
    r = await libraryRequest('/api/blueprint-drafts', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({blueprint_id:id, base_version:version})}, 'Create draft');
  }else{
    const existing = await r.json();
    if(version !== null && existing.base_version !== version){
      const openExisting = confirm(`A draft based on v${existing.base_version || 'new'} already exists.\n\nOK: open that existing draft.\nCancel: create a separate fork from v${version}.`);
      if(!openExisting) return libraryFork(id, version);
      loadBlueprintEditor(existing, 'draft', origin); setStep(2); return;
    }
    loadBlueprintEditor(existing, 'draft', origin); setStep(2); return;
  }
  if(!r) return;
  loadBlueprintEditor(await r.json(), 'draft', origin); setStep(2);
}
async function libraryFork(id, version, contentOverride=null, chosen=null){
  const suggested = slugify('my-' + id);
  if(!chosen){ LIB.forkPending={id,version,contentOverride}; document.getElementById('fork-id').value=suggested; document.getElementById('fork-name').value=suggested.replace(/-/g,' '); document.getElementById('fork-msg').textContent=''; document.getElementById('fork-dialog').showModal(); return; }
  const newId=chosen.id, name=chosen.name;
  const r = await libraryRequest(`/api/blueprints/${encodeURIComponent(id)}/fork`, {method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({new_id:newId, source_version:version, display_name:name})}, 'Fork harness');
  if(!r) return;
  let draft = await r.json();
  if(contentOverride){
    const updated = await libraryRequest(`/api/blueprint-drafts/${encodeURIComponent(newId)}`, {method:'PATCH',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({content:contentOverride, expected_revision:draft.revision})}, 'Preserve fork edits');
    if(!updated){
      await libraryRequest(`/api/blueprint-drafts/${encodeURIComponent(newId)}`, {method:'DELETE'}, 'Clean up fork');
      toast('Fork could not preserve your edits; the original remains open'); return;
    }
    draft = await updated.json();
  }
  delete LIB.versions[newId];
  loadBlueprintEditor(draft, 'draft', 'fork'); setStep(2);
}
function submitForkDialog(){
  const pending=LIB.forkPending, id=document.getElementById('fork-id').value.trim(), name=document.getElementById('fork-name').value.trim();
  if(!pending || !id || !name){ document.getElementById('fork-msg').textContent='Name and id are required.'; return; }
  if(!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id)){ document.getElementById('fork-msg').textContent='Use lowercase letters, numbers, and single hyphens for the id.'; return; }
  document.getElementById('fork-dialog').close(); LIB.forkPending=null; libraryFork(pending.id,pending.version,pending.contentOverride,{id,name});
}
async function libraryVersions(id){
  const versions = await libraryGet(`/api/blueprints/${encodeURIComponent(id)}/versions`, 'Load versions');
  if(!versions) return;
  LIB.versions[id] = versions;
  renderLibrary();
}
async function libraryArchive(id){
  if(!confirm('Archive this published harness? Runs and versions are preserved.')) return;
  const r = await libraryRequest(`/api/blueprints/${encodeURIComponent(id)}/archive`, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'}, 'Archive harness');
  if(r){ delete LIB.versions[id]; renderLibrary(); }
}
async function libraryRestore(id){
  const r = await libraryRequest(`/api/blueprints/${encodeURIComponent(id)}/restore`, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'}, 'Restore harness');
  if(r){ delete LIB.versions[id]; renderLibrary(); }
}
async function libraryDeleteDraft(id){
  if(!confirm('Permanently delete this draft? Published versions are never deleted.')) return;
  const r = await libraryRequest(`/api/blueprint-drafts/${encodeURIComponent(id)}`, {method:'DELETE'}, 'Delete draft');
  if(r){ delete LIB.versions[id]; renderLibrary(); }
}
async function libraryPublish(id){
  const draft = await libraryGet(`/api/blueprint-drafts/${encodeURIComponent(id)}`, 'Load draft');
  if(!draft) return;
  const r = await libraryRequest(`/api/blueprint-drafts/${encodeURIComponent(id)}/publish`, {method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({expected_revision:draft.revision})}, 'Publish harness');
  if(r){ delete LIB.versions[id]; toast(`Published v${(await r.json()).version}`); renderLibrary(); }
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
    const [runs, workers, prov, playbook, tuning, blueprints] = await Promise.all([
      get('/api/runs'), get('/api/workers'), get('/api/provenance'),
      get('/api/playbook'), get('/api/optimization'), get('/api/blueprints')]);
    document.getElementById('home-date').textContent =
      new Date().toLocaleDateString(undefined, {weekday:'long', month:'long', day:'numeric'});
    const q = nextActions(runs, workers, prov, tuning);
    const first = q[0];
    document.getElementById('home-next').innerHTML = `<div class="next-action">
      <div class="txt"><div class="eyebrow">${esc(first.eyebrow)}</div>
      <h2>${esc(first.title)}</h2><p>${esc(first.detail)}</p></div>
      <button class="btn" onclick="showView('${first.go}')">${esc(first.cta)}</button></div>`
      + (q.length > 1 ? `<div class="also">Also waiting: ${q.slice(1).map(x => esc(x.title)).join('  ·  ')}</div>` : '');
    document.getElementById('home-next').innerHTML += `<div style="display:flex;gap:10px;margin:0 0 18px;flex-wrap:wrap">
      <button class="btn" onclick="startFreshHarness()">Create a harness</button>
      <button class="btn ghost" onclick="showView('library')">Open Harness Library</button></div>`;

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
    const latestSearch = tuning.filter(s => s.report && !s.running)
      .sort((a, b) => (b.report.finished_at || 0) - (a.report.finished_at || 0))[0];
    const searchLine = latestSearch
      ? `<div class="small dim" style="margin-top:8px">${esc(tuningSummary(latestSearch))}</div>` : '';
    const recentHarnesses = blueprints.filter(b => b.origin !== 'builtin').slice(-3).reverse();
    document.getElementById('home-rows').innerHTML = `
      <div class="card wide"><h2>Recent harnesses</h2>
        <div class="sub">Create once, then run it again with new inputs.</div>
        ${recentHarnesses.length ? recentHarnesses.map(h => `<div class="lrow">
          <div class="rr-main"><div class="rr-title">${esc(h.display_name)}</div>
          <div class="rr-meta mono">${esc(h.id)}${h.latest_version ? ' · v' + h.latest_version : ' · draft'}</div></div>
          ${h.latest_version ? `<button class="pill" onclick="libraryRun('${esc(h.id)}',${h.latest_version},'${h.origin}')">Run</button>` : ''}
          <button class="pill" onclick="libraryEdit('${esc(h.id)}',${h.latest_version || 1},'${h.origin}')">Edit</button></div>`).join('')
          : '<div class="empty">No saved harnesses yet. Create one once, then reuse it with new inputs.</div>'}</div>
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
        ${searchLine}
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
              pinnedStep: null, fallbackReason: '', dirty: false,
              stepEditorDirty: false,
              stepEditorPriorDirty: false,
              blueprintMode: null, blueprintId: null, blueprintVersion: null,
              blueprintRevision: null, blueprintSource: null, blueprintContent: null,
              blueprintOriginal: null, blueprintOrigin: null,
              harnessName: '', harnessDescription: '', harnessSlug: '', inputDefs: [], readinessIssues: [], repairReturn:false, rerunInputs:false };

function resetBlueprintState(){
  wiz.blueprintMode = null; wiz.blueprintId = null; wiz.blueprintVersion = null;
  wiz.blueprintRevision = null; wiz.blueprintSource = null; wiz.blueprintContent = null;
  wiz.blueprintOriginal = null; wiz.blueprintOrigin = null; wiz.dirty = false;
  wiz.stepEditorDirty = false;
  wiz.stepEditorPriorDirty = false;
  wiz.harnessName = ''; wiz.harnessDescription = ''; wiz.harnessSlug = ''; wiz.inputDefs = [];
  wiz.readinessIssues=[]; wiz.repairReturn=false;
  wiz.rerunInputs=false;
}
function markPlanDirty(){ wiz.edited = true; wiz.dirty = true; }
function markStepEditorDirty(){ wiz.stepEditorDirty = true; wiz.dirty = true; }
document.addEventListener('input', e => {
  if(currentView !== 'wizard' || wiz.step !== 2) return;
  if(e.target.closest('.step-edit') || e.target.matches('.yaml-box') ||
     (e.target.id || '').startsWith('sb-')) markStepEditorDirty();
});
document.addEventListener('change', e => {
  if(currentView === 'wizard' && wiz.step === 2 &&
     (e.target.closest('.step-edit') || (e.target.id || '').startsWith('sb-')))
    markStepEditorDirty();
});

function renderStepper(){
  document.getElementById('stepper').innerHTML = STEPS.map((label, i) =>
    `<div class="s ${i === wiz.step ? 'on' : ''} ${i < wiz.step ? 'done' : ''}">
       <div class="n">${i < wiz.step ? '✓' : i + 1}</div><div class="l">${label}</div></div>`).join('');
}

function setStep(n){
  if(wiz.step === 2 && n !== 2 && !syncOpenStepEditor()) return false;
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
  return true;
}

/* ---------- step 1: agents ---------- */
async function renderAgentsStep(){
  const body = document.getElementById('wiz-body');
  body.innerHTML = '<div class="card"><div class="empty">loading agents…</div></div>';
  const [workers, routing] = await Promise.all([get('/api/workers'), get('/api/routing')]);
  let anyMember = false;
  const tiers = ['small','mid','frontier'].map(t => {
    const pool = routing[t] || {};
    const members = pool.members || [];
    const routed = pool.routed || {};
    if(members.length) anyMember = true;
    // leader = the pool member the most traffic has actually landed on
    let leader = null, leadN = 0;
    members.forEach(m => { const n = routed[m.worker_id] || 0; if(n > leadN){ leadN = n; leader = m.worker_id; } });
    const rows = members.length
      ? members.map(m => {
          const n = routed[m.worker_id] || 0;
          return `<div class="poolmember"><div class="tm">${esc(m.display_name)}${
            n ? badge('dim','routed ' + n + '×') : ''}${
            leader === m.worker_id ? badge('act','routing here') : ''}</div>
            <div class="td mono">${esc(m.worker_id)} · ${esc(m.model)}</div></div>`;
        }).join('')
      : '<div class="td">no agent — this tier can\\'t take work</div>';
    return `<div class="tierrow"><div class="tn">${t}</div>
      <div style="flex:1">${rows}</div>
      ${members.length ? badge('ok','ready') : badge('dim','empty')}</div>`;
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
      <button class="btn" ${anyMember ? '' : 'disabled'} onclick="setStep(1)">Continue →</button></div>`;
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
  if(document.getElementById('harness-name')) captureHarnessSetup();
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
  if(wiz.blueprintMode === 'run' || wiz.rerunInputs){
    const reusable = wiz.blueprintContent || contentForCurrentPlan();
    const inputFields = (reusable.inputs || []).map(inputRunField).join('');
    document.getElementById('wiz-body').innerHTML = `
      <div class="guide"><div><b>Run ${esc(reusable.name)} with new inputs.</b>
        <p>${wiz.blueprintMode === 'run' ? `This loads exact version v${wiz.blueprintVersion}.` : 'This keeps the current stages.'} Nothing runs until you review the stages and confirm.</p></div></div>
      <div class="card"><h2>Inputs for this run</h2>${inputFields || '<div class="empty">This harness has no declared inputs.</div>'}
        <details class="field"><summary>Advanced extra context (JSON)</summary>
          <label for="goalctx">Extra context</label><input id="goalctx" class="mono" aria-label="Other input context as JSON" value=""></details>
        <span class="small red" id="goalmsg"></span></div>
      <div class="wiz-nav"><button class="btn ghost" onclick="showView('library')">← Library</button>
        <button class="btn" onclick="reviewBlueprintRun()">Review harness →</button></div>`;
    return;
  }
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
      <div class="field" style="display:flex;gap:10px"><span style="flex:1"><label for="harness-name">Harness name</label>
        <input id="harness-name" value="${esc(wiz.harnessName)}" placeholder="Weekly incident triage"></span>
        <span style="width:240px"><label for="harness-slug">Harness id</label><input id="harness-slug" class="mono" value="${esc(wiz.harnessSlug)}" placeholder="weekly-incident-triage"></span></div>
      <div class="field"><label for="harness-description">Description</label>
        <input id="harness-description" value="${esc(wiz.harnessDescription)}" placeholder="What this reusable harness is for"></div>
      <div class="field"><label>Workflow type</label>
        <div class="pillrow">${typePills}</div>${typeNote}</div>
      <div class="field"><label for="goal">Goal</label>
        <textarea id="goal" placeholder="e.g. Read the incident report in context, classify severity as exactly low or high, summarize it for on-call, and draft the page for my approval.">${esc(wiz.goal)}</textarea></div>
      <div class="field"><label for="goalctx">Context (JSON, optional)</label>
        <input id="goalctx" class="mono" aria-label="Context as JSON" placeholder='{"report": "db-1 disk full, checkout failing"}'
          value="${esc(Object.keys(wiz.context).length ? JSON.stringify(wiz.context) : '')}"></div>
      <fieldset class="field"><legend>Reusable inputs</legend>
        <div class="small dim">Add named values that each run will ask for. Goal is handled automatically.</div>
        ${inputDefinitionRows()}
        <button class="pill" type="button" onclick="addInputDefinition()">+ Add input</button></fieldset>
      <span class="small dim" id="goalmsg"></span>
      <div style="margin-top:6px"><button class="btn ghost" onclick="adviseGoal()" id="advise-goal-btn">
        <svg style="width:13px;height:13px"><use href="#sparkle"/></svg> Improve with AI</button></div>
      <div id="goal-advice"></div></div>
    <div class="wiz-nav">
      <button class="btn ghost" onclick="setStep(0)">← Agents</button>
      <button class="btn" id="planbtn" onclick="makePlan()">Plan workflow →</button></div>`;
}

function schemaType(input){ return (input.schema || {}).type || 'string'; }
function guidedInputNames(){
  const names=['goal'];
  [...(wiz.blueprintContent?.inputs || []), ...(wiz.inputDefs || [])].forEach(input => {
    const name=String(input?.name || '').trim(); if(name && !names.includes(name)) names.push(name);
  });
  return names;
}
function inputMappingFields(value, prefix){
  const inputs=value.inputs || {};
  return `<fieldset class="field"><legend>Inputs available to this stage</legend>
    <div class="small dim">Choose which reusable run values this stage receives, and the local name used in its task.</div>
    ${guidedInputNames().map(source => {
      const found=Object.entries(inputs).find(([,v]) => v === `$context.${source}`);
      const target=found ? found[0] : source;
      return `<div style="display:flex;gap:10px;align-items:end;margin-top:8px">
        <label style="flex:1;display:flex;gap:8px;align-items:center"><input type="checkbox" style="width:auto" data-map-prefix="${prefix}" data-map-source="${esc(source)}" ${found ? 'checked' : ''}> Use <span class="mono">${esc(source)}</span></label>
        <span style="flex:1"><label>Stage input name</label><input data-map-prefix="${prefix}" data-map-target="${esc(source)}" value="${esc(target)}" ${found ? '' : 'disabled'}></span></div>`;
    }).join('')}</fieldset>`;
}
function collectMappedInputs(prefix, original={}){
  const mapped={};
  Object.entries(original).forEach(([key,value]) => { if(typeof value !== 'string' || !value.startsWith('$context.')) mapped[key]=value; });
  document.querySelectorAll(`[data-map-prefix="${prefix}"][data-map-source]`).forEach(box => {
    if(!box.checked) return;
    const target=document.querySelector(`[data-map-prefix="${prefix}"][data-map-target="${CSS.escape(box.dataset.mapSource)}"]`);
    const name=(target?.value || box.dataset.mapSource).trim(); if(name) mapped[name]=`$context.${box.dataset.mapSource}`;
  });
  return mapped;
}
document.addEventListener('change', event => {
  const box=event.target.closest?.('[data-map-source]'); if(!box) return;
  const target=document.querySelector(`[data-map-prefix="${box.dataset.mapPrefix}"][data-map-target="${CSS.escape(box.dataset.mapSource)}"]`);
  if(target) target.disabled=!box.checked;
});
function inputRunField(input){
  const name=esc(input.name), value=wiz.context[input.name] ?? input.default ?? '';
  if(input.name === 'goal') return `<div class="field"><label for="goal">Goal${input.required ? ' · required' : ''}</label><textarea id="goal" data-run-input="goal">${esc(wiz.goal || value)}</textarea></div>`;
  if(input.secret) return `<div class="field"><label for="run-input-${name}">${name} secret binding${input.required ? ' · required' : ''}</label><input id="run-input-${name}" data-run-input="${name}" data-secret="true" value="${esc(value && value.binding ? value.binding : '')}" placeholder="configured-binding-name"></div>`;
  const type=schemaType(input), htmlType=type === 'number' || type === 'integer' ? 'number' : 'text';
  if(type === 'boolean') return `<div class="field"><label><input type="checkbox" style="width:auto" data-run-input="${name}" data-value-type="boolean" ${value ? 'checked' : ''}> ${name}${input.required ? ' · required' : ''}</label></div>`;
  return `<div class="field"><label for="run-input-${name}">${name}${input.required ? ' · required' : ''}</label><input id="run-input-${name}" data-run-input="${name}" data-value-type="${esc(type)}" type="${htmlType}" value="${esc(value)}"></div>`;
}
function captureRunInputs(){
  const out={}; document.querySelectorAll('[data-run-input]').forEach(el => {
    let value=el.dataset.valueType === 'boolean' ? el.checked : el.value; if(value === '') return;
    if(el.dataset.secret) value={binding:value};
    else if(['number','integer'].includes(el.dataset.valueType)) value=Number(value);
    if(el.dataset.runInput === 'goal') wiz.goal=String(value); else out[el.dataset.runInput]=value;
  }); return out;
}
function captureHarnessSetup(){
  const grab=id=>document.getElementById(id)?.value || '';
  wiz.harnessName=grab('harness-name').trim(); wiz.harnessDescription=grab('harness-description').trim();
  wiz.harnessSlug=grab('harness-slug').trim() || slugify(wiz.harnessName);
  document.querySelectorAll('[data-input-row]').forEach((row,i)=>{
    const item=wiz.inputDefs[i]; if(!item) return;
    item.name=row.querySelector('[data-part=name]').value.trim(); item.type=row.querySelector('[data-part=type]').value;
    item.required=row.querySelector('[data-part=required]').checked; item.secret=row.querySelector('[data-part=secret]').checked;
    item.default=row.querySelector('[data-part=default]').value;
  });
}
function inputDefinitionRows(){ return wiz.inputDefs.map((d,i)=>`<div data-input-row style="display:grid;grid-template-columns:1.2fr .8fr .8fr .8fr 1.3fr auto;gap:8px;align-items:end;margin:8px 0">
  <span><label>Input name</label><input data-part="name" value="${esc(d.name)}"></span>
  <span><label>Type</label><select data-part="type">${['string','number','integer','boolean'].map(t=>`<option ${d.type===t?'selected':''}>${t}</option>`).join('')}</select></span>
  <label><input data-part="required" type="checkbox" style="width:auto" ${d.required?'checked':''}> Required</label>
  <label><input data-part="secret" type="checkbox" style="width:auto" ${d.secret?'checked':''}> Secret</label>
  <span><label>${d.secret ? 'Default binding' : 'Default'}</label><input data-part="default" value="${esc(d.default || '')}" placeholder="${d.secret ? 'binding-name' : ''}"></span>
  <button type="button" class="pill" aria-label="Remove input ${esc(d.name || i+1)}" onclick="removeInputDefinition(${i})">Remove</button></div>`).join(''); }
function addInputDefinition(){ captureHarnessSetup(); wiz.inputDefs.push({name:'',type:'string',required:false,secret:false,default:''}); renderGoalStep(); }
function removeInputDefinition(i){ captureHarnessSetup(); wiz.inputDefs.splice(i,1); renderGoalStep(); }

function reviewBlueprintRun(){
  const msg = document.getElementById('goalmsg');
  wiz.goal = '';
  const raw = document.getElementById('goalctx').value.trim();
  try{ wiz.context = Object.assign(raw ? JSON.parse(raw) : {}, captureRunInputs()); }catch(e){ msg.textContent = 'context is not valid JSON'; return; }
  const content=wiz.blueprintContent || contentForCurrentPlan();
  if((content.inputs || []).some(i => i.name === 'goal' && i.required) && !wiz.goal){
    msg.textContent = 'this harness requires a goal'; return;
  }
  wiz.rerunInputs=false;
  setStep(2);
}

async function makePlan(){
  const msg = document.getElementById('goalmsg');
  const btn = document.getElementById('planbtn');
  captureHarnessSetup();
  wiz.goal = document.getElementById('goal').value.trim();
  if(!wiz.goal){ msg.textContent = 'describe a goal first'; return; }
  if(!wiz.harnessName) wiz.harnessName=wiz.goal.slice(0,80);
  if(!wiz.harnessSlug) wiz.harnessSlug=slugify(wiz.harnessName) || 'custom-harness';
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
    wiz.dirty = true;
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
  wiz.dirty = true;
  setStep(2);
}

/* ---------- step 3: plan review + editor + builder ---------- */
const TASK_TYPES = ['classify','extract','summarize','transform','arithmetic',
                    'code_edit','reasoning','planning','general'];
let TOOLS_NAMES = null;
let TOOLS_CATALOG = null;
let TOOL_BUNDLES = [];
let ASSIGNMENT_WORKERS = [];
let ASSIGNMENT_WORKERS_LOADED = false;
async function loadToolNames(){
  if(TOOLS_NAMES === null){
    try{
      TOOLS_CATALOG = await get('/api/tools');
      TOOLS_NAMES = TOOLS_CATALOG.map(t => t.name);
      buildToolBundles();
    }catch(e){ TOOLS_CATALOG = []; TOOLS_NAMES = []; }
  }
  return TOOLS_NAMES;
}
function buildToolBundles(){
  const has = names => names.filter(n => (TOOLS_NAMES || []).includes(n));
  TOOL_BUNDLES = [
    {label:'Read workspace',tools:has(['read_file','list_files'])},
    {label:'Search workspace',tools:has(['grep','list_files'])},
    {label:'Change files',tools:has(['read_file','write_file','edit_file','grep','list_files'])},
    {label:'Use the web',tools:has(['web_fetch'])}, {label:'Calculate',tools:has(['calculator'])},
  ].filter(b=>b.tools.length);
  const mcp={}; (TOOLS_CATALOG || []).filter(t=>(t.source||'').startsWith('mcp:')).forEach(t=>{
    const server=t.source.slice(4); (mcp[server]=mcp[server]||[]).push(t.name); });
  Object.entries(mcp).forEach(([server,tools])=>TOOL_BUNDLES.push({label:`Connected: ${server}`,tools}));
}
async function loadAssignmentWorkers(){
  if(ASSIGNMENT_WORKERS_LOADED) return;
  try{ const routing=await get('/api/routing'); ASSIGNMENT_WORKERS=Object.entries(routing).flatMap(([tier,p])=>(p.members||[]).map(m=>({...m,tier}))); }
  catch(e){ ASSIGNMENT_WORKERS=[]; }
  ASSIGNMENT_WORKERS_LOADED=true;
}

function toolPicker(selected, mode){
  const groups = {};
  (TOOLS_CATALOG || []).forEach(tool =>
    (groups[tool.source || 'builtin'] = groups[tool.source || 'builtin'] || []).push(tool));
  const bundles = `<div class="hint-panel"><b>Capabilities</b><div class="small dim">Choose a friendly bundle; the exact granted tools are shown underneath.</div>
    <div class="pillrow">${TOOL_BUNDLES.map((b,i)=>`<button type="button" class="pill ${b.tools.every(t=>selected.includes(t))?'on':''}" data-tool-bundle="${i}" data-tool-mode="${mode}">${esc(b.label)}</button>`).join('')}</div>
    ${TOOL_BUNDLES.map(b=>`<div class="small"><b>${esc(b.label)}:</b> <span class="mono">${esc(b.tools.join(', '))}</span></div>`).join('')}</div>`;
  const exact = Object.entries(groups).map(([source, tools]) => {
    const mcp = source.startsWith('mcp:');
    const label = mcp ? `MCP server · ${source.slice(4)}` : 'Built-in tools';
    return `<div style="margin:10px 0"><div class="kv">${esc(label)} · ${tools.length}</div>
      ${tools.map(tool => {
        const friendly = mcp && tool.name.includes('.') ? tool.name.split('.').slice(1).join('.') : tool.name;
        const click = mode === 'builder'
          ? 'toggleDraftTool(this.dataset.tool)' : "this.classList.toggle('on');markStepEditorDirty()";
        const safety = mcp ? badge('warn','human gate') : '';
        return `<div style="display:flex;gap:8px;align-items:center;margin-top:6px">
          <button class="tool-toggle ${selected.includes(tool.name) ? 'on' : ''}"
            onclick="${click}" data-tool="${esc(tool.name)}">${esc(friendly)}</button>
          ${safety}<span class="small dim">${esc(tool.description || '')}</span></div>`;
      }).join('')}</div>`;
  }).join('') || '<div class="small dim">No tools are loaded. Open Settings to connect or load a capability.</div>';
  return bundles + `<details open><summary>Advanced: choose exact tools</summary>${exact}</details>`;
}

document.addEventListener('click', e=>{
  const button=e.target.closest('[data-tool-bundle]'); if(!button) return;
  const bundle=TOOL_BUNDLES[+button.dataset.toolBundle]; if(!bundle) return;
  if(button.dataset.toolMode === 'builder'){
    const selected=wiz.builder.draft.tools, all=bundle.tools.every(t=>selected.includes(t));
    bundle.tools.forEach(t=>{ if(all){ const i=selected.indexOf(t); if(i>=0) selected.splice(i,1); } else if(!selected.includes(t)) selected.push(t); });
    if(selected.some(mcpToolNeedsGate)) wiz.builder.draft.hitl=true; markStepEditorDirty(); renderPlanStep();
  }else{
    const all=bundle.tools.every(t=>document.querySelector(`.step-edit [data-tool="${CSS.escape(t)}"]`)?.classList.contains('on'));
    bundle.tools.forEach(t=>document.querySelector(`.step-edit [data-tool="${CSS.escape(t)}"]`)?.classList.toggle('on',!all)); markStepEditorDirty();
  }
});

function mcpToolNeedsGate(name){
  const tool = (TOOLS_CATALOG || []).find(t => t.name === name);
  return !!tool && (tool.source || '').startsWith('mcp:');
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
  await Promise.all([loadToolNames(), loadAssignmentWorkers()]);
  if(wiz.builderMode){
    if(!wiz.builder) wiz.builder = {sub: 0, draft: newDraft()};
    return renderStepBuilder();
  }
  if(wiz.yamlMode) return renderYamlEditor();
  const p = wiz.plan;
  const identity = wiz.blueprintId
    ? `${badge('act', wiz.blueprintMode === 'draft' ? 'saved draft' : 'exact version')} <span class="mono small">${esc(wiz.blueprintId)}${wiz.blueprintVersion ? '@' + wiz.blueprintVersion : ''}</span>`
    : badge('dim','not saved');
  const saveActions = wiz.blueprintMode === 'draft'
    ? `<button class="btn ghost" onclick="saveHarnessDraft()">Save draft</button>
       <button class="btn ghost" onclick="publishHarness()">Publish</button>`
    : wiz.blueprintMode === 'run'
    ? `<button class="btn ghost" onclick="forkCurrentHarness()">Save as harness</button>`
    : `<button class="btn ghost" onclick="saveHarnessDraft()">Save as harness</button>`;
  document.getElementById('wiz-body').innerHTML = `
    <div class="guide"><div><b>Nothing has run yet — and every step is editable.</b>
      <p>✎ edits a step, ↑↓ reorder, ✕ removes, + adds one via the step wizard.
      Steps marked HITL pause for your approval. YAML mode has every advanced field.</p></div></div>
    <div class="card"><h2>${esc(p.name)}</h2><div class="sub">${planNote()} · ${identity}</div>
      <details class="field"><summary><b>Harness details and reusable inputs</b></summary>
        <div class="field"><label for="plan-harness-name">Name</label><input id="plan-harness-name" value="${esc(wiz.harnessName || wiz.blueprintContent?.name || p.name)}" oninput="wiz.harnessName=this.value;markPlanDirty()"></div>
        <div class="field"><label for="plan-harness-description">Description</label><input id="plan-harness-description" value="${esc(wiz.harnessDescription || wiz.blueprintContent?.description || '')}" oninput="wiz.harnessDescription=this.value;markPlanDirty()"></div>
        <div class="small dim">Input fields are configured on the Goal step; Advanced YAML remains available for schema-only features.</div></details>
      ${p.steps.map((st, i) => wiz.editingStep === i ? stepEditForm(st, i) : `
        <div class="planstep"><div class="n">${i + 1}</div>
          <div style="flex:1"><div class="pt">${esc(st.id)}
            ${badge('dim', st.task_type)}${st.hitl ? badge('warn', st.hitl_timing === 'after' ? 'HITL — approve output' : 'HITL — approve before run') : ''}
            ${st.success_check ? badge('ok','verifiable') : ''}${whenBadge(st)}
            ${(st.tools || []).map(t => badge('act','🔧 ' + t)).join('')}</div>
          <div class="pd">${esc(st.objective)}</div>
          ${(st.depends_on || []).length ? `<div class="pd mono">after: ${esc(st.depends_on.join(', '))}</div>` : ''}</div>
          <div class="step-actions">
            <button title="edit" aria-label="Edit stage ${esc(st.id)}" onclick="editStep(${i})">✎</button>
            <button title="move up" aria-label="Move stage ${esc(st.id)} up" onclick="moveStep(${i},-1)" ${i ? '' : 'disabled'}>↑</button>
            <button title="move down" aria-label="Move stage ${esc(st.id)} down" onclick="moveStep(${i},1)" ${i < p.steps.length - 1 ? '' : 'disabled'}>↓</button>
            <button title="remove" aria-label="Remove stage ${esc(st.id)}" onclick="deleteStep(${i})">✕</button></div></div>`).join('')}
      <div style="margin-top:14px;display:flex;gap:10px">
        <button class="btn ghost" onclick="openStepBuilder()">+ Add step (wizard)</button>
        <button class="btn ghost" onclick="openYaml()">Edit as YAML</button>
        ${saveActions}</div>
      <div class="small red" id="planmsg" style="margin-top:8px"></div></div>
    <div class="wiz-nav">
      <button class="btn ghost" onclick="setStep(1)">← ${wiz.blueprintMode === 'run' ? 'Change inputs' : 'Rephrase goal'}</button>
      <button class="btn" onclick="runValidatedPlan()" ${p.steps.length ? '' : 'disabled'}>${wiz.blueprintMode === 'run' && !wiz.dirty ? 'Confirm and run v' + wiz.blueprintVersion + ' →' : wiz.blueprintMode === 'draft' ? 'Run without saving →' : 'Run this plan →'}</button></div>`;
}

function contentForCurrentPlan(name){
  const base = wiz.blueprintContent || {};
  const needsGoal = JSON.stringify(wiz.plan).includes('$context.goal');
  const guided = (wiz.inputDefs || []).filter(i=>i.name).map(i=>({name:i.name,schema:{type:i.type},required:i.required,
    default:i.secret ? (i.default ? {binding:i.default} : null) : i.default === '' ? null : (['number','integer'].includes(i.type) ? Number(i.default) : i.type === 'boolean' ? i.default === 'true' : i.default),secret:i.secret}));
  const existingGoal=(base.inputs || []).find(i=>i.name==='goal');
  const inputs = [...(existingGoal ? [existingGoal] : needsGoal ? [{name:'goal',schema:{type:'string'},required:true,default:null,secret:false}] : []), ...guided];
  return {schema_version:1, name:name || wiz.harnessName || base.name || wiz.plan.name, description:wiz.harnessDescription || base.description || '',
    workflow:wiz.plan, inputs,
    default_context:base.default_context || {}, eval_suites:base.eval_suites || [], source:base.source || null};
}
async function saveHarnessDraft(){
  if(!syncOpenStepEditor()) return false;
  const valid = await post('/api/workflows/validate', {workflow:wiz.plan});
  if(!valid.ok){ toast('Fix the workflow: '+humanApiError(await valid.text())); return false; }
  wiz.plan = (await valid.json()).workflow;
  let id = wiz.blueprintId, name = wiz.blueprintContent?.name;
  if(!id){
    name = wiz.harnessName || wiz.plan.name.replace(/-/g,' ');
    id = wiz.harnessSlug || slugify(name);
    if(!name || !id){ toast('Add a harness name and id on the Goal step before saving'); return false; }
  }
  const content = contentForCurrentPlan(name);
  let r;
  if(wiz.blueprintMode === 'draft'){
    r = await libraryRequest(`/api/blueprint-drafts/${encodeURIComponent(id)}`, {method:'PATCH',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({content, expected_revision:wiz.blueprintRevision})}, 'Save draft');
  }else{
    r = await libraryRequest('/api/blueprint-drafts', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({blueprint_id:id, content})}, 'Save draft');
  }
  if(!r) return false;
  const draft = await r.json();
  wiz.blueprintMode='draft'; wiz.blueprintId=draft.id; wiz.blueprintVersion=draft.base_version;
  wiz.blueprintRevision=draft.revision; wiz.blueprintSource=draft.source;
  wiz.blueprintContent=blueprintContent(draft); wiz.blueprintOriginal=JSON.stringify(wiz.blueprintContent);
  wiz.dirty=false; toast('Draft saved');
  if(wiz.step === 2) renderPlanStep(); else if(wiz.step === 4) renderDoneStep();
  return true;
}
async function publishHarness(){
  if(!syncOpenStepEditor()) return;
  if(wiz.dirty && !(await saveHarnessDraft())) return;
  if(wiz.blueprintMode !== 'draft') return;
  const r = await libraryRequest(`/api/blueprint-drafts/${encodeURIComponent(wiz.blueprintId)}/publish`, {method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({expected_revision:wiz.blueprintRevision})}, 'Publish harness');
  if(!r) return;
  const version = await r.json();
  wiz.blueprintMode='run'; wiz.blueprintVersion=version.version; wiz.blueprintRevision=null;
  wiz.blueprintContent=blueprintContent(version); wiz.blueprintOriginal=JSON.stringify(wiz.blueprintContent);
  wiz.dirty=false; delete LIB.versions[wiz.blueprintId]; toast(`Published v${version.version}`);
  if(wiz.step === 2) renderPlanStep(); else if(wiz.step === 4) renderDoneStep();
}
async function forkCurrentHarness(){
  if(!wiz.blueprintId || !wiz.blueprintVersion) return;
  if(!syncOpenStepEditor()) return;
  await libraryFork(
    wiz.blueprintId,
    wiz.blueprintVersion,
    wiz.dirty ? contentForCurrentPlan() : null,
  );
}

/* -- inline step editor (works on ANY plan: LLM, template, custom) -- */
function stepEditForm(st, i){
  const check = checkOf(st);
  return `<div class="step-edit">
    <div class="field" style="display:flex;gap:10px">
      <span style="width:180px"><label for="se-id">Step id</label>
        <input id="se-id" class="mono" value="${esc(st.id)}"></span>
      <span style="flex:1"><label for="se-type">Task type</label><select id="se-type">
        ${TASK_TYPES.map(t => `<option ${st.task_type === t ? 'selected' : ''}>${t}</option>`).join('')}</select></span></div>
    <div class="field"><label for="se-obj">Objective — the full delegation contract for this step</label>
      <textarea id="se-obj">${esc(st.objective)}</textarea></div>
    ${inputMappingFields(st, 'se')}
    ${assignmentFields(st, 'se')}
    <fieldset class="field" style="border:0;padding:0"><legend>Tools this step may call</legend>
      ${toolPicker(st.tools || [], 'edit')}</fieldset>
    <div class="field" style="display:flex;gap:10px">
      <span style="width:160px"><label for="se-check">Success check</label><select id="se-check">
        ${['none','equals','contains','one_of'].map(k => `<option ${check.kind === k ? 'selected' : ''}>${k}</option>`).join('')}</select></span>
      <span style="flex:1"><label for="se-checkval">Check value (one_of: comma-separated)</label>
        <input id="se-checkval" class="mono" value="${esc(check.value)}"></span></div>
    <div class="field" style="display:flex;gap:10px">
      <span style="flex:1"><label for="se-deps">Depends on (comma-separated step ids)</label>
        <input id="se-deps" class="mono" value="${esc((st.depends_on || []).join(', '))}"></span>
      <span style="width:190px;align-self:end"><label style="display:flex;gap:8px;align-items:center">
        <input type="checkbox" id="se-hitl" ${st.hitl ? 'checked' : ''} style="width:auto"> HITL gate</label></span></div>
    <div class="field" style="display:flex;gap:10px">
      <span style="width:220px"><label for="se-when-step">Only run when (branch)</label><select id="se-when-step">
        <option value="">— always —</option>
        ${wiz.plan.steps.filter(o => o.id !== st.id).map(o =>
          `<option ${st.when && st.when.step === o.id ? 'selected' : ''}>${esc(o.id)}</option>`).join('')}</select></span>
      <span style="width:140px"><label for="se-when-kind">Condition</label><select id="se-when-kind">
        ${['equals','contains','one_of'].map(k => {
          const cur = st.when ? ['equals','contains','one_of'].find(x => x in st.when) : 'equals';
          return `<option ${cur === k ? 'selected' : ''}>${k}</option>`;}).join('')}</select></span>
      <span style="flex:1"><label for="se-when-val">Value (one_of: comma-separated)</label>
        <input id="se-when-val" class="mono" value="${esc(st.when ? (Array.isArray(st.when[['equals','contains','one_of'].find(x => x in st.when)]) ? st.when[['equals','contains','one_of'].find(x => x in st.when)].join(', ') : st.when[['equals','contains','one_of'].find(x => x in st.when)]) : '')}"></span></div>
    <div style="display:flex;gap:10px">
      <button class="btn" onclick="saveStep(${i})">Save step</button>
      <button class="btn ghost" onclick="cancelStepEdit()">Cancel</button></div></div>`;
}

function editStep(i){
  if(wiz.editingStep !== null && wiz.editingStep !== i && !syncOpenStepEditor()) return;
  wiz.editingStep = i; wiz.stepEditorDirty = false; wiz.stepEditorPriorDirty = wiz.dirty;
  renderPlanStep();
}
function cancelStepEdit(){
  wiz.editingStep = null; wiz.stepEditorDirty = false; wiz.dirty = wiz.stepEditorPriorDirty;
  wiz.stepEditorPriorDirty = false; renderPlanStep();
}

function collectStepForm(){
  const tools = [...document.querySelectorAll('.step-edit .tool-toggle.on')].map(b => b.dataset.tool);
  return {
    id: document.getElementById('se-id').value.trim(),
    task_type: document.getElementById('se-type').value,
    objective: document.getElementById('se-obj').value.trim(),
    inputs: collectMappedInputs('se', wiz.plan.steps[wiz.editingStep]?.inputs || {}),
    role: document.getElementById('se-role').value.trim() || null,
    required_capabilities: document.getElementById('se-capabilities').value.split(',').map(x=>x.trim()).filter(Boolean),
    worker_id: document.getElementById('se-worker').value || null,
    tier_hint: document.getElementById('se-worker').value ? null : (document.getElementById('se-tier').value || null),
    tools,
    success_check: buildCheck(document.getElementById('se-check').value,
                              document.getElementById('se-checkval').value),
    depends_on: document.getElementById('se-deps').value.split(',').map(x => x.trim()).filter(Boolean),
    hitl: document.getElementById('se-hitl').checked || tools.some(mcpToolNeedsGate),
    when: buildWhen(document.getElementById('se-when-step').value,
                    document.getElementById('se-when-kind').value,
                    document.getElementById('se-when-val').value),
  };
}

function applyOpenStepForm(){
  if(wiz.yamlMode){ toast('Apply or cancel YAML changes before continuing'); return false; }
  if(wiz.builderMode){
    if(wiz.stepEditorDirty) toast('Finish or cancel the open step before continuing');
    return !wiz.stepEditorDirty;
  }
  if(wiz.editingStep === null) return true;
  if(!wiz.stepEditorDirty){
    wiz.editingStep = null;
    wiz.dirty = wiz.stepEditorPriorDirty;
    wiz.stepEditorPriorDirty = false;
    return true;
  }
  const i = wiz.editingStep;
  const edit = collectStepForm();
  if(!edit.id){ toast('the open step needs an id before continuing'); return false; }
  if(!edit.objective){ toast('the open step needs an objective before continuing'); return false; }
  if(wiz.plan.steps.some((st, j) => j !== i && st.id === edit.id)){
    toast(`step id ${edit.id} is already used`); return false; }
  wiz.plan.steps[i] = Object.assign({}, wiz.plan.steps[i], edit);
  wiz.editingStep = null; wiz.stepEditorDirty = false; markPlanDirty();
  wiz.stepEditorPriorDirty = false;
  return true;
}

function syncOpenStepEditor(){ return applyOpenStepForm(); }

function saveStep(i){
  if(wiz.editingStep !== i || !applyOpenStepForm()) return;
  renderPlanStep();
}

function deleteStep(i){
  if(!syncOpenStepEditor()) return;
  const removed = wiz.plan.steps.splice(i, 1)[0];
  wiz.plan.steps.forEach(st => {
    st.depends_on = (st.depends_on || []).filter(d => d !== removed.id); });
  wiz.editingStep = null; markPlanDirty();
  renderPlanStep();
}

function moveStep(i, delta){
  if(!syncOpenStepEditor()) return;
  const j = i + delta;
  if(j < 0 || j >= wiz.plan.steps.length) return;
  [wiz.plan.steps[i], wiz.plan.steps[j]] = [wiz.plan.steps[j], wiz.plan.steps[i]];
  markPlanDirty();
  renderPlanStep();
}

async function runValidatedPlan(){
  if(!syncOpenStepEditor()) return;
  const msg = document.getElementById('planmsg');
  msg.textContent = '';
  const r = await post('/api/workflows/validate', {workflow: wiz.plan});
  if(!r.ok){
    msg.textContent = 'invalid workflow: ' + JSON.parse(await r.text()).detail;
    return;
  }
  wiz.plan = (await r.json()).workflow;  // normalized
  if(wiz.blueprintMode === 'run' && !wiz.dirty){
    const context = Object.assign({}, wiz.context, wiz.goal ? {goal:wiz.goal} : {});
    const ready = await post('/api/blueprints/readiness', {
      blueprint:{id:wiz.blueprintId,version:wiz.blueprintVersion}, context});
    if(!ready.ok){ msg.textContent = 'readiness check failed'; return; }
    const report = await ready.json();
    if(!report.ready){
      wiz.readinessIssues=report.issues; msg.innerHTML = readinessIssueHtml(report.issues); return;
    }
  }
  startRun();
}
function readinessIssueHtml(issues){ return `<div class="readiness-list" role="alert"><b>This harness needs attention before it can run.</b>${issues.map((i,n)=>
  `<div class="hint-panel"><b>${esc(i.stage_id || i.input_name || 'Harness')}</b> — ${esc(i.message)}<div><button class="pill" onclick="repairReadiness(${n})">${esc(i.repair?.label || 'Repair')}</button></div></div>`).join('')}</div>`; }
async function repairReadiness(index){
  const issue=wiz.readinessIssues[index]; if(!issue) return;
  const action=issue.repair?.action, target=issue.repair?.target;
  if(action === 'load_mcp' && target){
    const r=await post('/api/config/mcp/'+encodeURIComponent(target)+'/load',{});
    if(!r.ok){ toast('Could not load '+target+': '+humanApiError(await r.text())); return; }
    const report=await r.json();
    if(!report.ok){
      const detail=report.detail || report.status || 'connection failed';
      issue.message=`${target} could not load: ${detail}`;
      toast(`Could not load ${target}: ${detail}`);
      const msg=document.getElementById('planmsg'); if(msg) msg.innerHTML=readinessIssueHtml(wiz.readinessIssues);
      return;
    }
    TOOLS_NAMES=null; TOOLS_CATALOG=null; toast('Capability loaded — checking readiness again'); await renderPlanStep(); runValidatedPlan(); return;
  }
  if(['choose_worker','choose_tool'].includes(action)){
    if(wiz.blueprintMode === 'run' && !wiz.dirty){ toast('Fork this exact version to change its stage settings'); return forkCurrentHarness(); }
    const idx=wiz.plan.steps.findIndex(s=>s.id===issue.stage_id); if(idx>=0){ editStep(idx); return; }
  }
  wiz.repairReturn=true; showView('settings', true);
}
function returnToHarnessRepair(){ currentView='settings'; showView('wizard',true); wiz.repairReturn=false; setStep(2); }

/* -- step builder: wizard-driven custom workflow authoring -- */
function newDraft(){
  return {id: `step-${wiz.plan.steps.length + 1}`, task_type: 'general', objective: '',
          inputs: {goal:'$context.goal'}, tools: [], hitl: false, depends_on: [], check_kind: 'none', check_value: '',
          when_step: '', when_kind: 'equals', when_value: '', role:'', required_capabilities:[], worker_id:'', tier_hint:''};
}

function assignmentFields(value, prefix){
  const selected=value.worker_id || '';
  return `<fieldset class="field"><legend>Who should handle this stage?</legend>
    <div class="field" style="display:flex;gap:10px"><span style="flex:1"><label for="${prefix}-worker">Assignment</label><select id="${prefix}-worker">
      <option value="">Automatic (recommended)</option>${ASSIGNMENT_WORKERS.map(w=>`<option value="${esc(w.worker_id)}" ${selected===w.worker_id?'selected':''}>Require ${esc(w.display_name || w.worker_id)} · ${esc(w.tier)}</option>`).join('')}</select></span>
      <span style="width:180px"><label for="${prefix}-tier">Minimum level</label><select id="${prefix}-tier" ${selected?'disabled':''}><option value="">Automatic</option>
        <option value="small" ${value.tier_hint==='small'?'selected':''}>Quick</option><option value="mid" ${value.tier_hint==='mid'?'selected':''}>Balanced</option><option value="frontier" ${value.tier_hint==='frontier'?'selected':''}>Most capable</option></select></span></div>
    <div class="field" style="display:flex;gap:10px"><span style="flex:1"><label for="${prefix}-role">Role (optional)</label><input id="${prefix}-role" value="${esc(value.role || '')}" placeholder="implementer"></span>
      <span style="flex:2"><label for="${prefix}-capabilities">Agent capabilities (comma-separated)</label><input id="${prefix}-capabilities" value="${esc((value.required_capabilities || []).join(', '))}" placeholder="workspace.write, tests.run"></span></div>
    <div class="small dim">Tool permissions below remain separate and least-privilege.</div></fieldset>`;
}

function openStepBuilder(){
  if(!syncOpenStepEditor()) return;
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
      <div class="field"><label for="sb-id">Step id</label>
        <input id="sb-id" class="mono" value="${esc(d.id)}"></div>
      <div class="field"><label for="sb-obj">Objective</label>
        <textarea id="sb-obj" placeholder="e.g. Classify the ticket severity as exactly one of: low, high.">${esc(d.objective)}</textarea></div>
      ${inputMappingFields(d, 'sb')}`;
  }else if(b.sub === 1){
    inner = `
      <fieldset class="field" style="border:0;padding:0"><legend>Task type — routes the step to the right tier</legend>
        <div class="pillrow">${TASK_TYPES.map(t =>
          `<button class="pill ${d.task_type === t ? 'on' : ''}" onclick="wiz.builder.draft.task_type='${t}';markStepEditorDirty();renderPlanStep()">${t}</button>`).join('')}</div></fieldset>
      ${assignmentFields(d, 'sb')}
      <fieldset class="field" style="border:0;padding:0"><legend>Tools (only what this step truly needs — fewer is better)</legend>
        ${toolPicker(d.tools, 'builder')}</fieldset>`;
  }else{
    const prior = wiz.plan.steps.map(st => st.id);
    inner = `
      <div class="field" style="display:flex;gap:10px">
        <span style="width:160px"><label for="sb-check">Success check</label><select id="sb-check">
          ${['none','equals','contains','one_of'].map(k => `<option ${d.check_kind === k ? 'selected' : ''}>${k}</option>`).join('')}</select></span>
        <span style="flex:1"><label for="sb-checkval">Check value (one_of: comma-separated)</label>
          <input id="sb-checkval" class="mono" value="${esc(d.check_value)}"></span></div>
      <div class="field"><label>Runs after (dependencies)</label>
        ${prior.length ? `<div class="pillrow">${prior.map(id =>
          `<button class="pill ${d.depends_on.includes(id) ? 'on' : ''}" onclick="toggleDraftDep('${esc(id)}')">${esc(id)}</button>`).join('')}</div>`
          : '<span class="small dim">first step — nothing to depend on yet</span>'}</div>
      <div class="field"><label style="display:flex;gap:8px;align-items:center">
        <input type="checkbox" id="sb-hitl" ${d.hitl ? 'checked' : ''} style="width:auto">
        HITL gate — pause for my approval before this step runs</label></div>
      ${prior.length ? `<div class="field" style="display:flex;gap:10px">
        <span style="width:200px"><label for="sb-when-step">Only run when (branch)</label><select id="sb-when-step">
          <option value="">— always —</option>
          ${prior.map(id => `<option ${d.when_step === id ? 'selected' : ''}>${esc(id)}</option>`).join('')}</select></span>
        <span style="width:130px"><label for="sb-when-kind">Condition</label><select id="sb-when-kind">
          ${['equals','contains','one_of'].map(k => `<option ${d.when_kind === k ? 'selected' : ''}>${k}</option>`).join('')}</select></span>
        <span style="flex:1"><label for="sb-when-val">Value</label>
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
  if(tools.includes(t) && mcpToolNeedsGate(t)) wiz.builder.draft.hitl = true;
  markStepEditorDirty();
  renderPlanStep();
}

function toggleDraftDep(id){
  const deps = wiz.builder.draft.depends_on;
  deps.includes(id) ? deps.splice(deps.indexOf(id), 1) : deps.push(id);
  markStepEditorDirty();
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
  const worker=grab('sb-worker'); if(worker !== null) d.worker_id=worker;
  const tier=grab('sb-tier'); if(tier !== null) d.tier_hint=worker ? '' : tier;
  const role=grab('sb-role'); if(role !== null) d.role=role.trim();
  const caps=grab('sb-capabilities'); if(caps !== null) d.required_capabilities=caps.split(',').map(x=>x.trim()).filter(Boolean);
  if(document.querySelector('[data-map-prefix="sb"][data-map-source]')) d.inputs=collectMappedInputs('sb',d.inputs);
}

function builderNav(delta){
  builderCapture();
  const b = wiz.builder;
  if(b.sub === 0 && delta < 0){
    wiz.builderMode = false;
    wiz.stepEditorDirty = false;
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
  if(d.tools.some(mcpToolNeedsGate)){ d.hitl = true; d.hitl_timing = 'before'; }
  if(wiz.plan.steps.some(st => st.id === d.id)){ toast(`step id ${d.id} is already used`); return; }
  wiz.plan.steps.push({id: d.id, task_type: d.task_type, objective: d.objective,
    inputs: {...d.inputs}, boundaries: [], tools: d.tools.slice(),
    depends_on: d.depends_on.slice(), hitl: d.hitl,
    role:d.role || null, required_capabilities:d.required_capabilities.slice(), worker_id:d.worker_id || null, tier_hint:d.worker_id ? null : (d.tier_hint || null),
    success_check: buildCheck(d.check_kind, d.check_value),
    when: buildWhen(d.when_step, d.when_kind, d.when_value)});
  markPlanDirty();
  wiz.stepEditorDirty = false;
  toast(`Added ${d.id}`);
  wiz.builder = {sub: 0, draft: newDraft()};
  renderPlanStep();
}

/* -- YAML power mode -- */
async function openYaml(){
  if(!syncOpenStepEditor()) return;
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
  wiz.yamlMode = false; wiz.stepEditorDirty = false; markPlanDirty();
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
        <button class="btn ghost" onclick="wiz.yamlMode=false;wiz.stepEditorDirty=false;renderPlanStep()">Cancel</button>
        <button class="btn" onclick="applyYaml()">Apply YAML</button></div></div>`;
}

async function startRun(){
  const context = Object.assign({}, wiz.context, wiz.goal ? {goal:wiz.goal} : {});
  const body = wiz.blueprintMode === 'run' && !wiz.dirty
    ? {blueprint:{id:wiz.blueprintId,version:wiz.blueprintVersion}, context, wait:false}
    : {workflow:wiz.plan, context, wait:false};
  const r = await post('/api/runs', body);
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
  if(run.failed_step === s.id) return {cls:'fail', icon:'✕', label:badge('bad','failed')};
  if(run.awaiting === s.id) return {cls:'now', icon:'…', label:badge('warn','waiting for you')};
  if(run.completed[s.id]) return {cls:'done', icon:'✓', label:badge(run.completed[s.id].verdict === 'pass' ? 'ok' : 'dim', run.completed[s.id].verdict)};
  const started = (wiz.journal || []).some(e => e.kind === 'step.started' && e.step_id === s.id);
  if(started && run.status === 'running') return {cls:'now', icon:'', label:'<span class="spin"></span> <span class="small dim">running…</span>'};
  return {cls:'', icon:'', label:badge('dim','queued')};
}

function attemptRows(stepId){
  // per-attempt verdicts + verifier reasons from the run journal — the
  // "why did this step fail 3 times" panel
  const atts = (wiz.journal || []).filter(e => ['verification.completed','step.attempt'].includes(e.kind) && e.step_id === stepId);
  if(!atts.length) return '';
  return `<div class="attempts">${atts.map(e => {
    const p = e.payload || {};
    return `<div class="att ${esc(p.verdict)}"><b>#${p.n} ${esc(p.verdict)}</b> · ${esc(p.model)}${p.scorer ? ' · ' + esc(p.scorer) : ''}${p.detail ? ' — ' + esc(p.detail) : ''}</div>`;
  }).join('')}</div>`;
}
function stageTimeline(stepId){
  const events=(wiz.journal || []).filter(e=>e.step_id===stepId && ['step.ready','attempt.assigned','attempt.started','tool.requested','tool.completed','verification.started','verification.completed','approval.required','approval.resolved','step.completed','step.failed','step.skipped'].includes(e.kind));
  if(!events.length) return '<div class="small dim">Waiting for dependencies and readiness checks.</div>';
  let previousTier=null;
  return `<ol class="timeline" aria-label="Live stage progress">${events.map(e=>{
    const p=e.payload||{}; let text=e.kind.replaceAll('.',' ');
    if(e.kind==='attempt.assigned'){
      const escalated=previousTier && previousTier!==p.tier; previousTier=p.tier;
      text=`${escalated?'Escalated and assigned':'Assigned'} attempt ${p.n} to ${p.worker_id} · ${p.model} · ${p.tier}`;
    }else if(e.kind==='attempt.started') text=`Agent ${p.worker_id} started attempt ${p.n}`;
    else if(e.kind==='tool.requested') text=`Using ${p.tool || 'a capability'}`;
    else if(e.kind==='tool.completed') text=`${p.tool || 'Capability'} ${p.status || 'completed'}`;
    else if(e.kind==='verification.started') text=`Verifying ${p.worker_id || 'agent'} output`;
    else if(e.kind==='verification.completed') text=`Verification ${p.verdict || 'completed'}${p.scorer?' · '+p.scorer:''}`;
    else if(e.kind==='step.ready') text='Dependencies satisfied; stage ready';
    return `<li><span class="small">${esc(text)}</span></li>`;
  }).join('')}</ol>`;
}

function stepInputRefs(value, out = new Set()){
  if(Array.isArray(value)){ value.forEach(v => stepInputRefs(v, out)); return out; }
  if(value && typeof value === 'object'){ Object.values(value).forEach(v => stepInputRefs(v, out)); return out; }
  if(typeof value !== 'string') return out;
  for(const m of value.matchAll(/\\$steps\\.([^.]+)\\.output/g)) out.add(m[1]);
  return out;
}

function upstreamEvidence(s){
  const run = wiz.run || {completed: {}};
  const completed = run.completed || {};
  const explicit = new Set([...(s.depends_on || []), ...stepInputRefs(s.inputs || {})]);
  let ids = [...explicit].filter(id => completed[id]);
  if(!ids.length && !completed[s.id]){
    const prior = [];
    for(const step of (wiz.plan?.steps || [])){
      if(step.id === s.id) break;
      if(completed[step.id]) prior.push(step.id);
    }
    ids = prior;
  }
  ids = [...new Set(ids)];
  if(!ids.length) return '';
  return `<div class="evidence-panel"><h3>Evidence available for ${esc(s.id)}</h3>
    <div class="small dim">Review these completed upstream outputs before approving or judging this stage.</div>
    ${ids.map(id => {
      const rec = completed[id];
      return `<details class="evidence-item" open><summary>${esc(stepName(id))} ${verdictBadge(rec.verdict)}</summary>
        ${humanizeOutput(rec.output)}
        ${attemptRows(id)}</details>`;
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
  return `<div class="steptabs" role="tablist" aria-label="Harness stages">` + wiz.plan.steps.map((s, i) => {
    const st = stepStatus(s);
    return `<button role="tab" aria-selected="${s.id===sel}" aria-controls="stage-panel" class="stab ${s.id === sel ? 'on' : ''}" data-step-id="${esc(s.id)}">
      <span class="ticon ${st.cls}">${st.icon || i + 1}</span>${esc(s.id)}</button>`;
  }).join('') + `</div>`;
}

function stepPanel(s){
  const run = wiz.run || {completed: {}};
  const st = stepStatus(s);
  const rec = (run.completed || {})[s.id];
  return `<div class="steppanel" id="stage-panel" role="tabpanel">
    <div class="pt">${esc(s.id)} ${badge('dim', s.task_type)} ${st.label}</div>
    <div class="pd">${esc(s.objective)}</div>
    ${rec ? humanizeOutput(rec.output) : ''}
    ${upstreamEvidence(s)}
    ${stageTimeline(s.id)}
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
  const gateStep = p.steps.find(s => s.id === run.awaiting);
  const gateMessage = gateStep && gateStep.hitl_timing === 'after'
    ? 'Review the completed artifact below. Approve to continue; reject to stop downstream work.'
    : 'This step is gated — it runs only if you approve it.';
  const hitl = run.status === 'awaiting_approval'
    ? `<div class="guide"><div><b>Approval needed: ${esc(run.awaiting)}</b>
        <p>${gateMessage}</p>
        <div style="margin-top:10px;display:flex;gap:10px">
          <button class="btn" onclick="resolveHitl(true)">Approve ${esc(run.awaiting)}</button>
          <button class="btn reject" onclick="resolveHitl(false)">Reject</button></div></div></div>`
    : '';
  const selected = p.steps.find(s => s.id === activeStepId()) || p.steps[0];
  document.getElementById('wiz-body').innerHTML = hitl + `
    <div class="card" aria-live="polite" aria-atomic="false"><h2>${esc(p.name)}</h2>
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
    <div class="card" style="margin-top:16px"><h2>Reuse this harness</h2>
      <div class="sub">Save it once, edit it, or run the same stages with new inputs.</div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        ${wiz.blueprintMode === 'draft' ? '<button class="btn ghost" onclick="saveHarnessDraft()">Save draft</button><button class="btn ghost" onclick="publishHarness()">Publish</button>'
          : wiz.blueprintMode === 'run' ? '<button class="btn ghost" onclick="forkCurrentHarness()">Save as harness</button>'
          : '<button class="btn ghost" onclick="saveHarnessDraft()">Save as harness</button>'}
        <button class="btn ghost" onclick="editAfterRun()">Edit stages</button>
        <button class="btn ghost" onclick="runWithNewInputs()">Run with new inputs</button></div></div>
    <div class="wiz-nav">
      <button class="btn ghost" onclick="showView('console')">Inspect in Console</button>
      <a class="btn ghost" href="/api/runs/${esc(wiz.runId)}/package" download>⬇ Download run package</a>
      <button class="btn" onclick="resetWizard()">Start another run →</button></div>`;
}

function editAfterRun(){
  wiz.runId=null; wiz.run=null;
  if(wiz.blueprintMode === 'run') return libraryEdit(wiz.blueprintId, wiz.blueprintVersion, wiz.blueprintOrigin || 'owned');
  wiz.dirty = wiz.blueprintMode !== 'draft'; setStep(2);
}
function runWithNewInputs(){
  wiz.runId=null; wiz.run=null; wiz.goal=''; wiz.context={};
  wiz.rerunInputs=true; setStep(1);
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
  wiz.context = data.context || {};
  wiz.goal = wiz.context.goal || wiz.goal || '';
  wiz.planSource = data.plan_source;
  wiz.fallbackReason = data.fallback_reason || '';
  wiz.edited = false; wiz.editingStep = null;
  wiz.dirty = true;
  wiz.builderMode = false; wiz.yamlMode = false;
  toast('Follow-up plan ready — review and approve before it runs');
  setStep(2);
}

function resetWizard(render=true, force=false){
  if(!force && wiz.step === 2 && !syncOpenStepEditor()) return false;
  if(!force && wiz.dirty && !confirm('Discard unsaved harness changes and start over?')) return false;
  wiz.goal = ''; wiz.context = {}; wiz.plan = null; wiz.runId = null; wiz.run = null;
  wiz.editingStep = null; wiz.builderMode = false; wiz.builder = null;
  wiz.yamlMode = false; wiz.edited = false;
  wiz.pinnedStep = null; wiz.fallbackReason = '';
  resetBlueprintState();
  if(wiz.poller){ clearInterval(wiz.poller); wiz.poller = null; }
  if(render) setStep(1);
  return true;
}
function startFreshHarness(){ if(resetWizard(true)) showView('wizard', true); }

/* ---------- console view ---------- */
const openRuns = new Set();
let showArchivedRuns = false;
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
  if(!runs.length) return showArchivedRuns
    ? '<div class="empty">no runs to show — archived runs will appear here after you archive a finished run</div>'
    : '<div class="empty">no active runs — start one from the Run tab, or show archived runs</div>';
  return paginate('runs', runs.slice().reverse(), rows => rows.map(r => {
    const open = openRuns.has(r.run_id);
    const kind = runKind(r);
    const hitl = r.status === 'awaiting_approval'
      ? `<button class="btn" data-approve="1" data-run="${esc(r.run_id)}" data-step="${esc(r.awaiting)}">Approve</button>
         <button class="btn reject" data-approve="0" data-run="${esc(r.run_id)}" data-step="${esc(r.awaiting)}">Reject</button>`
      : '';
    const archiveAction = r.archived_at
      ? `<button class="pill" data-run-action="restore" data-run="${esc(r.run_id)}">Restore</button>`
      : ['completed','failed'].includes(r.status)
        ? `<button class="pill" data-run-action="archive" data-run="${esc(r.run_id)}">Archive</button>` : '';
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
      ${r.archived_at ? badge('dim','archived') : ''}${statusBadge(r.status)}${hitl}${archiveAction}</div>${detail}`;
  }).join(''), {timeOrdered: true});
}

/* Delegated clicks: step ids are user-authored, so they ride in data-* attributes
   (HTML-escaped) instead of being spliced into inline JS strings. */
document.getElementById('runs').addEventListener('click', ev => {
  const action = ev.target.closest('button[data-run-action]');
  if(action){ setRunArchived(action.dataset.run, action.dataset.runAction === 'archive'); return; }
  const b = ev.target.closest('button[data-approve]');
  if(b){ resolveApproval(b.dataset.run, b.dataset.step, b.dataset.approve === '1'); return; }
  const row = ev.target.closest('.runrow');
  if(row) toggleRun(row.dataset.run);
});
document.getElementById('runs-show-archived').addEventListener('change', ev => {
  showArchivedRuns = ev.target.checked; pages.runs = 0; refreshConsole();
});

async function setRunArchived(runId, archived){
  const action=archived ? 'archive' : 'restore';
  const r=await post(`/api/runs/${encodeURIComponent(runId)}/${action}`,{});
  if(!r.ok){ toast(`${archived ? 'Archive' : 'Restore'} failed: ${humanApiError(await r.text())}`); return; }
  openRuns.delete(runId);
  toast(archived ? 'Run archived — its outputs and package are still available' : 'Run restored to the active list');
  refreshConsole();
}

/* Pager buttons, anywhere in the console */
document.getElementById('view-console').addEventListener('click', ev => {
  const b = ev.target.closest('button[data-page]');
  if(!b) return;
  const cut = b.dataset.page.lastIndexOf(':');   // keys may contain ':'
  const key = b.dataset.page.slice(0, cut), delta = b.dataset.page.slice(cut + 1);
  pages[key] = Math.max(0, (pages[key] || 0) + Number(delta));
  refreshConsole();
});

/* Flatten /api/routing into per-worker and per-model lookups for the console. */
function routingIndex(routing){
  const byWorker = {}, byModel = {};
  Object.entries(routing || {}).forEach(([tier, pool]) => {
    const routed = (pool && pool.routed) || {};
    ((pool && pool.members) || []).forEach(m => {
      const n = routed[m.worker_id] || 0;
      byWorker[m.worker_id] = {tier, routed: n};
      const bm = byModel[m.model] || (byModel[m.model] = {tier, routed: 0});
      bm.routed += n;
    });
  });
  return {byWorker, byModel};
}

function renderWorkers(ws, routing){
  if(!ws.length) return '<div class="empty">no agents yet — add one in Settings</div>';
  const rIdx = routingIndex(routing);
  return paginate('workers', ws, rows => rows.map(w => {
    const rr = rIdx.byWorker[w.worker_id];
    return `
    <div class="lrow">
      <div class="rr-main">
        <div class="rr-title" style="white-space:normal">${esc(w.display_name)}
          ${(w.tiers||[]).map(t => badge('act', t + ' tier')).join(' ')}${
          rr && rr.routed ? ' ' + badge('dim', 'routed ' + rr.routed + '×') : ''}</div>
        <div class="rr-meta">${esc(w.worker_id)} · identity key ${esc(w.public_key_b64.slice(0,10))}…${
          w.key_rotations ? ` · key rotated ×${w.key_rotations}` : ''}</div></div>
      ${badge(w.active ? 'ok' : 'dim', w.active ? 'ready' : 'retired')}</div>`;
  }).join(''));
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

function renderMatrix(m, routing){
  const panel = CARD_ADVICE.routing ? renderCardAdvice('routing', routingFacts(routing)) : '';
  const models = Object.keys(m);
  if(!models.length) return panel + '<div class="empty">nothing observed yet — the harness learns who’s good at what as runs finish</div>';
  const rIdx = routingIndex(routing);
  return panel + paginate('matrix', models, ms => ms.map(model => {
    const rows = Object.entries(m[model]).map(([t, c]) =>
      `<tr><td class="small">${esc(taskPlain(t))}</td>
       <td><span class="bar-h" style="width:${Math.round(c.pass_rate*110)}px"></span>
       <span class="mono small">${(c.pass_rate*100).toFixed(0)}%</span></td>
       <td class="small faint">${c.samples === 1 ? 'from 1 try' : `from ${c.samples} tries`}</td></tr>`).join('');
    const rm = rIdx.byModel[model];
    const pool = rm ? `<span class="faint small" style="margin-left:8px">${esc(rm.tier)} pool${
      rm.routed ? ' · routed ' + rm.routed + '×' : ''}</span>` : '';
    return `<div class="small" style="margin:8px 0 4px"><b>${esc(model)}</b>${pool}</div><table>${rows}</table>`;
  }).join(''), {size: 3});
}

function renderPlaybook(bullets){
  const panel = CARD_ADVICE.playbook ? renderCardAdvice('playbook', playbookFacts(bullets)) : '';
  if(!bullets.length) return panel + '<div class="empty">no lessons yet — the harness writes them as failure patterns emerge</div>';
  return panel + paginate('playbook', bullets, bs =>
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
  timeout: 'ran out of time before finishing',
  unknown: 'cause not identified',
};
const mastPlain = m => MAST_PLAIN[m] || taskPlain(m);

function renderFailures(f){
  const panel = CARD_ADVICE.failures ? renderCardAdvice('failures', failuresFacts(f)) : '';
  const rows = Object.keys(f).flatMap(t =>
    Object.entries(f[t]).map(([mode, n]) => ({t, mode, n})));
  if(!rows.length) return panel + '<div class="empty">no failures yet — when one happens it lands here, labelled</div>';
  return panel + paginate('failures', rows, rs =>
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
const TUNE = { suite: 'mixed', proposer: 'rule' };
const PROPOSERS = ['rule', 'llm', 'code'];
/* The client-known suites start_tune/add_coverage may legally target — the tuning
   dropdown offers these, and card-level advice buttons validate against them.
   Review K-C1: the list is the four always-startable built-in generators UNIONED
   with every suite /api/optimization reports from disk (refreshConsole keeps it
   fresh on the 3s loop), so a suite created on disk becomes advisable without a
   frontend release. */
const TUNE_DEFAULT_SUITES = ['mixed', 'classify', 'extract', 'math'];
let TUNE_SUITES = TUNE_DEFAULT_SUITES;
const validSuite = s => TUNE_SUITES.includes(s);

/* One plain-language sentence wrapping up a suite's last search — shared by
   the tuning card and the Home landing. */
function tuningSummary(s){
  if(!s.report || s.running) return '';
  const rep = s.report;
  const experiments = s.candidates.filter(c => c.status === 'evaluated').length;
  const outcome = s.pending ? `${s.pending.candidate} is waiting for your decision`
    : rep.promoted ? `${rep.best_id} won and was promoted`
    : rep.stopped === 'error' ? 'the search crashed — see the note in the Console'
    : 'nothing beat the current setup';
  const g = rep.gate;
  return `Last ${s.suite} search${rep.target_model ? ' on ' + rep.target_model : ''}${
      rep.finished_at ? ' finished ' + ago(rep.finished_at) : ''}: `
    + `${experiments} experiment${experiments === 1 ? '' : 's'} over ${rep.rounds_run} round${rep.rounds_run === 1 ? '' : 's'} — ${outcome}`
    + (g ? ` (held-out ${g.overall_incumbent.toFixed(2)} → ${g.overall_candidate.toFixed(2)})` : '') + '.';
}

function renderTuning(suites){
  const busy = suites.some(s => s.running);
  const controls = `<div class="chainline">
    <select id="tune-suite" style="border:1px solid var(--line2);border-radius:999px;
      padding:5px 10px;font-family:inherit;font-size:12.5px;background:var(--card);color:var(--ink)">
      ${TUNE_SUITES.map(n =>
        `<option value="${n}" ${TUNE.suite === n ? 'selected' : ''}>${n} suite</option>`).join('')}</select>
    <select id="tune-proposer" style="border:1px solid var(--line2);border-radius:999px;
      padding:5px 10px;font-family:inherit;font-size:12.5px;background:var(--card);color:var(--ink)">
      <option value="rule" ${TUNE.proposer === 'rule' ? 'selected' : ''}>built-in ideas</option>
      <option value="llm" ${TUNE.proposer === 'llm' ? 'selected' : ''}>✦ frontier agent reads the traces</option>
      <option value="code" ${TUNE.proposer === 'code' ? 'selected' : ''}>✦ coding agent writes harness code</option>
    </select>
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
        c.code_ref ? badge('act', 'code') : '',
        c.status === 'rejected' ? badge('bad', 'rejected')
          : c.frontier ? '<span title="on the pass-vs-cost frontier">⭐</span>'
          : badge('dim', 'not worth it'),
      ].filter(Boolean).join(' ');
      const meta = (c.scores
        ? `pass^${c.scores.k} ${c.scores.pass_hat_k.toFixed(2)} · pass@1 ${c.scores.pass_at_1.toFixed(2)}`
          + ` · ${c.scores.tokens_total.toLocaleString()} tokens${c.parent ? ' · builds on ' + esc(c.parent) : ''}`
        : 'never evaluated')
        + (c.code_ref ? ' · code <span class="mono">' + esc(c.code_ref) + '</span>' : '')
        + (c.created_at ? ' · ' + esc(ago(c.created_at)) : '');
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
    const summaryText = tuningSummary(s);
    const summary = summaryText
      ? `<div class="small dim" style="margin:2px 0 8px">${esc(summaryText)}</div>` : '';
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
        .map(x => `<button class="btn small" data-advise-act="${esc(x.action)}"
          data-suite="${esc(key.split('/')[0])}">${esc(x.label)}</button>`).join('')}</div>`;
  return `<div class="rr-detail"><div class="advisor">${facts}<div class="takes">
    <div class="h">Advisor’s read <span class="ai-chip"><svg style="width:11px;height:11px"><use href="#sparkle"/></svg>AI companion — advisory, not verified</span></div>
    ${body}</div></div></div>`;
}

/* Card-level advisor panels (routing / failures / playbook): same explicit-click,
   render-from-cache mechanism as the tuning rows, but one panel per card keyed by
   page. Verified facts come from data already on the 3s loop; the read + actions
   from the cached /api/advise response. */
const CARD_ADVICE = {};
/* page -> the console body div whose render function re-prepends the panel */
const CARD_ADVICE_TARGET = { routing: 'matrix', failures: 'failures', playbook: 'playbook' };

/* review G-FU2: data-params is markup and can be malformed — a broken attribute
   must degrade to empty params, never kill the click handler with a throw. */
const btnParams = el => { try{ return JSON.parse(el.dataset.params || '{}') || {}; }catch(e){ return {}; } };

/* Next-action buttons, shared by every advisor panel. Each button carries its
   params as JSON; suite-targeting actions with an absent/invalid suite are dropped
   here (render guard — defense in depth alongside the handler guard). When the
   facts have drifted since the advice was fetched (review F-C), mutating actions
   are dropped too — navigation actions may stay. */
function adviceActions(next_actions, dropMutating){
  const btns = (next_actions || []).filter(x => x.action !== 'none').map(x => {
    const params = x.params || {};
    if((x.action === 'start_tune' || x.action === 'add_coverage')
       && (dropMutating || !validSuite(params.suite))) return '';
    return `<button class="btn small" data-advise-act="${esc(x.action)}" data-params="${esc(JSON.stringify(params))}">${esc(x.label)}</button>`;
  }).filter(Boolean).join('');
  return `<div class="nba">${btns}</div>`;
}

function renderCardAdvice(page, factsHtml){
  const a = CARD_ADVICE[page];
  if(!a) return '';
  // review F-C: the advice is only as fresh as the facts it was computed from.
  // The first render (the loading tick, right after the sparkle click) pins a
  // fingerprint of the facts block; if a later refresh renders different facts,
  // say so and stop offering mutating actions computed from the old state.
  if(a.factsFp === undefined) a.factsFp = factsHtml;
  const stale = a.factsFp !== factsHtml;
  const staleNote = stale && !a.loading && !a.error
    ? '<div class="small dim" style="margin-top:8px">the facts changed since this advice — close and re-ask ✦</div>' : '';
  const body = a.loading ? '<div class="empty">thinking…</div>'
    : a.error ? `<div class="empty">${esc(a.error)}</div>`
    : `<p>${esc(a.read)}</p>` + adviceActions(a.next_actions, stale) + staleNote;
  return `<div class="advisor" style="margin:2px 0 14px">${factsHtml}<div class="takes">
    <div class="h">Advisor’s read <span class="ai-chip"><svg style="width:11px;height:11px"><use href="#sparkle"/></svg>AI companion — advisory, not verified</span></div>
    ${body}</div></div>`;
}

/* Per-card verified-facts blocks (deterministic, instant). */
function routingFacts(routing){
  const items = Object.entries(routing || {}).map(([tier, pool]) => {
    const members = (pool && pool.members) || [];
    const routed = (pool && pool.routed) || {};
    const total = Object.values(routed).reduce((a, b) => a + b, 0);
    // best starts at 0 (review F-A; same convention as the wizard's leadN): a
    // zero-traffic pool elects NO leader — never claim "routing here" without evidence
    let leader = null, best = 0;
    members.forEach(m => { const n = routed[m.worker_id] || 0; if(n > best){ best = n; leader = m; } });
    const lead = leader ? `${esc(leader.model)} routing here` : 'nobody routing here yet';
    return `<li><b>${esc(tier)}</b>: ${lead} · ${members.length} member${members.length === 1 ? '' : 's'} · routed ${total}×</li>`;
  }).join('');
  return `<div class="facts"><div class="h">Routing state <span class="badge dim">verified facts</span></div>`
    + `<ul>${items || '<li>no pools configured yet</li>'}</ul></div>`;
}
function failuresFacts(f){
  const rows = Object.keys(f || {}).flatMap(t =>
    Object.entries(f[t]).map(([mode, n]) => ({t, mode, n})));
  rows.sort((a, b) => b.n - a.n || (a.t + a.mode).localeCompare(b.t + b.mode));
  const top = rows.slice(0, 3).map(({t, mode, n}) =>
    `<li>${esc(taskPlain(t))} — ${esc(mastPlain(mode))} · ${n}×</li>`).join('');
  return `<div class="facts"><div class="h">Top failures <span class="badge dim">verified facts</span></div>`
    + `<ul>${top || '<li>no failures recorded yet</li>'}</ul></div>`;
}
function playbookFacts(bullets){
  const bs = bullets || [];
  const active = bs.filter(b => b.active);
  const scored = active.map(b => ({b, s: (b.helpful + 1) / (b.helpful + b.harmful + 2)}))
    .sort((x, y) => y.s - x.s);
  const best = scored[0], worst = scored[scored.length - 1];
  const line = (label, e) => e ? `<li>${label}: ${esc(e.b.text)} <span class="mono">${(e.s * 100).toFixed(0)}%</span></li>` : '';
  return `<div class="facts"><div class="h">Playbook state <span class="badge dim">verified facts</span></div><ul>`
    + `<li>${bs.length} lesson${bs.length === 1 ? '' : 's'} · ${active.length} active</li>`
    + line('best', best) + (worst && worst !== best ? line('worst', worst) : '')
    + `</ul></div>`;
}

/* Shared action executor — used by the tuning listener AND the card listeners.
   start_tune/add_coverage resolve suite = valid params.suite, else the (trusted,
   already-known) fallbackSuite; card panels pass no fallback, so a bad suite is
   refused with a toast rather than firing the API. */
async function runAdviseAction(action, params, fallbackSuite){
  params = params || {};
  if(action === 'start_tune' || action === 'add_coverage'){
    let suite;
    if(validSuite(params.suite)) suite = params.suite;
    else if(fallbackSuite) suite = fallbackSuite;
    else { toast("the advisor didn’t name a valid suite — pick one in Harness tuning"); return; }
    if(action === 'start_tune'){
      // review K-A1/A2: carry the advisor's tuning knobs through (validated
      // client-side: TuneRequest has no bounds) and honor the user's proposer
      // dropdown instead of silently letting the server default to 'rule'
      const body = {suite, proposer: PROPOSERS.includes(params.proposer) ? params.proposer : TUNE.proposer};
      const sane = (v, max) => Number.isInteger(v) && v > 0 && v <= max;
      if(sane(params.rounds, 12)) body.rounds = params.rounds;
      if(sane(params.k, 5)) body.k = params.k;
      const r = await post('/api/optimization/runs', body);
      toast(r.ok ? `Tuning started on the ${suite} suite` : 'Could not start — a search may already be running');
      refreshConsole();
    } else {
      toast('Asking the frontier agent for harder questions…');
      const r = await post(`/api/optimization/${encodeURIComponent(suite)}/coverage`, {n: 6});
      if(r.ok){
        const d = await r.json();
        toast(`Added ${d.added} harder question${d.added === 1 ? '' : 's'} to the ${suite} suite — run Tune harness again`);
      } else {
        toast('Question generation failed — try again in a moment');
      }
    }
  } else if(action === 'approve_promotion'){
    const banner = document.querySelector('#tuning button[data-tune-approve]');
    if(banner){ banner.scrollIntoView({behavior: 'smooth', block: 'center'}); toast('The decision buttons are in the Promote banner'); }
    else toast('Nothing is awaiting approval right now');
  } else if(action === 'open_settings'){
    showView('settings');
  } else if(action === 'prefill_goal'){
    showView('wizard');
  } else {
    toast('That suggestion needs a human — see the Help tab for how');
  }
}

/* One click handler per advisory card: the header sparkle toggles the panel
   (POST /api/advise, cache, re-render), body buttons run shared actions with no
   fallback suite. Header buttons are injected once by initCardAdvisors so they
   survive the 3s innerHTML refresh (the panel itself re-prepends from cache). */
function initCardAdvisors(){
  Object.entries(CARD_ADVICE_TARGET).forEach(([page, bodyId]) => {
    const card = document.getElementById(bodyId).closest('.card');
    const btn = document.createElement('button');
    btn.className = 'why card-advise';
    btn.dataset.advisePage = page;
    btn.title = 'AI insight — read the harness on this card';
    btn.innerHTML = '<svg><use href="#sparkle"/></svg>';
    card.querySelector('h2').appendChild(btn);
    card.addEventListener('click', async ev => {
      const spark = ev.target.closest('button[data-advise-page]');
      if(spark){
        if(CARD_ADVICE[page]){ delete CARD_ADVICE[page]; btn.classList.remove('on'); refreshConsole(); return; }
        // review F-B: the loading marker doubles as a request token — the response
        // commits only if this exact request is still the cache entry, so closing
        // (or re-opening) while the POST is in flight can never zombie-reopen the panel
        const req = {loading: true};
        CARD_ADVICE[page] = req;
        btn.classList.add('on');
        refreshConsole();
        let res;
        try{
          const r = await post('/api/advise', {page});
          res = r.ok ? await r.json()
            : {error: 'the advisor is unavailable right now — the facts above still stand'};
        }catch(e){
          res = {error: 'the advisor is unavailable right now — the facts above still stand'};
        }
        if(CARD_ADVICE[page] !== req) return;   // closed while pending — discard
        res.factsFp = req.factsFp;              // fingerprint captured at fetch time (F-C)
        CARD_ADVICE[page] = res;
        refreshConsole();
        return;
      }
      const act = ev.target.closest('button[data-advise-act]');
      if(act){
        await runAdviseAction(act.dataset.adviseAct, btnParams(act), null);
      }
    });
  });
}

document.getElementById('tuning').addEventListener('click', async ev => {
  const w = ev.target.closest('button[data-advise]');
  if(w){
    const key = w.dataset.suite + '/' + w.dataset.advise;
    if(ADVICE[key]){ delete ADVICE[key]; refreshConsole(); return; }
    // review K-D1: same request-token guard as the card advisors (F-B) — the
    // response commits only while this exact request is still the cached entry,
    // so a close while the POST is in flight can never zombie-reopen the panel
    const req = {loading: true};
    ADVICE[key] = req;
    refreshConsole();
    let res;
    try{
      const r = await post('/api/advise', {page: 'tuning', subject: w.dataset.advise, suite: w.dataset.suite});
      res = r.ok ? await r.json() : {error: 'the advisor is unavailable right now — the facts above still stand'};
    }catch(e){ res = {error: 'the advisor is unavailable right now — the facts above still stand'}; }
    if(ADVICE[key] !== req) return;   // closed while pending — discard
    ADVICE[key] = res;
    refreshConsole(); return;
  }
  const act = ev.target.closest('button[data-advise-act]');
  if(act){
    const action = act.dataset.adviseAct;
    // the tuning card keeps its existing fallback: start_tune -> TUNE.suite,
    // add_coverage -> the row's suite, else TUNE.suite
    const fallbackSuite = action === 'add_coverage'
      ? (act.dataset.suite || TUNE.suite) : TUNE.suite;
    await runAdviseAction(action, btnParams(act), fallbackSuite);
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
    const r = await post('/api/optimization/runs', {suite: TUNE.suite, proposer: TUNE.proposer});
    toast(r.ok ? `Tuning started on the ${TUNE.suite} suite — watch it think out loud here`
               : 'Could not start — a search may already be running');
    refreshConsole();
  }
});
document.getElementById('tuning').addEventListener('change', ev => {
  if(ev.target.id === 'tune-suite') TUNE.suite = ev.target.value;
  if(ev.target.id === 'tune-proposer') TUNE.proposer = ev.target.value;
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
    const [runs, workers, prov, matrix, playbook, failures, spans, tuning, routing] = await Promise.all([
      get('/api/runs' + (showArchivedRuns ? '?include_archived=true' : '')), get('/api/workers'), get('/api/provenance'),
      get('/api/matrix'), get('/api/playbook'), get('/api/failures'), get('/api/spans'),
      get('/api/optimization'), get('/api/routing'),
    ]);
    // review K-C1: refresh the legal-suite list BEFORE any render uses it, so
    // the advice render guards and the tuning dropdown see this tick's suites
    TUNE_SUITES = [...new Set([...TUNE_DEFAULT_SUITES, ...tuning.map(s => s.suite)])];
    document.getElementById('tiles').innerHTML = renderTiles(runs, workers, prov, playbook);
    document.getElementById('runs').innerHTML = renderRuns(runs);
    document.getElementById('workers').innerHTML = renderWorkers(workers, routing);
    document.getElementById('provenance').innerHTML = renderProvenance(prov);
    document.getElementById('matrix').innerHTML = renderMatrix(matrix, routing);
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

/* Console cards can be arranged (‹ › on hover); the order sticks per browser. */
function initCardArranging(){
  const grid = document.querySelector('#view-console .grid');
  const cards = [...grid.children];
  cards.forEach(card => card.dataset.cardId = card.querySelector('h2').textContent.trim());
  const saved = JSON.parse(localStorage.getItem('console-card-order') || '[]');
  if(saved.length){
    const byId = {};
    cards.forEach(c => byId[c.dataset.cardId] = c);
    saved.forEach(id => { if(byId[id]) grid.appendChild(byId[id]); });
  }
  cards.forEach(card => {
    const ctl = document.createElement('span');
    ctl.className = 'card-move';
    ctl.innerHTML = '<button title="Move card earlier" data-move="-1">‹</button>'
                  + '<button title="Move card later" data-move="1">›</button>';
    card.querySelector('h2').appendChild(ctl);
  });
  grid.addEventListener('click', ev => {
    const b = ev.target.closest('button[data-move]');
    if(!b) return;
    const card = b.closest('.card');
    const sib = b.dataset.move === '-1' ? card.previousElementSibling : card.nextElementSibling;
    if(!sib) return;
    grid.insertBefore(b.dataset.move === '-1' ? card : sib, b.dataset.move === '-1' ? sib : card);
    localStorage.setItem('console-card-order',
      JSON.stringify([...grid.children].map(c => c.dataset.cardId)));
  });
}
initCardArranging();
initCardAdvisors();

/* ================= SETTINGS: wizard-driven configuration ================= */
const SET = { cfg: null, tools: [], provWiz: null, agentWiz: null, mcpWiz: null };

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
  if(SET.mcpWiz){ body.innerHTML = renderMcpWizard(); return; }
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
        ${a.model ? ' · ' + esc(a.model) : ''}${a.system_prompt ? ' · has its own instructions' : ''}
        ${a.timeout_s ? ' · timeout ' + a.timeout_s + 's' : ''}</div>
      <div class="small dim">Roles: ${esc((a.roles || []).join(', ') || 'any')} · Capabilities: ${esc((a.capabilities || []).join(', ') || 'general')}</div></div>
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
      <div class="pi-name">${esc(s.name)} ${badge('dim', s.transport)} ${s.authenticated ? badge('ok','OAuth token') : ''}</div>
      <div class="kv">${esc(s.transport === 'http' ? s.url : s.command + ' ' + (s.args || []).join(' '))}</div></div>
      <button class="pill" data-act="mcp_edit" data-id="${esc(s.name)}">${s.authenticated ? 'Re-authenticate / Edit' : 'Edit'}</button>
      <button class="pill" data-act="mcp_load" data-id="${esc(s.name)}">Load tools</button>
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
        <div class="sub">MCP servers add tools your workflows can call. Load them now after saving, and they also reconnect when the server starts.</div>${mcpRows}
        <button class="btn ghost" onclick="startMcpWizard()">+ Connect MCP server</button>
        <div class="small dim" style="margin-top:6px">Loaded tools appear by MCP server in the workflow step wizard.</div></div>
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
    mcp_edit:  () => startMcpWizardExisting(id),
    mcp_load:  () => loadMcp(id),
    mcp_del:   () => deleteMcp(id),
    pick:      () => pickModel(el.dataset.input, el.dataset.value),
  })[el.dataset.act]?.();
});

async function deleteMcp(name){
  const r = await fetch('/api/config/mcp/' + encodeURIComponent(name), {method:'DELETE'});
  toast(r.ok ? 'Removed tool server ' + name : 'Failed: ' + (await r.text()).slice(0,140));
  if(r.ok){ TOOLS_CATALOG = null; TOOLS_NAMES = null; }
  renderSettings(true);
}

async function loadMcp(name){
  toast('Loading tools from ' + name + '…');
  const r = await post('/api/config/mcp/' + encodeURIComponent(name) + '/load', {});
  if(!r.ok){ toast('Load failed: ' + (await r.text()).slice(0,140)); return; }
  const report = await r.json();
  toast(report.ok ? `Loaded ${report.tools} tool${report.tools === 1 ? '' : 's'} from ${name}`
                  : `Could not load ${name}: ${report.detail || 'connection failed'}`);
  if(report.ok){ TOOLS_CATALOG = null; TOOLS_NAMES = null; renderSettings(true); }
}

async function deleteProvider(pid){
  const r = await fetch('/api/config/providers/' + encodeURIComponent(pid), {method:'DELETE'});
  toast(r.ok ? 'Removed ' + pid : 'Failed: ' + (await r.text()).slice(0,140));
  renderSettings(true);
}

async function removeAgent(id){
  const r = await fetch('/api/workers/' + encodeURIComponent(id), {method:'DELETE'});
  toast(r.ok ? 'Retired ' + id : 'Failed: ' + (await r.text()).slice(0,140));
  if(r.ok){ ASSIGNMENT_WORKERS=[]; ASSIGNMENT_WORKERS_LOADED=false; }
  renderSettings(true);
}

/* ---------- MCP wizard: connection type → details → review & save ---------- */
const MCP_PRESETS = {
  filesystem: {label: 'Filesystem', transport: 'stdio', name: 'filesystem',
    command: 'npx', args: ['-y', '@modelcontextprotocol/server-filesystem@2026.7.10', '/path/to/allowed/folder'],
    hint: '<b>Filesystem · official MCP server</b>Search, read, and manage files inside directories you explicitly allow. Replace the example directory before saving.'},
  brave: {label: 'Brave Search', transport: 'stdio', name: 'brave-search',
    command: 'npx', args: ['-y', '@brave/brave-search-mcp-server@2.0.85', '--transport', 'stdio'],
    env: [{key: 'BRAVE_API_KEY', value: ''}], required_env: 'BRAVE_API_KEY',
    hint: '<b>Brave Search · vendor maintained</b>Web, news, image, and local search through your Brave Search API key.'},
  playwright: {label: 'Playwright', transport: 'stdio', name: 'playwright',
    command: 'npx', args: ['-y', '@playwright/mcp@0.0.78', '--isolated', '--headless'],
    hint: '<b>Playwright · Microsoft maintained</b>Browse and interact with websites in an isolated headless browser session.'},
  gmail: {label: 'Gmail', transport: 'http', name: 'gmail',
    url: 'https://gmailmcp.googleapis.com/mcp/v1', requires_oauth: true,
    hint: '<b>Gmail · official Google endpoint</b>Search mail and create drafts. Requires a scoped OAuth access token; mailbox passwords are never accepted.'},
  calendar: {label: 'Google Calendar', transport: 'http', name: 'google-calendar',
    url: 'https://calendarmcp.googleapis.com/mcp/v1', requires_oauth: true,
    hint: '<b>Google Calendar · official Google endpoint</b>List and manage calendar events with a scoped OAuth access token.'},
  local: {label: 'Custom local', transport: 'stdio', name: '',
    hint: '<b>Custom local command</b>Start any trusted MCP server installed on this machine.'},
  remote: {label: 'Custom remote', transport: 'http', name: '',
    hint: '<b>Custom remote URL</b>Connect to a trusted MCP HTTP endpoint that is already running.'},
};

function startMcpWizard(){
  SET.mcpWiz = {step: 0, editing:false, preset: 'filesystem', transport: 'stdio', name: '',
    command: '', args: [], env: [], url: '', oauth_token: '',
    oauth_project: '', requires_oauth: false, required_env: ''};
  mcpApplyPreset('filesystem');
  renderSettings();
}

function startMcpWizardExisting(name){
  const server=(SET.cfg.mcp_servers || {})[name]; if(!server) return;
  const preset=server.url === MCP_PRESETS.gmail.url ? 'gmail'
    : server.url === MCP_PRESETS.calendar.url ? 'calendar'
    : server.transport === 'stdio' ? 'local' : 'remote';
  SET.mcpWiz={step:1, editing:true, preset, transport:server.transport, name:server.name,
    command:server.command || '', args:(server.args || []).slice(),
    env:Object.entries(server.env || {}).map(([key,value])=>({key,value})), url:server.url || '',
    oauth_token:server.oauth_token || '', oauth_project:server.oauth_project || '',
    requires_oauth:['gmail','calendar'].includes(preset), required_env:''};
  renderSettings();
}

function mcpWizSteps(){
  const labels = ['Connection type','Details','Review'];
  return '<div class="subwiz-steps">' + labels.map((l, i) =>
    `<span class="t ${i === SET.mcpWiz.step ? 'on' : ''} ${i < SET.mcpWiz.step ? 'done' : ''}">${i + 1} · ${l}</span>`).join('') + '</div>';
}

function renderMcpWizard(){
  const w = SET.mcpWiz;
  let inner = '';
  if(w.step === 0){
    const preset = MCP_PRESETS[w.preset];
    inner = `<div class="sub">Start with a maintained preset, or connect your own trusted server.</div>
      <div class="pillrow">${Object.entries(MCP_PRESETS).map(([id, p]) =>
        `<button class="pill ${w.preset === id ? 'on' : ''}" onclick="mcpPick('${id}')">${esc(p.label)}</button>`).join('')}</div>
      <div class="hint-panel">${preset.hint}</div>
      <div class="small dim" style="margin-top:8px">Presets are never enabled automatically. Review their command, permissions, and credentials before saving.</div>`;
  }else if(w.step === 1){
    const common = `<div class="field"><label>Connection name</label>
      <input id="mw-name" class="mono" value="${esc(w.name)}" placeholder="filesystem-tools" ${w.editing ? 'disabled' : ''}>
      <span class="small dim">A short name used in config and tool source labels.</span></div>`;
    if(w.transport === 'stdio'){
      const envRows = w.env.map((pair, i) => `<div style="display:flex;gap:8px;margin-top:8px">
        <input id="mw-env-key-${i}" class="mono" value="${esc(pair.key)}" placeholder="VARIABLE_NAME" style="flex:1">
        <input id="mw-env-value-${i}" class="mono" type="password" value="${esc(pair.value)}" placeholder="value" style="flex:1">
        <button class="pill" onclick="mcpRemoveEnv(${i})">Remove</button></div>`).join('');
      inner = `${common}
        <div class="field"><label>Command</label>
          <input id="mw-command" class="mono" value="${esc(w.command)}" placeholder="npx">
          <span class="small dim">Just the executable. Arguments go below.</span></div>
        <div class="field"><label>Arguments — one per line</label>
          <textarea id="mw-args" class="mono" placeholder="-y&#10;@modelcontextprotocol/server-filesystem&#10;/path/to/folder">${esc(w.args.join('\\n'))}</textarea>
          <span class="small dim">Each line stays one argument, including paths or values containing spaces.</span></div>
        <details class="field" open><summary style="cursor:pointer">Environment variables (optional)</summary>
          ${envRows || '<div class="small dim" style="margin-top:8px">No extra environment variables.</div>'}
          <button class="btn ghost" style="margin-top:8px" onclick="mcpAddEnv()">+ Add variable</button></details>`;
    }else{
      inner = `${common}<div class="field"><label>MCP server URL</label>
        <input id="mw-url" class="mono" type="url" value="${esc(w.url)}" placeholder="https://tools.example.com/mcp" ${w.requires_oauth ? 'disabled' : ''}>
        <span class="small dim">The full HTTP endpoint supplied by the server operator.</span></div>
        ${w.requires_oauth ? `<div class="field"><label>OAuth access token</label>
          <input id="mw-oauth-token" class="mono" type="password" value="${esc(w.oauth_token)}" placeholder="OAuth bearer token">
          <span class="small dim">Use a scoped, temporary OAuth token from your provider. Tokens normally expire and must then be replaced. It is obfuscated at rest, masked in the API, and sent only to the pinned Google endpoint above.</span></div>
          <div class="field"><label>Google Cloud project ID</label>
            <input id="mw-oauth-project" class="mono" value="${esc(w.oauth_project)}" placeholder="my-workspace-project">
            <span class="small dim">The project where this Workspace MCP API is enabled; sent as x-goog-user-project.</span></div>` : ''}`;
    }
  }else{
    const destination = w.transport === 'stdio'
      ? `${esc(w.command)}${w.args.length ? ' · ' + w.args.length + ' argument' + (w.args.length === 1 ? '' : 's') : ' · no arguments'}`
      : esc(w.url);
    const envNote = w.transport === 'stdio' && w.env.length
      ? `<div class="small dim" style="margin-top:8px">Environment: ${w.env.map(p => esc(p.key)).join(', ')} (values hidden)</div>` : '';
    const authNote = w.requires_oauth
      ? `<div class="small dim" style="margin-top:8px">OAuth token ${w.oauth_token ? 'set' : 'missing'} · value hidden · project ${esc(w.oauth_project)}</div>` : '';
    inner = `<div class="hint-panel"><b>Ready to connect ${esc(w.name)}</b>
      <span class="kv">${w.transport === 'stdio' ? 'Local command' : 'Remote URL'} → ${destination}</span>${envNote}${authNote}</div>
      <div class="small dim">Saving writes this connection to config. Then use Load tools—no terminal or manual CLI step required.</div>`;
  }
  return `<div class="card"><h2>${w.editing ? 'Edit MCP server' : 'Connect MCP server'}</h2>
    ${mcpWizSteps()}${inner}
    <div class="wiz-nav">
      <button class="btn ghost" onclick="mcpWizNav(-1)">${w.step === 0 ? 'Cancel' : '← Back'}</button>
      ${w.step < 2
        ? '<button class="btn" onclick="mcpWizNav(1)">Next →</button>'
        : `<button class="btn" onclick="mcpSave()">${w.editing ? 'Update connection' : 'Save connection'}</button>`}</div></div>`;
}

function mcpApplyPreset(id){
  const p = MCP_PRESETS[id];
  Object.assign(SET.mcpWiz, {preset: id, transport: p.transport, name: p.name || '',
    command: p.command || '', args: (p.args || []).slice(),
    env: (p.env || []).map(pair => ({...pair})), url: p.url || '', oauth_token: '',
    oauth_project: '', requires_oauth: !!p.requires_oauth,
    required_env: p.required_env || ''});
}

function mcpPick(id){
  mcpApplyPreset(id);
  renderSettings();
}

function mcpCapture(){
  const w = SET.mcpWiz;
  if(w.step !== 1) return;
  const name = document.getElementById('mw-name');
  if(name) w.name = name.value.trim();
  if(w.transport === 'stdio'){
    w.command = document.getElementById('mw-command').value.trim();
    w.args = document.getElementById('mw-args').value.split('\\n')
      .map(arg => arg.trim()).filter(Boolean);
    w.env = w.env.map((pair, i) => ({
      key: document.getElementById(`mw-env-key-${i}`).value.trim(),
      value: document.getElementById(`mw-env-value-${i}`).value,
    }));
  }else{
    w.url = document.getElementById('mw-url').value.trim();
    const token = document.getElementById('mw-oauth-token');
    if(token) w.oauth_token = token.value.trim();
    const project = document.getElementById('mw-oauth-project');
    if(project) w.oauth_project = project.value.trim();
  }
}

function mcpAddEnv(){
  mcpCapture();
  SET.mcpWiz.env.push({key: '', value: ''});
  renderSettings();
}

function mcpRemoveEnv(i){
  mcpCapture();
  SET.mcpWiz.env.splice(i, 1);
  renderSettings();
}

function mcpWizNav(delta){
  mcpCapture();
  const w = SET.mcpWiz;
  if(w.step === 0 && delta < 0){ SET.mcpWiz = null; renderSettings(); return; }
  if(delta > 0 && w.step === 1){
    if(!w.name){ toast('give the MCP connection a name'); return; }
    if(w.transport === 'stdio' && !w.command){ toast('give the local command'); return; }
    if(w.transport === 'http' && !w.url){ toast('give the MCP server URL'); return; }
    if(w.requires_oauth && !w.oauth_token){ toast('give an OAuth access token'); return; }
    if(w.requires_oauth && !w.oauth_project){ toast('give the Google Cloud project ID'); return; }
    if(w.preset === 'filesystem' && w.args.includes('/path/to/allowed/folder')){
      toast('replace the example with an allowed directory'); return;
    }
    if(w.required_env && !w.env.some(p => p.key === w.required_env && p.value)){
      toast(`give ${w.required_env}`); return;
    }
    if(w.transport === 'stdio' && w.env.some(p => !p.key && p.value)){
      toast('environment variable values need a name'); return;
    }
    w.env = w.env.filter(p => p.key);
  }
  w.step = Math.max(0, Math.min(2, w.step + delta));
  renderSettings();
}

async function mcpSave(){
  const w = SET.mcpWiz;
  const body = {name: w.name, transport: w.transport};
  if(w.transport === 'stdio'){
    body.command = w.command;
    body.args = w.args;
    body.env = Object.fromEntries(w.env.map(p => [p.key, p.value]));
  }else{
    body.url = w.url;
    if(w.oauth_token) body.oauth_token = w.oauth_token;
    if(w.oauth_project) body.oauth_project = w.oauth_project;
  }
  const r = await post('/api/config/mcp', body);
  if(!r.ok){ toast('save failed: ' + (await r.text()).slice(0,140)); return; }
  toast((w.editing ? 'Updated' : 'Connected') + ' MCP server ' + w.name);
  SET.mcpWiz = null;
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
// kind default timeout, in words — shown under the wizard's Advanced timeout
// field (issue #2). Only kinds with a real timeout knob appear here; mock
// has none.
const TIMEOUT_KIND_HINT = {
  openai_compat: 'default is 120s, flat for every task type',
  coding_cli: 'default is 600s (1800s for code-edit tasks — 3× when unset)',
  subscription_cli: 'default is 300s (900s for code-edit tasks — 3× when unset)',
};

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
    roles: a ? (a.roles || []).slice() : [],
    capabilities: a ? (a.capabilities || []).slice() : [],
    timeout_s: a ? (a.timeout_s ?? null) : null,
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
        subscription_cli: '<b>Subscription access</b>LLM completions through your signed-in Claude Code (Anthropic subscription) or Codex CLI (OpenAI subscription). No API key stored — the CLI login is the credential. These agents inspect the active workspace with read-only tools; they cannot edit it.',
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
      <div class="field" style="display:flex;gap:10px">
        <span style="flex:1"><label for="aw-roles">Roles this agent can take</label><input id="aw-roles" value="${esc(w.roles.join(', '))}" placeholder="reviewer, implementer"></span>
        <span style="flex:1"><label for="aw-capabilities">Capability IDs</label><input id="aw-capabilities" value="${esc(w.capabilities.join(', '))}" placeholder="workspace.write, tests.run"></span></div>
      <div class="small dim">Comma-separated. Automatic assignment uses these exact IDs to match a stage's role and capability requirements.</div>
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
        </ul></div>
      ${w.kind === 'mock' ? '' : `<details class="field"><summary style="cursor:pointer">Advanced</summary>
        <div class="field" style="margin-top:8px"><label>Timeout (seconds)</label>
          <input id="aw-timeout" type="number" step="any" class="mono"
            value="${w.timeout_s == null ? '' : w.timeout_s}" placeholder="default">
          <span class="small dim">Blank = ${TIMEOUT_KIND_HINT[w.kind] || 'the kind default'}.</span></div>
      </details>`}`;
  }else{
    inner = `
      <div class="hint-panel"><b>About to ${w.editing ? 'update' : 'register'}</b>
        <span class="kv">${esc(w.worker_id)} · ${esc(w.tier)} · ${esc(w.kind)}
        ${w.kind === 'coding_cli' ? '· ' + esc(w.cli) : '· ' + esc(w.provider || w.base_url) + (w.model ? ' · ' + esc(w.model) : '')}
        ${w.timeout_s ? ' · timeout ' + w.timeout_s + 's' : ''}</span></div>
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
  // a timeout entered for one kind must not silently survive a switch to
  // mock — MockLLMWorker has no timeout to apply it to (issue #2)
  if(key === 'kind' && value === 'mock'){ SET.agentWiz.timeout_s = null; }
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
  const roles=grab('aw-roles'); if(roles !== null) w.roles=roles.split(',').map(x=>x.trim()).filter(Boolean);
  const capabilities=grab('aw-capabilities'); if(capabilities !== null) w.capabilities=capabilities.split(',').map(x=>x.trim()).filter(Boolean);
  const timeout = grab('aw-timeout');
  if(timeout !== null){
    const n = parseFloat(timeout);
    w.timeout_s = (timeout.trim() === '' || !(n > 0)) ? null : n;  // blank/≤0 -> default
  }
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
    system_prompt: w.system_prompt, cli: w.cli, roles:w.roles, capabilities:w.capabilities, persist: true,
    // a timeout entered before a kind switch to mock must not silently
    // reach a kind that has no timeout to apply it to (issue #2)
    ...(w.timeout_s && w.kind !== 'mock' ? {timeout_s: w.timeout_s} : {})});
  if(!r.ok){ toast('failed: ' + (await r.text()).slice(0,140)); return; }
  toast(`${w.editing ? 'Updated' : 'Registered'} ${w.worker_id} on ${w.tier} tier`);
  ASSIGNMENT_WORKERS=[]; ASSIGNMENT_WORKERS_LOADED=false;
  SET.agentWiz = null;
  renderSettings(true);
}

setStep(0);        // pre-render the wizard so switching to Run is instant
showView('home');  // the calm landing answers "what do I do right now?"
</script>
</body>
</html>
"""
