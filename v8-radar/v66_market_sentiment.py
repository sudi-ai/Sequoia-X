# V6.6 Pro 市场情绪雷达
def calculate_market_sentiment(limit_up=0, limit_down=0, max_board=0, turnover_factor=0):
    score=50
    score += min(limit_up/5,20)
    score -= min(limit_down/3,15)
    score += min(max_board*3,15)
    score += min(turnover_factor,10)
    return max(0,min(100,round(score)))

def sentiment_state(score):
    if score>=80:return '强势周期'
    if score>=60:return '可交易'
    if score>=40:return '谨慎'
    return '防守'
