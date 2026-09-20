# V6.6 Pro 板块生命周期辅助
def sector_cycle(score, days_up=0):
    if score>=85 and days_up>=5:
        return '高潮风险'
    if score>=70:
        return '趋势延续'
    if score>=55:
        return '观察启动'
    return '退潮观察'
