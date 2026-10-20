import {REFRESH_SERVICE_URL, serviceEndpoint, signInWithGitHub} from './refresh-auth.js';
const ROOT = 'https://api.github.com/repos/soylee22/atlas-stock-screener';
export const WORKFLOW_URL = 'https://github.com/soylee22/atlas-stock-screener/actions/workflows/pages.yml';
const ACTIVE = new Set(['queued', 'in_progress', 'waiting', 'pending', 'requested']);
export const activeRun = run => !!run && ACTIVE.has(run.status);

export function progressState(run, jobs = []) {
  if (!run) return { label: 'No cloud refresh found', value: null };
  if (run.status === 'completed') {
    if (run.conclusion === 'success') return { label: 'Published. Checking the website…', value: null, published: true };
    return { label: `Refresh ${run.conclusion || 'failed'}. Previous snapshot retained.`, value: null, failed: true };
  }
  const running = jobs.find(job => job.status === 'in_progress');
  const failed = jobs.find(job => job.status === 'completed' && !['success', 'skipped'].includes(job.conclusion));
  if (failed) return { label: `${failed.name} ${failed.conclusion}. Previous snapshot retained.`, value: null, failed: true };
  if (!running) return { label: 'Queued on GitHub. Waiting for a runner or publication.', value: null };
  const steps = running.steps || [];
  const done = steps.filter(step => step.status === 'completed').length;
  const step = steps.find(step => step.status === 'in_progress');
  return {
    label: `${running.name === 'deploy' ? 'Publishing' : 'Refreshing'}: ${step?.name || running.name}`,
    detail: steps.length ? `${done} of ${steps.length} ${running.name} steps complete. Steps vary in duration.` : '',
    value: steps.length ? done / steps.length : null,
  };
}

