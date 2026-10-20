// Filled after the sign-in service is deployed. Never put GitHub credentials here.
export const REFRESH_SERVICE_URL = '';

export function serviceEndpoint(serviceURL,path) {
  const service=new URL(serviceURL);
  if(service.protocol!=='https:'||service.username||service.password||service.search||service.hash||service.pathname!=='/')throw new Error('Atlas sign-in service is not configured correctly');
  return new URL(path,service.origin).href;
}

export function signInWithGitHub(serviceURL,{target=window,openWindow=(...args)=>window.open(...args)}={}) {
  if(!serviceURL)return Promise.reject(new Error('Atlas sign-in is awaiting service setup.'));
  const origin=new URL(serviceEndpoint(serviceURL,'/login')).origin;
  const attempt=Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
  return new Promise((resolve,reject)=>{
    let popup,closedTimer,timeout;
    const clear=()=>{target.removeEventListener('message',receive);clearInterval(closedTimer);clearTimeout(timeout);};
    const receive=event=>{
      if(event.origin!==origin||event.source!==popup||event.data?.attempt!==attempt)return;
      if(event.data?.type==='atlas-auth-success'&&typeof event.data.session==='string'&&event.data.session.length<=12000&&Number(event.data.expires)>Date.now()/1000) {
        clear();popup?.close();resolve(event.data.session);
      } else if(event.data?.type==='atlas-auth-error') {clear();popup?.close();reject(new Error(event.data.error||'GitHub sign-in did not complete'));}
    };
    target.addEventListener('message',receive);
    popup=openWindow(`${origin}/login?attempt=${attempt}`,'atlas-github-sign-in','popup,width=560,height=720');
    if(!popup){clear();reject(new Error('Allow the GitHub sign-in popup to refresh Atlas.'));return;}
    // A short grace period lets the success message arrive before observing popup closure.
    closedTimer=setInterval(()=>{if(popup.closed){clear();reject(new Error('GitHub sign-in was closed. Click Refresh data to retry.'));}},1000);
    timeout=setTimeout(()=>{clear();popup?.close();reject(new Error('GitHub sign-in expired. Click Refresh data to retry.'));},600000);
  });
}
