import assert from 'node:assert/strict';
import {serviceEndpoint,signInWithGitHub} from '../static/refresh-auth.js';
assert.equal(serviceEndpoint('https://auth.test/','/api/refresh'),'https://auth.test/api/refresh');
assert.throws(()=>serviceEndpoint('http://auth.test/','/login'));
assert.throws(()=>serviceEndpoint('https://user:secret@auth.test/','/login'));
assert.throws(()=>serviceEndpoint('https://auth.test/?secret=value','/login'));
await assert.rejects(signInWithGitHub(''),/awaiting service setup/);
let channel,channelClosed=false,popup={closed:false,location:{},close(){this.closed=true;}},attempt,calls=[];
let settled=false;
const pending=signInWithGitHub('https://auth.test/',{
 openWindow(){return popup;},
 makeChannel(name){attempt=name.replace('atlas-auth-','');return channel={close(){channelClosed=true;}};},
 fetcher:async(url,options)=>{calls.push({url,options});return Response.json({session:'opaque-test-session',expires:Date.now()/1000+60});},
}).then(value=>{settled=true;return value;});
await new Promise(resolve=>setTimeout(resolve,10));
assert.equal(new URL(popup.location.href).searchParams.get('attempt'),attempt);
assert.match(new URL(popup.location.href).searchParams.get('challenge'),/^[A-Za-z0-9_-]{43}$/);
await channel.onmessage({data:{attempt:'different',code:'a'.repeat(43)}});
assert.equal(settled,false);
// OAuth may sever the popup reference. The same-origin bridge still completes sign-in.
popup.closed=true;
await channel.onmessage({data:{attempt,code:'a'.repeat(43)}});
assert.equal(await pending,'opaque-test-session');assert.equal(channelClosed,true);
assert.match(calls[0].options.headers['X-Atlas-Proof'],/^[a-f0-9]{64}$/);
assert.equal(calls[0].options.headers['X-Atlas-Code'],'a'.repeat(43));
assert.ok(!calls[0].url.includes(calls[0].options.headers['X-Atlas-Proof']));
await assert.rejects(signInWithGitHub('https://auth.test/',{openWindow(){return null;}}),/Allow the GitHub sign-in popup/);
const controller=new AbortController();controller.abort();
await assert.rejects(signInWithGitHub('https://auth.test/',{signal:controller.signal,openWindow(){return {location:{},close(){}};},makeChannel(){return {close(){}};}}),/cancelled/);
console.log('GitHub sign-in: service URL, popup isolation, one-time code, secret proof, cancellation and successful cleanup passed');
