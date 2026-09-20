"""Presentation-only source and freshness disclosure; never a trading gate."""
from datetime import datetime, timezone, timedelta
from html import escape

TZ = timezone(timedelta(hours=8))


def describe(snapshot, market_state, fresh, now=None):
    now = now or datetime.now(TZ)
    snapshot = snapshot or {}
    observed = snapshot.get('observed_at')
    age = None
    try:
        parsed = datetime.fromisoformat(str(observed))
        if parsed.tzinfo is not None:
            age = round((now - parsed).total_seconds())
    except (ValueError, TypeError):
        pass
    return {
        'connection_mode': 'LIVE',
        'connection_label': '真实数据通道（不等于实时行情）',
        'freshness_state': market_state,
        'fresh_for_intraday_review': bool(fresh),
        'snapshot_trade_date': snapshot.get('trade_date'),
        'collection_received_at': observed,
        'source_trade_time': snapshot.get('source_trade_time'),
        'receipt_age_seconds': age,
        'page_generated_at': now.isoformat(timespec='seconds'),
        'receipt_age_is_quote_delay': False,
    }


def panel(snapshot, market_state, fresh):
    info = describe(snapshot, market_state, fresh)
    e = lambda value: escape(str(value if value not in (None, '') else '未提供'), quote=True)
    return (
        '<section class="pd-panel" id="fusion-data-freshness" role="status">'
        '<h2>数据来源与时效</h2><p>' + e(info['connection_label']) + '</p>'
        '<p>当前状态：' + e(market_state) + '</p>'
        '<p>行情日期：' + e(info['snapshot_trade_date']) +
        ' | 采集接收时间：' + e(info['collection_received_at']) + '</p>'
        '<p>行情源时间：' + e(info['source_trade_time']) + '</p>'
        '<p class="pd-note">页面刷新不代表行情更新；采集接收时间不等于交易所行情时间。'
        + ('盘中时效检查通过，不代表买入确认。' if fresh else '当前不满足盘中时效要求，不用此快照作实时执行判断。')
        + '</p></section>'
    )