export function createCloudClient({ fetcher = fetch, getToken = () => '', serviceURL = REFRESH_SERVICE_URL } = {}) {
  async function request(path, options = {}) {
    const token = getToken();
    let target = `${ROOT}${path}`;
    if(token) {
      let route;
      if(path.startsWith('/actions/workflows/pages.yml/runs?'))route='/api/latest';
      else if(path==='/actions/workflows/pages.yml/dispatches')route='/api/refresh';
      else route=path.replace('/actions/runs/','/api/runs/').replace('?per_page=100','');
      target=serviceEndpoint(serviceURL,route);
    }
    if(options.method==='POST'&&!token)throw new Error('Sign in with GitHub to start a cloud refresh.');
    const response = await fetcher(target, {
      ...options, cache: 'no-store', headers: {
        Accept: token ? 'application/json' : 'application/vnd.github+json',
        ...(!token ? {'X-GitHub-Api-Version': '2026-03-10'} : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      }, signal: AbortSignal.timeout(20000),
    });
    if (!response.ok) {
      if (response.status === 401) throw new Error('GitHub access expired or was rejected. Reconnect to retry.');
      if (response.status === 403 || response.status === 429) throw new Error('GitHub access is restricted or its request limit was reached. Retry later.');
      throw new Error(`GitHub could not complete the request (${response.status}).`);
    }
    return response.status === 204 ? null : response.json();
  }
  return {
    async latest() {
      const data = await request('/actions/workflows/pages.yml/runs?branch=main&per_page=10');
      return data.workflow_runs.find(activeRun) || data.workflow_runs[0] || null;
    },
    async run(id) { return request(`/actions/runs/${Number(id)}`); },
    async jobs(id) { return (await request(`/actions/runs/${Number(id)}/jobs?per_page=100`)).jobs; },
    async dispatch() { return request('/actions/workflows/pages.yml/dispatches', {
      method: 'POST', body: JSON.stringify({ ref: 'main', inputs: { intraday: true } }),
    }); },
  };
}

export function installRefreshProgress({ button, onPublished, getSnapshotBuilt }) {
  let token = '', timer, checking = false, starting = false, run, submitted = false;
  let before = getSnapshotBuilt(), confirmedRun, trackedId, signInController;
  const client = createCloudClient({ getToken: () => token });
  const panel = document.createElement('section');
  panel.className = 'cloud-refresh'; panel.setAttribute('aria-label', 'Cloud refresh progress');
  panel.innerHTML = '<div class="cloud-refresh-summary"><strong>Cloud refresh</strong><span class="cloud-refresh-label" role="status" aria-live="polite">Checking GitHub…</span><a target="_blank" rel="noopener">View run ↗</a></div><progress aria-label="Cloud refresh job steps"></progress><small class="cloud-refresh-detail"></small>';
  button.closest('.heading-row').after(panel);
  const label = panel.querySelector('.cloud-refresh-label'), bar = panel.querySelector('progress');
  const detail = panel.querySelector('small'), link = panel.querySelector('a');
  const cancelSignIn=document.createElement('button');cancelSignIn.className='button secondary';cancelSignIn.textContent='Cancel sign-in';cancelSignIn.hidden=true;panel.append(cancelSignIn);
  cancelSignIn.onclick=()=>signInController?.abort();
  function render(state) {
    label.textContent = state.label; detail.textContent = state.detail || '';
    if (state.value == null) bar.removeAttribute('value'); else { bar.max = 1; bar.value = state.value; }
    bar.hidden = !!state.failed;
    link.href = run?.html_url || WORKFLOW_URL;
    button.disabled = starting || submitted || activeRun(run);
    button.title = activeRun(run) ? 'A cloud refresh is already queued or running' : 'Start a cloud market-data refresh';
  }
  async function check() {
    if (checking) return;
    checking = true;
    try {
      const latest = trackedId ? await client.run(trackedId) : await client.latest();
      if (submitted && latest?.id === run?.id) {
        render({ label: 'Refresh requested. Waiting for GitHub to register the run.', value: null });
        return;
      }
      if (submitted) submitted = false;
      run = latest;
      const state = progressState(run, activeRun(run) ? await client.jobs(run.id) : []);
      render(state);
      if (state.published && confirmedRun !== run.id) {
        await onPublished();
        const built = getSnapshotBuilt();
        if (built && (built !== before || Date.parse(built) >= Date.parse(run.run_started_at || run.created_at))) {
          confirmedRun = run.id; before = built; trackedId = null;
          render({ label: `Up to date. Snapshot ${new Date(built).toLocaleString('en-GB')}`, value: 1 });
        } else render({ label: 'Run succeeded. Waiting for the new website snapshot.', value: null });
      } else if (confirmedRun === run?.id) render({ label: `Up to date. Snapshot ${new Date(before).toLocaleString('en-GB')}`, value: 1 });
    } catch (error) {
      render({ label: 'Live status unavailable. The last snapshot remains available.', detail: error.message, failed: true });
    } finally {
      checking = false;
      clearTimeout(timer);
      timer = setTimeout(() => { if (!document.hidden) check(); else schedule(); }, token ? 20000 : 180000);
    }
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(check, token ? 20000 : 180000); }

  button.onclick = async () => {
    if (starting || submitted) return;
    starting = true;
    render({label:token?'Checking for an existing refresh…':'Sign in with GitHub to start the refresh…',value:null});
    try {
      if (!token) {
        signInController=new AbortController();cancelSignIn.hidden=false;
        try {token=await signInWithGitHub(REFRESH_SERVICE_URL,{signal:signInController.signal});}
        finally {cancelSignIn.hidden=true;signInController=undefined;}
      }
      run = await client.latest();
      if (activeRun(run)) { render(progressState(run, await client.jobs(run.id))); return; }
      before = getSnapshotBuilt(); confirmedRun = undefined;
      const response = await client.dispatch();
      submitted = true;
      render({label:'Refresh requested. Waiting for GitHub to register the run.',value:null});
      if (response?.workflow_run_id) {
        trackedId = response.workflow_run_id;
        run = {id:trackedId,status:'queued',html_url:response.html_url};
        submitted = false;
      }
    } catch(error) {
      token = '';
      render({label:'Refresh was not confirmed. Check the run before retrying.',detail:error.message,failed:true});
    } finally {
      starting = false;
      button.disabled = submitted || activeRun(run);
      schedule();
    }
  };
  const visibility = () => { if (!document.hidden) schedule(); };
  document.addEventListener('visibilitychange', visibility);
  check();
  return { check, client, render, setToken(value) { token = value; }, getRun: () => run };
}
