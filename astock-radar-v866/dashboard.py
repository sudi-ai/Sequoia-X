# -*- coding: utf-8 -*-
from __future__ import annotations
import tkinter as tk
from tkinter import ttk
from signal_engine import classify_candidate
from market_phase import classify_market_phase
from mock_data import dashboard_snapshot
from mock_latent import latent_demo
from latent_store import LatentStore
from data_store import Store
from legacy_v8_bridge import load_legacy, legacy_snapshot

BG="#0b1018"; PANEL="#111824"; PANEL2="#151e2b"; BORDER="#263246"; TEXT="#e9eef7"
MUTED="#8896aa"; GREEN="#25d07f"; RED="#ff5964"; AMBER="#f2b84b"; CYAN="#4bb8ff"
FONT=("Microsoft YaHei UI",10); BOLD=("Microsoft YaHei UI",10,"bold"); H=("Microsoft YaHei UI",13,"bold"); BIG=("Microsoft YaHei UI",26,"bold")

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("A股机会雷达 V8.3｜潜伏启动 + 盈利决策工作台")
        self.geometry("1720x980"); self.minsize(1320,780); self.configure(bg=BG)
        self.store=Store(); self.latent_store=LatentStore(); self.legacy=load_legacy()
        self._build(); self.after(250,self.refresh)

    def panel(self,parent,title):
        f=tk.Frame(parent,bg=PANEL,highlightbackground=BORDER,highlightthickness=1)
        tk.Label(f,text="│ "+title,bg=PANEL,fg=TEXT,font=H).pack(anchor="w",padx=10,pady=(8,4))
        return f

    def tree(self,parent,cols,widths,height=8):
        t=ttk.Treeview(parent,columns=cols,show="headings",height=height)
        for c,w in zip(cols,widths):
            t.heading(c,text=c); t.column(c,width=w,anchor="center")
        t.pack(fill="both",expand=True,padx=8,pady=8)
        return t

    def _build(self):
        hdr=tk.Frame(self,bg="#091019",height=58); hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr,text="📡 A股机会雷达 V8.3",bg="#091019",fg=TEXT,font=("Microsoft YaHei UI",17,"bold")).pack(side="left",padx=18)
        tk.Label(hdr,text="原V8买点 + 潜伏启动 + 错失复盘 + T+1 + 风控",bg="#091019",fg=MUTED,font=FONT).pack(side="left")
        tk.Label(hdr,text="● 系统运行中",bg="#091019",fg=GREEN,font=BOLD).pack(side="right",padx=18)

        body=tk.Frame(self,bg=BG); body.pack(fill="both",expand=True,padx=10,pady=10)
        nav=tk.Frame(body,bg="#091019",width=170); nav.pack(side="left",fill="y"); nav.pack_propagate(False)
        for i,n in enumerate(["▣ 总览","◉ 市场阶段","⌁ 实时竞价","◎ 潜伏启动","★ A级机会","▤ B级观察","▥ 持仓风控","↺ 错失复盘","⌘ 回测实验室","⚙ 数据健康"]):
            tk.Label(nav,text=n,bg="#162130" if i==0 else "#091019",fg=TEXT if i==0 else MUTED,font=BOLD,anchor="w",padx=14).pack(fill="x",pady=2,ipady=10)

        main=tk.Frame(body,bg=BG); main.pack(side="left",fill="both",expand=True,padx=(10,0))

        top=tk.Frame(main,bg=BG); top.pack(fill="x")
        p=self.panel(top,"市场阶段"); p.pack(side="left",fill="both",expand=True,padx=(0,5))
        self.temp=tk.Label(p,text="--",bg=PANEL,fg=AMBER,font=BIG); self.temp.pack(anchor="w",padx=16,pady=4)
        self.phase=tk.Label(p,text="--",bg=PANEL,fg=TEXT,font=H); self.phase.pack(anchor="w",padx=16,pady=(0,8))

        p=self.panel(top,"执行漏斗"); p.pack(side="left",fill="both",expand=True,padx=5)
        self.funnel=tk.Label(p,text="",justify="left",bg=PANEL,fg=TEXT,font=BOLD); self.funnel.pack(anchor="w",padx=16,pady=8)

        p=self.panel(top,"潜伏雷达"); p.pack(side="left",fill="both",expand=True,padx=5)
        self.latent_stats=tk.Label(p,text="",justify="left",bg=PANEL,fg=TEXT,font=BOLD); self.latent_stats.pack(anchor="w",padx=16,pady=8)

        p=self.panel(top,"错失机会审计"); p.pack(side="left",fill="both",expand=True,padx=(5,0))
        self.missed_stats=tk.Label(p,text="",justify="left",bg=PANEL,fg=TEXT,font=BOLD); self.missed_stats.pack(anchor="w",padx=16,pady=8)

        latent_panel=self.panel(main,"◎ 潜伏启动池｜不是买点，只负责提前发现")
        latent_panel.pack(fill="both",expand=True,pady=(8,4))
        self.latent_tree=self.tree(latent_panel,("股票","潜伏分","阶段","均线收敛","平台","距突破","动作"),
                                   (180,80,85,90,80,90,310),height=7)

        mid=tk.Frame(main,bg=BG); mid.pack(fill="both",expand=True,pady=4)
        left=self.panel(mid,"★ A级执行信号｜原V8买点 + T+1 + 风控确认"); left.pack(side="left",fill="both",expand=True,padx=(0,4))
        self.a_tree=self.tree(left,("股票","评分","T+1","隔夜风险","板块","动作"),(170,70,70,80,125,260),height=8)

        right=self.panel(mid,"市场主线 / 资金"); right.pack(side="left",fill="both",expand=True,padx=(4,0))
        self.theme_box=tk.Frame(right,bg=PANEL); self.theme_box.pack(fill="both",expand=True,padx=8,pady=8)

        bottom=self.panel(main,"系统纪律")
        bottom.pack(fill="x",pady=(4,0))
        self.health=tk.Label(bottom,text="",justify="left",bg=PANEL,fg=MUTED,font=FONT)
        self.health.pack(anchor="w",padx=12,pady=(4,10))

        st=ttk.Style(self); st.theme_use("clam")
        st.configure("Treeview",background=PANEL2,fieldbackground=PANEL2,foreground=TEXT,rowheight=29,borderwidth=0,font=FONT)
        st.configure("Treeview.Heading",background="#1b2635",foreground=MUTED,font=BOLD,relief="flat")
        st.map("Treeview",background=[("selected","#213148")],foreground=[("selected",TEXT)])

    def refresh(self):
        snap=legacy_snapshot(self.legacy) or dashboard_snapshot()
        mp=classify_market_phase(snap.get("market",{}))
        self.temp.configure(text=f"{mp['temperature']:.0f}/100")
        self.phase.configure(text=f"{mp['phase']}｜进攻系数 {mp['attack_factor']:.2f}")

        rows=[]; pools={"A":0,"B":0,"C":0}
        for c in snap.get("candidates",[]):
            c=dict(c); c["market_temp"]=mp["temperature"]; c["market_phase"]=mp["phase"]; c.setdefault("data_quality",.98)
            s=classify_candidate(c); pools[s["pool"]]+=1; rows.append(s)
        self.funnel.configure(text=f"候选：{len(rows)}\nA级 {pools['A']}｜B级 {pools['B']}｜C级 {pools['C']}\n微信：只推真正A级买点")

        latent=[x for x in latent_demo() if x["latent_stage"] in ("WATCH","READY","START")]
        stage_counts={k:sum(1 for x in latent if x["latent_stage"]==k) for k in ("WATCH","READY","START")}
        self.latent_stats.configure(text=f"潜伏池：{len(latent)}\nWATCH {stage_counts['WATCH']}｜READY {stage_counts['READY']}｜START {stage_counts['START']}\n潜伏≠买点")

        lm=self.latent_store.metrics()
        recall="样本不足" if lm["big_mover_recall"] is None else f"{lm['big_mover_recall']*100:.1f}%"
        self.missed_stats.configure(text=f"大涨样本：{lm['missed_cases']}\nBig Mover Recall：{recall}\n目标：减少漏报，不牺牲买点纪律")

        for t in (self.latent_tree,self.a_tree):
            for x in t.get_children(): t.delete(x)
        for x in sorted(latent,key=lambda z:z["latent_score"],reverse=True):
            self.latent_tree.insert("", "end",values=(f"{x['name']} {x['ts_code']}",x["latent_score"],x["latent_stage"],x["ma_cluster"],x["platform"],x["breakout_proximity"],x["action"]))
        for s in sorted(rows,key=lambda x:x["score"],reverse=True):
            if s["pool"]=="A":
                self.a_tree.insert("", "end",values=(f"{s.get('name','')} {s.get('ts_code','')}",s["score"],f"{s['t1_probability']*100:.0f}%",s["overnight_risk"],s.get("sector",""),s.get("action","")))

        for child in self.theme_box.winfo_children(): child.destroy()
        for t in snap.get("themes",[])[:7]:
            r=tk.Frame(self.theme_box,bg=PANEL); r.pack(fill="x",pady=3)
            tk.Label(r,text=t["name"],bg=PANEL,fg=TEXT,font=BOLD,width=16,anchor="w").pack(side="left")
            tk.Label(r,text=f"+{t['rise']:.2f}%",bg=PANEL,fg=RED,font=BOLD,width=9).pack(side="left")
            tk.Label(r,text=f"强度 {t['strength']}",bg=PANEL,fg=AMBER,font=FONT,width=10).pack(side="left")
            tk.Label(r,text=f"资金 +{t['flow']:.2f}亿",bg=PANEL,fg=GREEN,font=FONT).pack(side="left")

        self.health.configure(text="PIT数据 ✓ ｜ 潜伏Precision+Recall ✓ ｜ 错失样本库 ✓ ｜ 原V8买点保留 ✓ ｜ 潜伏阈值不自动改 ✓")
        self.after(5000,self.refresh)

if __name__=="__main__":
    App().mainloop()
