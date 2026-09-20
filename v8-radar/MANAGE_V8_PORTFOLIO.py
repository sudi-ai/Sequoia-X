from __future__ import annotations

from v8.env_loader import load_v8_env
load_v8_env()
from v8.portfolio import close_position,list_positions,upsert_position


def main():
    while True:
        print("\n=== V8 Research Shadow 持仓管理 ===")
        print("1 查看持仓  2 新增/更新持仓  3 确认减仓/清仓成交  0 退出")
        choice=input("请选择：").strip()
        try:
            if choice=="1":
                for row in list_positions():print(row)
            elif choice=="2":
                code=input("股票代码：").strip(); name=input("股票名称：").strip()
                shares=float(input("持股数量：")); cost=float(input("真实成本价："))
                stop_text=input("手动防守价（可空）：").strip(); notes=input("备注（可空）：").strip()
                print("已保存：",upsert_position(code,name,shares,cost,manual_stop_price=float(stop_text) if stop_text else None,notes=notes))
            elif choice=="3":
                pid=input("position_id：").strip();price=float(input("真实卖出成交价："));amount=input("卖出股数（直接回车=全部）：").strip()
                reason=input("卖出原因（止损/止盈/趋势破坏/人工）：").strip() or 'MANUAL_EXIT'
                close_position(pid,exit_price=price,reason=reason,shares=float(amount) if amount else None);print("真实退出记录已保存")
            elif choice=="0":return 0
        except Exception as exc:print("操作失败：",type(exc).__name__,str(exc))


if __name__=="__main__":raise SystemExit(main())
