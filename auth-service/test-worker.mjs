import test from 'node:test';
import assert from 'node:assert/strict';
import {createService,seal,open} from './worker.js';
const env={GITHUB_CLIENT_ID:'client',GITHUB_CLIENT_SECRET:'secret',SESSION_KEY:Buffer.alloc(32,7).toString('base64url'),OWNER_ID:'73914454'};
const origin='https://soylee22.github.io';
const expires=()=>Math.floor(Date.now()/1000)+600;
const session=()=>seal({kind:'session',github_token:'github-test-only',user_id:73914454,exp:expires()},env);
const request=(path,options={})=>new Request('https://auth.test'+path,options);
const apiRequest=async(path,options={})=>request(path,{...options,headers:{Origin:origin,Authorization:'Bearer '+await session(),...options.headers}});

test('sessions are encrypted, authenticated and expire',async()=>{
 const value=await session();assert.ok(!value.includes('github-test-only'));
 assert.equal((await open(value,env,'session')).user_id,73914454);
 await assert.rejects(open(value+'x',env,'session'));
 await assert.rejects(open(await seal({kind:'session',exp:1},env),env,'session'));
 await assert.rejects(open(value,env,'flow'));
});
test('cross-origin calls and missing sessions cannot refresh',async()=>{
 const handler=createService(()=>{throw Error('must not call GitHub');});
 assert.equal((await handler(request('/api/refresh',{method:'POST',headers:{Origin:'https://evil.test'}}),env)).status,403);
 assert.equal((await handler(request('/api/refresh',{method:'POST',headers:{Origin:origin}}),env)).status,401);
});
test('session for another user cannot refresh',async()=>{
 const token=await seal({kind:'session',github_token:'test',user_id:1,exp:expires()},env);
 const response=await createService()(request('/api/refresh',{method:'POST',headers:{Origin:origin,Authorization:'Bearer '+token}}),env);
 assert.equal(response.status,403);
});
test('active run is reused without dispatch',async()=>{
 const calls=[];const handler=createService(async(url,options)=>{calls.push({url,options});return Response.json({workflow_runs:[{id:123,status:'queued',html_url:'https://github.com/run/123'}]});});
 const response=await handler(await apiRequest('/api/refresh',{method:'POST'}),env);
 assert.equal(response.status,200);assert.equal((await response.json()).reused,true);assert.equal(calls.length,1);
});
test('dispatch is pinned to the Atlas main intraday workflow',async()=>{
 const calls=[];const handler=createService(async(url,options)=>{calls.push({url,options});return Response.json(url.endsWith('/dispatches')?{workflow_run_id:456}:{workflow_runs:[]});});
 const response=await handler(await apiRequest('/api/refresh',{method:'POST',body:JSON.stringify({ref:'another-branch',repository:'other/repo'})}),env);
 assert.equal((await response.json()).workflow_run_id,456);
 assert.ok(calls[1].url.startsWith('https://api.github.com/repos/soylee22/atlas-stock-screener/'));
 assert.deepEqual(JSON.parse(calls[1].options.body),{ref:'main',inputs:{intraday:true}});
 assert.equal(calls[1].options.headers.Authorization,'Bearer github-test-only');
});
test('CORS is restricted and the proxy has no arbitrary GitHub route',async()=>{
 const handler=createService();
 const options=await handler(request('/api/refresh',{method:'OPTIONS',headers:{Origin:origin}}),env);
 assert.equal(options.status,204);assert.equal(options.headers.get('Access-Control-Allow-Origin'),origin);
 assert.equal((await handler(await apiRequest('/api/repos/other/repo'),env)).status,404);
});
test('login uses state, PKCE and a secure HttpOnly flow cookie',async()=>{
 const response=await createService()(request('/login?attempt='+'a'.repeat(32)),env);
 assert.equal(response.status,302);
 const target=new URL(response.headers.get('Location'));
 assert.equal(target.origin,'https://github.com');assert.equal(target.searchParams.get('code_challenge_method'),'S256');assert.ok(target.searchParams.get('state'));
 const cookie=response.headers.get('Set-Cookie');assert.match(cookie,/Secure; HttpOnly; SameSite=Lax/);
 assert.ok(!cookie.includes('secret'));
});
async function callback(handler,userId=73914454,wrongState=false){
 const login=await handler(request('/login?attempt='+'a'.repeat(32)),env);
 const target=new URL(login.headers.get('Location'));
 const state=wrongState?'wrong':target.searchParams.get('state');
 return handler(request('/callback?code=example&state='+state,{headers:{Cookie:login.headers.get('Set-Cookie').split(';')[0]}}),env);
}
test('bad OAuth state is rejected before exchanging credentials',async()=>{
 let count=0;const handler=createService(async()=>{count++;throw Error('not expected');});
 const result=await callback(handler,73914454,true);assert.equal(count,0);assert.match(await result.text(),/atlas-auth-error/);
});
test('authorised callback returns only an opaque session to the exact Atlas origin',async()=>{
 const handler=createService(async(url)=>Response.json(url.includes('access_token')?{access_token:'private-github-token',expires_in:3600}:url.endsWith('/user')?{id:73914454}:{name:'Atlas'}));
 const response=await callback(handler);const html=await response.text();
 assert.match(html,/atlas-auth-success/);assert.ok(!html.includes('private-github-token'));assert.match(html,/https:\/\/soylee22.github.io/);assert.match(response.headers.get('Set-Cookie'),/Max-Age=0/);assert.match(response.headers.get('Content-Security-Policy'),/default-src 'none'/);
});
test('a signed-in GitHub user who is not Lee is refused',async()=>{
 const handler=createService(async(url)=>Response.json(url.includes('access_token')?{access_token:'test'}:{id:1}));
 const html=await(await callback(handler,1)).text();assert.match(html,/atlas-auth-error/);assert.ok(!html.includes('atlas-auth-success'));
});
