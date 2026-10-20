# Atlas GitHub sign-in service

The Worker handles GitHub App sign-in and starts the existing Atlas cloud workflow.
Only GitHub user 73914454 can create a refresh session.
The App must have Actions read and write permission and be installed only on atlas-stock-screener.

GitHub credentials stay inside the Worker.
Atlas receives an encrypted one-hour session through a same-origin callback bridge.
Redemption requires a secret held by the original tab and a separate single-use callback code.
The session stays in tab memory and clears on reload or close.
Public job progress works without signing in.

## Account setup

1. Sign into Cloudflare with Wrangler through the normal account approval page.
2. Deploy this directory and record its HTTPS workers.dev address.
3. Register a private GitHub App with the Worker /callback address and no active webhook.
4. Give the App Actions read and write permission and install it only on Atlas.
5. Store GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET and SESSION_KEY in the Worker configuration.
6. Generate SESSION_KEY as 32 random bytes encoded with base64url.
7. Set REFRESH_SERVICE_URL in static/refresh-auth.js to the deployed service origin.
8. Check service readiness, publish the configured frontend, then verify owner sign-in and a real dispatch.

Keep credential material in an ignored private directory.
Never print credentials or commit development secrets.
Do not deploy the frontend with an empty REFRESH_SERVICE_URL.

## Checks

Run node --test auth-service/test-worker.mjs.
Run the frontend tests under tests/test_refresh_auth.mjs and tests/test_refresh_progress.mjs.
Use wrangler deploy --dry-run to check the deployment bundle before account setup.

The /health endpoint reports configuration readiness without returning credentials.
The API accepts only the Atlas origin and a valid encrypted session.
The proxy exposes fixed run-status and refresh operations for Atlas only.
Dispatch always uses main with intraday enabled and reuses an active run.

## Current setup state

The implementation and local security checks are complete.
The Worker is deployed at https://atlas-refresh-auth.econd-rain.workers.dev.
GitHub App registration and the real sign-in check remain pending.
The published site retains its previous access method until the new service is verified.
