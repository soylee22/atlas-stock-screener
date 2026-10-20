const values=new URLSearchParams(location.hash.slice(1));
// Remove the single-use handoff code immediately. It cannot be redeemed without the original tab's secret proof.
history.replaceState(null,'',location.pathname);
const attempt=values.get('attempt'),code=values.get('code');
if(/^[a-f0-9]{32}$/.test(attempt||'')&&/^[A-Za-z0-9_-]{43}$/.test(code||'')) {
  const channel=new BroadcastChannel('atlas-auth-'+attempt);
  channel.postMessage({attempt,code});
  document.querySelector('#sign-in-result').textContent='Sign-in is complete. You can return to Atlas.';
  setTimeout(()=>{channel.close();window.close();},1000);
} else document.querySelector('#sign-in-result').textContent='Sign-in could not be matched to Atlas. Close this tab and try Refresh data again.';
