// Filled after the sign-in service is deployed. Never put GitHub credentials here.
export const REFRESH_SERVICE_URL = 'https://atlas-refresh-auth.econd-rain.workers.dev';

export function serviceEndpoint(serviceURL,path) {
  const service=new URL(serviceURL);
  if(service.protocol!=='https:'||service.username||service.password||service.search||service.hash||service.pathname!=='/')throw new Error('Atlas sign-in service is not configured correctly');
  return new URL(path,service.origin).href;
}

export async function signInWithGitHub(serviceURL,{openWindow=(...args)=>window.open(...args),makeChannel=name=>new BroadcastChannel(name),fetcher=fetch,signal}={}) {
  if(!serviceURL)throw new Error('Atlas sign-in is awaiting service setup.');
  const login=serviceEndpoint(serviceURL,'/login');
  const hex=n=>Array.from(crypto.getRandomValues(new Uint8Array(n)),b=>b.toString(16).padStart(2,'0')).join('');
  const attempt=hex(16),proof=hex(32);
  // Open synchronously, before the first await, to preserve the click's popup permission.
  const popup=openWindow('about:blank','atlas-github-sign-in','popup,width=560,height=720');
  if(!popup)throw new Error('Allow the GitHub sign-in popup to refresh Atlas.');
  const channel=makeChannel('atlas-auth-'+attempt);
  const challenge=btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(proof))))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
  return new Promise((resolve,reject)=>{
    let done=false;
    const clear=()=>{clearTimeout(timeout);channel.close();signal?.removeEventListener('abort',abort);};
    const fail=message=>{if(done)return;done=true;clear();try{popup.close();}catch{}reject(new Error(message));};
    const abort=()=>fail('GitHub sign-in was cancelled. Click Refresh data to retry.');
    const timeout=setTimeout(()=>fail('GitHub sign-in expired. Click Refresh data to retry.'),600000);
    signal?.addEventListener('abort',abort,{once:true});
    if(signal?.aborted){abort();return;}
    channel.onmessage=async event=>{
      const data=event.data;
      if(done||data?.attempt!==attempt||!/^[A-Za-z0-9_-]{43}$/.test(data.code||''))return;
      try {
        const response=await fetcher(serviceEndpoint(serviceURL,`/api/sign-in-result?attempt=${attempt}`),{cache:'no-store',headers:{'X-Atlas-Proof':proof,'X-Atlas-Code':data.code},signal:AbortSignal.timeout(20000)});
        const result=await response.json();
        if(!response.ok||result.error)throw new Error(result.error||'GitHub sign-in did not complete');
        if(typeof result.session!=='string'||Number(result.expires)<=Date.now()/1000)throw new Error('GitHub sign-in expired');
        if(done)return;done=true;clear();try{popup.close();}catch{}resolve(result.session);
      } catch(error) {fail(error.message);}
    };
    try {popup.location.href=`${login}?attempt=${attempt}&challenge=${challenge}`;}catch{fail('Could not open GitHub sign-in. Please retry.');}
  });
}
