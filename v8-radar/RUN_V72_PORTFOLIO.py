from __future__ import annotations
from v7.env_loader import load_project_env
load_project_env()
from v7.portfolio import create_pending_fill,create_simulated,confirm_fill,reduce_position,list_positions,update_position,confirm_portfolio_push

def menu():
    while True:
        print('\n=== V7.2 我的持仓（Research Shadow）===')
        print('1 查看持仓  2 新增待确认成交  3 确认成交/加仓  4 部分减仓/清仓  5 修改备注/防守价  6 新增模拟仓  7 确认/关闭Portfolio微信  0 退出')
        x=input('请选择：').strip()
        try:
            if x=='1':
                for p in list_positions(): print(p)
            elif x=='2': print(create_pending_fill(input('股票代码：'),input('股票名称：'),input('备注：')))
            elif x=='3': print(confirm_fill(input('position_id：'),float(input('成交股数：')),float(input('成交价：'))))
            elif x=='4': print(reduce_position(input('position_id：'),float(input('卖出股数：')),float(input('成交价：'))))
            elif x=='5': print(update_position(input('position_id：'),manual_stop_price=float(input('防守价：') or 0) or None,notes=input('备注：')))
            elif x=='6': print(create_simulated(input('股票代码：'),input('股票名称：'),float(input('股数：')),float(input('模拟成本：'))))
            elif x=='7':
                ok=input('输入 YES 明确允许V7.2 Shadow持仓群推送；其他输入关闭：').strip()=='YES'; confirm_portfolio_push(ok); print('已设置：',ok)
            elif x=='0': return 0
        except Exception as e: print('操作失败：',type(e).__name__,str(e))
if __name__=='__main__': raise SystemExit(menu())
