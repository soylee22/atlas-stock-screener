const REPO = 'soylee22/atlas-stock-screener';
const FRONTEND = 'https://soylee22.github.io';
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const API = `https://api.github.com/repos/${REPO}`;
const b64 = bytes => btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
function unb64(value) { return Uint8Array.from(atob(value.replaceAll('-','+').replaceAll('_','/')), c=>c.charCodeAt(0)); }
const random = () => b64(crypto.getRandomValues(new Uint8Array(32)));
const now = () => Math.floor(Date.now()/1000);
async function key(env) {
  const bytes = unb64(env.SESSION_KEY || '');
  if (bytes.length !== 32) throw new Error('Service configuration is incomplete');
  return crypto.subtle.importKey('raw',bytes,'AES-GCM',false,['encrypt','decrypt']);
}
export async function seal(payload,env) {
  const iv=crypto.getRandomValues(new Uint8Array(12));
  const data=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:encoder.encode('atlas-refresh-v1')},await key(env),encoder.encode(JSON.stringify(payload)));
  return `${b64(iv)}.${b64(new Uint8Array(data))}`;
}
export async function open(value,env,kind) {
  const parts=(value||'').split('.');
  if(parts.length!==2 || value.length>12000)throw new Error('Invalid session');
  const raw=await crypto.subtle.decrypt({name:'AES-GCM',iv:unb64(parts[0]),additionalData:encoder.encode('atlas-refresh-v1')},await key(env),unb64(parts[1]));
  const payload=JSON.parse(decoder.decode(raw));
  if(payload.kind!==kind || !Number.isFinite(payload.exp) || payload.exp<=now())throw new Error('Session expired');
  return payload;
}
const headers = origin => ({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',...(origin===FRONTEND?{'Access-Control-Allow-Origin':FRONTEND,'Vary':'Origin'}:{})});
const json=(value,status=200,origin)=>new Response(JSON.stringify(value),{status,headers:{...headers(origin),'Content-Type':'application/json'}});
const flowCookie=(value,age=600)=>`__Host-atlas_flow=${value}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=${age}`;
function callbackPage(message,nonce) {
  const scriptNonce=random();
  const body=JSON.stringify({...message,attempt:nonce}).replaceAll('<','\\u003c');
  const html=`<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>Atlas sign-in</title><p>${message.type==='atlas-auth-success'?'Signed in. Returning to Atlas…':'Atlas could not complete sign-in. Return to Atlas and retry.'}</p><script nonce="${scriptNonce}">if(window.opener){window.opener.postMessage(${body},${JSON.stringify(FRONTEND)});window.close();}</script>`;
  return new Response(html,{headers:{...headers(),'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':`default-src 'none'; script-src 'nonce-${scriptNonce}'; frame-ancestors 'none'; base-uri 'none'`,'Referrer-Policy':'no-referrer','Set-Cookie':flowCookie('',0)}});
}
export function createService(fetcher=fetch) {
  async function github(path,token,options={}) {
    const response=await fetcher(path.startsWith('https://')?path:API+path,{
      ...options,headers:{Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10','User-Agent':'Atlas-Refresh-Sign-In',Authorization:`Bearer ${token}`,...(options.body?{'Content-Type':'application/json'}:{})},signal:AbortSignal.timeout(20000),
    });
    if(!response.ok) {
      const error=new Error(response.status===401?'GitHub sign-in expired. Please sign in again.':'GitHub could not complete this request.');
      error.status=response.status===401?401:502;throw error;
    }
    return response.status===204?null:response.json();
  }
  return async function handle(request,env) {
    const url=new URL(request.url),origin=request.headers.get('Origin');
    if(url.pathname==='/health')return json({service:'Atlas refresh sign-in',configured:!!(env.GITHUB_CLIENT_ID&&env.GITHUB_CLIENT_SECRET&&env.SESSION_KEY&&env.OWNER_ID)});
    if(url.pathname.startsWith('/api/')) {
      if(origin!==FRONTEND)return json({error:'Origin is not allowed'},403);
      if(request.method==='OPTIONS')return new Response(null,{status:204,headers:{...headers(origin),'Access-Control-Allow-Methods':'GET, POST, OPTIONS','Access-Control-Allow-Headers':'Authorization, Content-Type','Access-Control-Max-Age':'600'}});
      let session;
      try { session=await open(request.headers.get('Authorization')?.replace(/^Bearer /,''),env,'session'); }
      catch { return json({error:'Please sign in with GitHub'},401,origin); }
      if(String(session.user_id)!==String(env.OWNER_ID))return json({error:'This account cannot refresh Atlas'},403,origin);
      try {
        if(url.pathname==='/api/latest'&&request.method==='GET')return json(await github('/actions/workflows/pages.yml/runs?branch=main&per_page=10',session.github_token),200,origin);
        const match=url.pathname.match(/^\/api\/runs\/([1-9]\d{0,19})(\/jobs)?$/);
        if(match&&request.method==='GET')return json(await github(`/actions/runs/${match[1]}${match[2]?'/jobs?per_page=100':''}`,session.github_token),200,origin);
        if(url.pathname==='/api/refresh'&&request.method==='POST') {
          if(Number(request.headers.get('Content-Length')||0)>1000)return json({error:'Request is too large'},413,origin);
          const runs=await github('/actions/workflows/pages.yml/runs?branch=main&per_page=10',session.github_token);
          const current=runs.workflow_runs.find(run=>['queued','pending','waiting','requested','in_progress'].includes(run.status));
          if(current)return json({workflow_run_id:current.id,html_url:current.html_url,reused:true},200,origin);
          const result=await github('/actions/workflows/pages.yml/dispatches',session.github_token,{method:'POST',body:JSON.stringify({ref:'main',inputs:{intraday:true}})});
          return json(result||{accepted:true},200,origin);
        }
        return json({error:'Unknown operation'},404,origin);
      } catch(error) { return json({error:error.message},error.status||502,origin); }
    }
    if(!env.GITHUB_CLIENT_ID||!env.GITHUB_CLIENT_SECRET||!env.SESSION_KEY||!env.OWNER_ID)return json({error:'Atlas sign-in is awaiting account setup'},503);
    if(url.pathname==='/login'&&request.method==='GET') {
      const attempt=url.searchParams.get('attempt');
      if(!/^[a-f0-9]{32}$/.test(attempt||''))return json({error:'Invalid sign-in request'},400);
      const state=random(),verifier=random();
      const flow=await seal({kind:'flow',attempt,state,verifier,exp:now()+600},env);
      const challenge=b64(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(verifier))));
      const target=new URL('https://github.com/login/oauth/authorize');
      target.search=new URLSearchParams({client_id:env.GITHUB_CLIENT_ID,redirect_uri:url.origin+'/callback',state,code_challenge:challenge,code_challenge_method:'S256'}).toString();
      return new Response(null,{status:302,headers:{...headers(),Location:target.href,'Set-Cookie':flowCookie(flow),'Referrer-Policy':'no-referrer'}});
    }
    if(url.pathname==='/callback'&&request.method==='GET') {
      let flow;
      try {
        const cookie=request.headers.get('Cookie')?.split(';').map(s=>s.trim()).find(s=>s.startsWith('__Host-atlas_flow='))?.slice('__Host-atlas_flow='.length);
        flow=await open(cookie,env,'flow');
        if(!url.searchParams.get('state')||url.searchParams.get('state')!==flow.state||!url.searchParams.get('code'))throw new Error('Sign-in did not match the request');
        const exchange=await fetcher('https://github.com/login/oauth/access_token',{method:'POST',headers:{Accept:'application/json','Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({client_id:env.GITHUB_CLIENT_ID,client_secret:env.GITHUB_CLIENT_SECRET,code:url.searchParams.get('code'),redirect_uri:url.origin+'/callback',code_verifier:flow.verifier}),signal:AbortSignal.timeout(20000)});
        if(!exchange.ok)throw new Error('GitHub sign-in failed');
        const grant=await exchange.json();
        if(typeof grant.access_token!=='string'||grant.error)throw new Error('GitHub sign-in failed');
        const user=await github('https://api.github.com/user',grant.access_token);
        if(String(user.id)!==String(env.OWNER_ID))return callbackPage({type:'atlas-auth-error',error:'This GitHub account cannot refresh Atlas'},flow.attempt);
        // Confirm the installed app can access this repository before creating a session.
        await github('/actions/workflows/pages.yml',grant.access_token);
        const exp=now()+Math.min(3600,Math.max(0,Number(grant.expires_in)||3600));
        const session=await seal({kind:'session',github_token:grant.access_token,user_id:user.id,exp},env);
        return callbackPage({type:'atlas-auth-success',session,expires:exp},flow.attempt);
      } catch { return callbackPage({type:'atlas-auth-error',error:'GitHub sign-in was not completed. Please retry.'},flow?.attempt||''); }
    }
    return json({error:'Not found'},404);
  };
}
const handle=createService();
export default {fetch:handle};
