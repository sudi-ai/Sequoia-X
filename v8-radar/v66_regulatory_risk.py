# V6.6 监管风险预警模块
# 用于辅助判断短线强势股是否处于高波动/高风险区域

def regulatory_risk_score(data: dict):
    """
    data:
    {
      "change_5d": float,
      "change_10d": float,
      "change_20d": float,
      "limit_up_count": int,
      "deviation_ma20": float,
      "profit_ratio": float
    }
    """
    score = 0
    reasons = []

    if data.get("change_5d", 0) > 30:
        score += 15
        reasons.append("5日涨幅过快")
    if data.get("change_10d", 0) > 50:
        score += 20
        reasons.append("10日累计涨幅较高")
    if data.get("change_20d", 0) > 80:
        score += 20
        reasons.append("20日涨幅过高")
    if data.get("limit_up_count", 0) >= 3:
        score += 15
        reasons.append("连续涨停风险")
    if data.get("deviation_ma20", 0) > 15:
        score += 15
        reasons.append("偏离20日线较大")
    if data.get("profit_ratio", 0) > 80:
        score += 15
        reasons.append("获利盘集中")

    score = min(score, 100)

    if score >= 60:
        level = "高风险"
    elif score >= 35:
        level = "中风险"
    else:
        level = "低风险"

    return {
        "regulatory_risk_score": score,
        "level": level,
        "reasons": reasons
    }
