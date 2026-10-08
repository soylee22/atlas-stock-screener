"""Run checkpointable nightly Yahoo batches on a GitHub runner."""
import argparse
import logging
import math
import os
import time

import app as model
import refresh_data


def cloud_batch(store, part, seconds=14400, limit=30000, force_quotes=False):
    run_id = os.environ.get('GITHUB_RUN_ID', 'local')
    if part == '1':
        state = dict(run_id=run_id, started=model.now_iso(), phase='profiles_1',
            financials=dict(attempted=0, succeeded=0, failed=0),
            quotes_attempted=False, quotes_succeeded=None, rate_limited=False,
            cooldowns=0, rate_limits_seen=False)
    else:
        state = store.meta('cloud_refresh', {})
        if state.get('run_id') != run_id:
            raise ValueError('The cloud checkpoint belongs to another run')
    store.set_meta('cloud_refresh', state)
    if part in {'1', '2'}:
        first = part == '1'
        batch_seconds = seconds//2 if first else seconds-seconds//2
        batch_limit = limit//2 if first else limit-limit//2
        deadline=time.monotonic()+batch_seconds
        attempted=0
        first_request=True
        while attempted < batch_limit and time.monotonic()<deadline:
            if state['rate_limited']:
                if deadline-time.monotonic() <= 300:
                    break
                state['cooldowns'] += 1
                store.set_meta('cloud_refresh',state)
                print('Yahoo rate limit: cooling down for five minutes before resuming',flush=True)
                time.sleep(300)
            remaining=max(1,math.ceil(deadline-time.monotonic()))
            result = refresh_data.refresh(store, model.ROOT/'seed'/'bootstrap.tar.gz',
                seconds=remaining, limit=batch_limit-attempted,
                force_quotes=force_quotes and first and first_request, workers=2)
            first_request=False
            attempted += result['financials']['attempted']
            for key, value in result['financials'].items():
                state['financials'][key] += value
            if result['quotes_attempted']:
                state.update(quotes_attempted=True, quotes_succeeded=result['quotes_succeeded'])
            state['rate_limited'] = result['stop_reason'] == 'rate_limited'
            state['rate_limits_seen'] |= state['rate_limited']
            state['stop_reason'] = result['stop_reason']
            store.set_meta('cloud_refresh',state)
            if not state['rate_limited']:
                break
        state['phase'] = 'profiles_'+part
    elif part == 'history':
        if not state['rate_limited']:
            growth = refresh_data.backfill_growth(store, seconds=min(1200, seconds), limit=30000, workers=4)
            state['rate_limited'] = growth.get('rate_limited', False)
            if not state['rate_limited']:
                refresh_data.backfill_statements(store, seconds=min(60, seconds), limit=20)
                refresh_data.backfill_technicals(store, seconds=min(60, seconds), limit=40)
        state['phase'] = 'finished'
        state['finished'] = model.now_iso()
    state['remaining_eligible'] = len(store.enrichment_candidates())
    store.set_meta('cloud_refresh', state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--part', required=True, choices=['1', '2', 'history'])
    parser.add_argument('--seconds', type=int, default=14400)
    parser.add_argument('--limit', type=int, default=30000)
    parser.add_argument('--force-quotes', action='store_true')
    args = parser.parse_args()
    if not 2 <= args.seconds <= 14400 or not 0 <= args.limit <= 30000:
        parser.error('Use 2 to 14400 seconds and 0 to 30000 profiles')
    logging.basicConfig(level=logging.INFO)
    cloud_batch(model.Store(), args.part, args.seconds, args.limit, args.force_quotes)


if __name__ == '__main__':
    main()
