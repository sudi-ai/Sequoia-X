# -*- coding: utf-8 -*-
"""
A股机会雷达 V6.6 趋势结构分析模块（缠论辅助）
用途：
1. 判断趋势阶段
2. 辅助识别震荡中枢
3. 提示背驰风险
注意：仅作为辅助评分，不作为独立买卖依据
"""

def analyze_trend_structure(price_change_20=0, price_change_60=0,
                            volume_expand=False, high_position=False,
                            momentum_weaken=False):
    score = 50
    tags = []

    if price_change_20 > 10:
        score += 10
        tags.append("短期趋势向上")
    if volume_expand:
        score += 8
        tags.append("放量确认")
    if price_change_60 > 20:
        score += 5
        tags.append("中期趋势")

    if high_position:
        score -= 10
        tags.append("高位区域")

    if momentum_weaken:
        score -= 12
        tags.append("疑似背驰风险")

    if score >= 70:
        state = "趋势偏强"
    elif score >= 50:
        state = "震荡观察"
    else:
        state = "风险偏高"

    return {
        "trend_score": max(0, min(100, score)),
        "state": state,
        "tags": tags
    }
