"""Exchange-aware completion for Yahoo daily bars, with a publication allowance."""
from datetime import datetime, time, timezone
from functools import lru_cache
import exchange_calendars as calendars
import pandas as pd

CLOSE_ALLOWANCE = pd.Timedelta(minutes=30)
REGION_CALENDARS = dict(us='XNYS', gb='XLON', ca='XTSE', jp='XTKS', kr='XKRX', tw='XTAI',
                        de='XETR', es='XMAD', it='XMIL', nl='XAMS', dk='XCSE', se='XSTO')
EXCHANGE_CALENDARS = dict(LSE='XLON', LONDON='XLON', NYQ='XNYS', NYSE='XNYS',
    NMS='XNYS', NGM='XNYS', NCM='XNYS', NASDAQ='XNYS', ASE='XNYS', PCX='XNYS',
    TOR='XTSE', TSX='XTSE', JPX='XTKS', TYO='XTKS', KSC='XKRX', KOE='XKRX',
    TAI='XTAI', TWO='XTAI', GER='XETR', FRA='XFRA', HAM='XHAM', STU='XSTU',
    DUS='XDUS', MCE='XMAD', MIL='XMIL', AMS='XAMS', CPH='XCSE', STO='XSTO')


@lru_cache(maxsize=24)
def exchange_calendar(name):
    return calendars.get_calendar(name)


class SessionCutoff:
    def __init__(self, today=None, *, asof=None, metadata=None, region=None):
        # A supplied date represents the start of that date, preserving deterministic callers.
        self.asof = pd.Timestamp(asof or (datetime.combine(today, time(), timezone.utc)
                              if today else datetime.now(timezone.utc)))
        if self.asof.tzinfo is None:
            raise ValueError('Session cutoff requires a timezone-aware timestamp')
        meta = metadata or {}
        exchange = str(meta.get('exchangeName') or meta.get('exchange') or '').upper()
        name = EXCHANGE_CALENDARS.get(exchange) or REGION_CALENDARS.get(region)
        self.calendar = exchange_calendar(name) if name else None
        self.calendar_name = name
        self.today = self.asof.tz_convert(self.calendar.tz if self.calendar else 'UTC').date()

    def completed(self, history):
        frame = history.copy().sort_index()
        frame.index = pd.to_datetime(frame.index)
        if frame.index.tz is not None:
            frame.index = frame.index.tz_localize(None)  # Yahoo daily timestamps name local sessions.
        include_today = (self.calendar is not None and self.calendar.is_session(str(self.today))
                         and self.calendar.session_close(str(self.today)) + CLOSE_ALLOWANCE <= self.asof)
        return frame[(frame.index.date < self.today) |
                     ((frame.index.date == self.today) & include_today)]

    def week_complete(self, friday):
        friday = pd.Timestamp(friday).normalize()
        if self.calendar is None:
            return friday.date() < self.today
        sessions = self.calendar.sessions_in_range(friday - pd.Timedelta(days=4), friday)
        return not len(sessions) or self.calendar.session_close(sessions[-1]) + CLOSE_ALLOWANCE <= self.asof

    def latest_session(self):
        if self.calendar is None:
            return None, None
        session = self.calendar.date_to_session(str(self.today), direction='previous')
        ready = self.calendar.session_close(session) + CLOSE_ALLOWANCE
        if ready > self.asof:
            session = self.calendar.previous_session(session)
            ready = self.calendar.session_close(session) + CLOSE_ALLOWANCE
        return str(session.date()), ready

    def month_complete(self, month_end):
        end = pd.Timestamp(month_end).normalize()
        if self.calendar is None:
            return end.date() < self.today
        sessions = self.calendar.sessions_in_range(end.replace(day=1), end)
        return not len(sessions) or self.calendar.session_close(sessions[-1]) + CLOSE_ALLOWANCE <= self.asof

    def source_note(self, candles, start):
        if self.calendar is None:
            return 'Exchange calendar unknown. Current-day bars are excluded and period completion is conservative.'
        sessions = self.calendar.sessions_in_range(pd.Timestamp(start).normalize(), str(self.today))
        completed = [s for s in sessions if self.calendar.session_close(s) + CLOSE_ALLOWANCE <= self.asof]
        observed = set(candles.dropna(subset=['High','Low','Close']).index.normalize())
        missing = [str(s.date()) for s in completed if s not in observed]
        if not missing:
            return None
        shown = ', '.join(missing[:8]) + (' …' if len(missing)>8 else '')
        return f'Yahoo is missing {len(missing)} completed session(s): {shown}. Indicator uses available bars. Missing prices are not estimated.'
