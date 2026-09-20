"""Dated calendars, not an annually repeated lunar-date guess or trading calendar."""
import datetime as dt
from fusion_engine import timestamp

CALENDAR_SOURCE = 'https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm'
CALENDAR_MIRROR = 'https://www.beijing.gov.cn/cs/gncs/zcwj/202603/t20260327_4568275.html'
PUBLISHED_AT = '2025-11-04T00:00:00+08:00'
VERIFIED_AT = '2026-09-05'
HOLIDAYS_2026 = (
    ('new_year', '元旦假期', '2026-01-01', '2026-01-03'),
    ('spring', '春节假期', '2026-02-15', '2026-02-23'),
    ('qingming', '清明假期', '2026-04-04', '2026-04-06'),
    ('labour', '劳动节假期', '2026-05-01', '2026-05-05'),
    ('dragon_boat', '端午假期', '2026-06-19', '2026-06-21'),
    ('mid_autumn', '中秋假期', '2026-09-25', '2026-09-27'),
    ('national', '国庆假期', '2026-10-01', '2026-10-07'),
)
THEMES = {
    'tourism': {'name': '旅游', 'industries': ['旅游服务', '旅游景点', '旅游综合'],
                'hypothesis': '假期出行关注可能升温，需核对客流、预订及价格反应'},
    'hotel': {'name': '酒店', 'industries': ['酒店餐饮', '酒店'],
              'hypothesis': '住宿关注可能升温，需核对入住率和房价；餐饮分类需另核主营'},
    'aviation': {'name': '航空客运', 'industries': ['航空运输', '空运'],
                 'hypothesis': '客运关注可能升温；需核对客运主营、票价和油价，不关联航空制造'},
    'film': {'name': '影视', 'industries': ['影视音像', '影视院线'],
             'hypothesis': '档期关注可能升温，需核对片单、预售及实际票房'},
    'food': {'name': '食品', 'industries': ['食品', '食品加工', '食品综合'],
             'hypothesis': '节庆消费关注可能升温，需核对主营关联、订单和库存'},
    'duty_free': {'name': '免税', 'industries': [],
                  'hypothesis': '需有免税业务依据才能映射公司，不把所有零售股视为免税股'},
}


def calendar_events(now, horizon=60, extra=()):
    """Returns only published, explicitly dated facts; gaps remain visible."""
    events = [dict(id='holiday:2026:'+key, name=name, start=start, end=end,
                   published_at=PUBLISHED_AT, source=CALENDAR_SOURCE,
                   themes=list(THEMES), kind='OFFICIAL_HOLIDAY', cancelled=False)
              for key, name, start, end in HOLIDAYS_2026]
    errors = []
    seen = {e['id'] for e in events}
    for raw in extra:
        try:
            event = dict(raw)
            if not event.get('id') or event['id'] in seen:
                raise ValueError('duplicate or missing event ID')
            if not event.get('name') or not str(event.get('source', '')).startswith('https://'):
                raise ValueError('name/source missing')
            if not event.get('themes') or any(t not in THEMES for t in event['themes']):
                raise ValueError('unknown theme')
            event.setdefault('end', event['start'])
            event.setdefault('kind', 'DATED_EVENT')
            event.setdefault('cancelled', False)
            events.append(event)
            seen.add(event['id'])
        except (ValueError, TypeError, KeyError):
            errors.append('自定义事件字段无效或ID重复')
    selected = []
    for event in events:
        try:
            start, end = dt.date.fromisoformat(event['start']), dt.date.fromisoformat(event['end'])
            published = timestamp(event.get('published_at'))
            if end < start or published is None:
                raise ValueError('invalid dates')
            if published > now or end < now.date() or (start-now.date()).days > horizon:
                continue
            selected.append(dict(event, days_to_start=(start-now.date()).days,
                                 phase='UPCOMING' if start > now.date() else 'IN_PROGRESS'))
        except (ValueError, TypeError, KeyError):
            errors.append('事件日期/发布时间无效')
    years = list(range(now.year, (now+dt.timedelta(days=horizon)).year+1))
    return dict(events=sorted(selected, key=lambda e:(e['start'], e['id'])),
                horizon_days=horizon, official_years=[2026],
                missing_calendar_years=[y for y in years if y != 2026], errors=errors,
                coverage='已维护的官方节假日和已录入事件，不代表全部未来催化事件',
                policy='节假日安排不是证券交易日历；主题关联是待验证假设，不是上涨预测')
