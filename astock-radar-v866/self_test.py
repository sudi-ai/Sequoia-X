# -*- coding: utf-8 -*-
"""V8.6 全量离线自检：不调用真实付费行情，不消耗接口额度。"""
import importlib, tempfile, time, math
from pathlib import Path

MODULES=[
"config","tushare_client","data_store","market_phase","auction_engine","t1_model",
"chip_engine","risk_veto","signal_engine","backtest_lab","legacy_v8_bridge","wecom_push",
"mock_data","event_bus","provider_guard","data_provenance","feature_store","signal_lifecycle",
"pit_store","paid_data_hub","live_runtime","live_dashboard","live_data_verifier",
"strategy_registry","portfolio_risk","calibration","walkforward","experiment_registry","deep_review",
"latent_startup","latent_store","latent_runtime","missed_opportunity","missed_review_runner",
"latent_metrics","threshold_advisor","daily_scheduler","mock_latent","latent_paid_features","latent_auction_confirm","latent_wecom","v83_pipeline",
"limitup_data_adapter","limitup_ecology","first_board_potential","seal_quality","limitup_store",
"first_board_training","limitup_runtime","limitup_wecom","board_model_audit","mainline_rotation","limitup_pipeline","mock_limitup","v84_pipeline","v84_scheduler",
"factor_lab","alpha_ranker","regime_router","bootstrap_confidence","execution_simulator","monte_carlo","model_drift",
"model_governance","stat_learning_pipeline","research_dataset","paper_trade_guard","mock_stat_learning","v85_pipeline",
"triple_barrier","sample_uniqueness","purged_cv","meta_labeler","probability_calibrator","conformal_selective",
"deflated_sharpe","meta_dataset","cpcv_meta_validation","precision_sniper","precision_pipeline","mock_precision"
]

def test_imports():
    for m in MODULES: importlib.import_module(m)

def test_market():
    from market_phase import classify_market_phase
    x=classify_market_phase({"advance_rate":62,"limit_up":96,"limit_down":5,"broken_rate":14,"advance_height":5})
    assert x["phase"] in ("启动","主升","高潮"),x
    assert 0<=x["temperature"]<=100

def test_signal_a():
    from signal_engine import classify_candidate
    f={"daily_pct":3.2,"market_temp":72,"sector_strength":95,"auction_quality":88,
       "main_flow_score":82,"chip_lock_score":84,"close_strength":82,"event_risk":3,
       "distribution_risk":5,"trend_score":94,"buy_timing_score":93,"rr":2.8,
       "market_phase":"主升","data_quality":.98}
    x=classify_candidate(f)
    assert x["pool"]=="A",x

def test_signal_veto():
    from signal_engine import classify_candidate
    f={"daily_pct":3,"market_temp":80,"sector_strength":95,"auction_quality":95,
       "main_flow_score":90,"chip_lock_score":90,"close_strength":90,"event_risk":2,
       "distribution_risk":5,"trend_score":95,"buy_timing_score":95,"rr":3,
       "market_phase":"主升","regulatory_risk":90,"data_quality":1}
    x=classify_candidate(f)
    assert x["pool"]=="C" and "监管" in x["veto"],x

def test_pit():
    from data_provenance import observed,pit_safe
    x=observed(88,"test",observed_at="2026-09-02T09:30:00.000")
    assert pit_safe({"x":x},"2026-09-02T09:31:00.000")==[]
    assert pit_safe({"x":x},"2026-09-02T09:29:00.000")

def test_event_bus():
    from event_bus import EventBus
    got=[]; b=EventBus().start()
    b.on("SIGNAL",lambda e:got.append(e.data)); b.emit("SIGNAL",{"x":1})
    time.sleep(.15);b.stop()
    assert got and got[0]["x"]==1

def test_provider_guard():
    from provider_guard import ProviderGuard
    g=ProviderGuard(fail_threshold=2,cooldown_seconds=1)
    assert g.call("ok",lambda:7)==7
    g.call("bad",lambda:1/0);g.call("bad",lambda:1/0)
    assert g.snapshot()["bad"]["circuit_open"] is True

def test_portfolio_risk():
    from portfolio_risk import evaluate_order
    p={"nav":100000,"exposure_pct":.2,"positions_count":2,"daily_pnl_pct":0,"sector_exposure":{}}
    assert evaluate_order({"value":10000,"stop_loss_pct":4,"sector":"AI"},p)["allowed"]
    assert not evaluate_order({"value":30000,"stop_loss_pct":10,"sector":"AI"},p)["allowed"]

def test_calibration():
    from calibration import reliability_bins,brier_score
    p=[.8,.8,.2,.2]; y=[1,1,-1,1]
    assert reliability_bins(p,y)
    assert 0<=brier_score(p,y)<=1

def test_walkforward():
    from walkforward import windows
    ws=list(windows(list(range(30)),10,5,5))
    assert len(ws)==4 and ws[0][0][-1] < ws[0][1][0]

def test_backtest():
    from backtest_lab import evaluate
    s=evaluate([2,-1,3,1,-.5,2])
    assert s["n"]==6 and s["expectancy"]>0

def _latent_kline():
    rows=[]
    for i in range(70):
        if i<45:
            p=6.50+i*.012+math.sin(i/4)*.025; spread=.12; vol=150000-500*i
        else:
            p=7.43+(i-45)*.006+math.sin(i/3)*.02; spread=.07; vol=max(52000,85000-(i-45)*950)
        if i==69:p=7.58;vol=95000
        rows.append({"date":f"20260{6+i//28}{(i%28)+1:02d}","open":p-.02,"high":p+spread/2,"low":p-spread/2,"close":p,"volume":vol})
    return rows

def test_latent_radar():
    from latent_startup import analyze_kline
    x=analyze_kline(_latent_kline(),{"chip_lock_score":75,"accumulation_score":68,"sector_heat":58,
        "event_risk":5,"unlock_risk":3,"distribution_risk":8,"data_quality":.98})
    assert x["latent_stage"] in ("WATCH","READY","START"),x
    assert x["latent_score"]>=68,x
    assert x["is_buy_signal"] is False

def test_latent_store_and_runtime():
    from latent_store import LatentStore
    from latent_runtime import LatentRuntime
    p=Path(tempfile.gettempdir())/"latent_v83_test.db";p.unlink(missing_ok=True)
    s=LatentStore(p)
    r=LatentRuntime(store=s).evaluate("601086.SH","国芳型测试",_latent_kline(),
       {"chip_lock_score":75,"accumulation_score":68,"sector_heat":58,"data_quality":.98})
    assert r["latent_stage"] in ("WATCH","READY","START")
    assert s.metrics()["latent_signals"]==1
    p.unlink(missing_ok=True)

def _big_mover_history():
    rows=_latent_kline()
    p=rows[-1]["close"]
    for j in range(10):
        p*=1.028
        rows.append({"date":f"202609{j+1:02d}","open":p*.99,"high":p*1.015,"low":p*.985,"close":p,"volume":120000+j*8000})
    return rows

def test_missed_opportunity():
    from latent_store import LatentStore
    from missed_opportunity import review_case
    p=Path(tempfile.gettempdir())/"missed_v83_test.db";p.unlink(missing_ok=True)
    s=LatentStore(p)
    x=review_case("601086.SH","错失测试",_big_mover_history(),store=s)
    assert x is not None,x
    assert (x["ret_5d"] or 0)>=12 or (x["ret_10d"] or 0)>=20
    assert x["had_latent"] is False
    assert x["miss_reason"]
    assert s.metrics()["missed_cases"]==1
    p.unlink(missing_ok=True)

def test_precision_recall_guard():
    from latent_metrics import evaluate
    from threshold_advisor import advise
    m=evaluate(
       [{"future_5d":10,"lead_days":3},{"future_5d":-2,"lead_days":None},{"future_5d":12,"lead_days":2}],
       [{"had_latent":True},{"had_latent":False},{"had_latent":True}])
    assert m["precision"] is not None and m["recall"] is not None
    a=advise(m,min_samples=50)
    assert a["action"]=="KEEP" and "样本" in a["reason"]


def test_paid_feature_fusion():
    from latent_paid_features import moneyflow_accumulation_score,liquidity_score,build_extra
    flows=[{"net_mf_amount":10},{"net_mf_amount":8},{"net_mf_amount":-2},{"net_mf_amount":5}]
    s=moneyflow_accumulation_score(flows)
    assert s>50,s
    liq=liquidity_score({"turnover_rate":4.2},{"amount":800000})
    assert liq>=70,liq
    x=build_extra(moneyflow_df=flows,daily_basic_row={"turnover_rate":4.2},
                  daily_row={"amount":800000},share_float_df=[],top_list_df=[],
                  ts_code="601086.SH",sector_heat=65,chip_lock_score=72)
    assert x["accumulation_score"]>50 and x["liquidity_score"]>=70

def test_auction_confirm():
    from latent_auction_confirm import confirm
    latent={"latent_score":84,"latent_stage":"READY"}
    tick={"pct":2.0,"amount":60000000,"imbalance":.55,"bid_vol":100,"ask_vol":30}
    x=confirm(latent,tick)
    assert x["preopen_status"] in ("PREOPEN_PRIORITY","KEEP_READY"),x
    assert x["is_buy_signal"] is False


def test_limitup_adapter_and_ecology():
    from mock_limitup import today_rows,yesterday_rows
    from limitup_ecology import analyze
    r=analyze(today_rows(),yesterday_rows(),broken_rate=25.8,limit_down=9)
    assert r["max_board"]==5,r
    assert r["multi_board_count"]>=8,r
    assert r["promotion"] is not None
    assert r["succession"]["happened"] is True,r["succession"]
    assert r["phase"] in ("接力主升","梯队扩散","分歧博弈"),r

def test_first_board_potential():
    from first_board_potential import potential
    x=potential({
        "latent_score":86,"first_seal_score":90,"reseal_quality":88,"seal_strength":92,
        "turnover_quality":88,"sector_ladder":90,"sector_heat":86,"auction_quality":74,
        "main_flow":82,"catalyst":80,"crowding_risk":8,"event_risk":4,
        "regulatory_risk":0,"unlock_risk":0,"broken_rate_market":20
    })
    assert x["board_grade"]=="A",x
    assert x["board_potential"]>=82,x
    assert x["is_buy_signal"] is False

def test_limitup_runtime():
    from mock_limitup import today_rows,yesterday_rows
    from limitup_store import LimitupStore
    from limitup_runtime import LimitupRuntime
    pp=Path(tempfile.gettempdir())/"limitup_v84_test.db";pp.unlink(missing_ok=True)
    store=LimitupStore(pp)
    def extra(item):
        high=item.get("name")=="首板潜力A"
        return {"latent_score":85 if high else 70,"sector_heat":82,"main_flow":80 if high else 62,
                "catalyst":78,"event_risk":5,"unlock_risk":3,"regulatory_risk":0,"crowding_risk":8}
    r=LimitupRuntime(store).analyze_day(today_rows(),"20260902",yesterday_rows(),25.8,9,extra)
    assert r["ecology"]["max_board"]==5
    assert len(r["first_board_potential"])==2
    assert r["first_board_potential"][0]["board_potential"]>=r["first_board_potential"][1]["board_potential"]
    assert len(r["high_board_risk"])>=3
    pp.unlink(missing_ok=True)

def test_mainline_rotation():
    from mock_limitup import today_rows
    from limitup_ecology import sector_ladders
    from mainline_rotation import rank_sectors,early_mainline_candidates
    ranked=rank_sectors(sector_ladders(today_rows()))
    assert ranked
    assert ranked[0]["rotation_score"]>=ranked[-1]["rotation_score"]
    assert isinstance(early_mainline_candidates(ranked),list)

def test_first_board_training_and_audit():
    from first_board_training import build_sample,compare_groups
    from board_model_audit import evaluate
    fb={"trade_date":"20260901","ts_code":"A","name":"正样本"}
    s1=build_sample(fb,[{"ts_code":"A","board":2}],{"seal_strength":90,"sector_ladder":88})
    s1["board_grade"]="A"
    fb2={"trade_date":"20260901","ts_code":"B","name":"负样本"}
    s2=build_sample(fb2,[],{"seal_strength":40,"sector_ladder":35})
    s2["board_grade"]="C"
    assert s1["label"]==1 and s2["label"]==0
    diffs=compare_groups([s1,s2])
    assert diffs and diffs[0]["feature"] in ("seal_strength","sector_ladder")
    m=evaluate([s1,s2])
    assert m["n"]==2 and m["actual_promoted"]==1



def test_limitup_pipeline():
    from mock_limitup import today_rows,yesterday_rows
    from limitup_pipeline import LimitupPipeline
    r=LimitupPipeline().analyze(today_rows(),"20260902",yesterday_rows(),25.8,9,
        lambda x:{"latent_score":88,"sector_heat":85,"main_flow":82,"catalyst":80,"event_risk":5,"unlock_risk":5})
    assert r["sector_rotation"]
    assert "wecom_preview" in r
    assert r["ecology"]["max_board"]==5

def test_v84_pipeline():
    from v84_pipeline import V84Pipeline
    from mock_limitup import today_rows,yesterday_rows
    v=V84Pipeline()
    r=v.limitup_ecology(today_rows(),"20260902",yesterday_rows(),25.8,9,
        lambda x:{"latent_score":86,"sector_heat":82,"main_flow":80,"catalyst":78})
    assert r["ecology"]["max_board"]==5


def test_factor_lab():
    from mock_stat_learning import research_frame
    from factor_lab import lab_report
    df=research_frame(dates=45,stocks=55)
    factors=["auction_quality","main_flow_score","chip_lock_score","trend_score","trend_duplicate","noise_factor"]
    r=lab_report(df,factors)
    reps={x["factor"]:x for x in r["factors"]}
    assert abs(reps["trend_score"]["rank_ic_mean"])>abs(reps["noise_factor"]["rank_ic_mean"])+.05, reps
    dropped=[x["factor"] for x in r["prune"]["dropped"]]
    assert "trend_duplicate" in dropped or "trend_score" in dropped, r["prune"]

def test_bootstrap_confidence():
    from bootstrap_confidence import bootstrap_trade_metrics
    x=bootstrap_trade_metrics([2,1,-1,3,.5,-.7,1.2,2.5,-1,1.8]*8,iterations=500)
    assert x["n"]==80
    assert x["win_rate_ci"][0] <= x["win_rate"] <= x["win_rate_ci"][1]
    assert x["win_rate_ci"][1]>x["win_rate_ci"][0]

def test_monte_carlo():
    from monte_carlo import simulate_trade_returns
    x=simulate_trade_returns([2,1,-1,3,.5,-.7,1.2,2.5,-1,1.8],paths=500,trades_per_path=50,target_return_pct=20)
    assert 0<=x["profit_probability"]<=1
    assert 0<=x["target_probability"]<=1
    assert 0<=x["drawdown_breach_probability"]<=1

def test_alpha_ranker():
    from mock_stat_learning import research_frame
    from alpha_ranker import AlphaRanker,resonance
    df=research_frame(dates=60,stocks=70)
    dates=sorted(df["date"].unique())
    train=df[df["date"].isin(dates[:45])]
    test=df[df["date"].isin(dates[45:])].copy()
    feats=["auction_quality","main_flow_score","chip_lock_score","trend_score","noise_factor"]
    r=AlphaRanker().fit(train,feats)
    pred=r.predict(test)
    corr=pred["alpha_raw"].corr(pred["forward_return"],method="spearman")
    assert corr>.20,corr
    assert pred["alpha_rank_pct"].between(0,1).all()
    rr=resonance("A",.99,False)
    assert rr["grade"]=="A+"

def test_regime_router():
    from regime_router import route
    assert route("主升")["champion"]=="rules_trend"
    assert route("退潮")["champion"]=="rules_defensive"

def test_execution_simulator():
    from execution_simulator import simulate_fill,trade_return
    blocked=simulate_fill({"side":"SELL","qty":100,"trade_date":"20260902","acquired_trade_date":"20260902"},
                          {"price":10,"bid":9.99,"available_volume":10000})
    assert blocked["filled_qty"]==0 and "T+1" in blocked["reason"]
    up=simulate_fill({"side":"BUY","qty":100},{"price":10,"ask":10,"at_limit_up":True,"queue_fill_possible":False})
    assert up["filled_qty"]==0
    buy=simulate_fill({"side":"BUY","qty":100},{"price":10,"ask":10,"available_volume":10000})
    sell=simulate_fill({"side":"SELL","qty":100,"trade_date":"20260903","acquired_trade_date":"20260902"},
                       {"price":10.5,"bid":10.5,"available_volume":10000})
    ret=trade_return(buy,sell)
    assert ret is not None and 4 < ret < 6,ret

def test_model_drift():
    from model_drift import psi,drift_level,performance_drift
    ref=list(range(100))*2
    cur=[x+40 for x in range(100)]*2
    p=psi(ref,cur)
    assert p is not None and p>0
    assert drift_level(p) in ("WARN","ALERT")
    d=performance_drift([2,1,1,-.2]*20,[-2,-1,.2,-1]*10)
    assert d["level"] in ("WARN","ALERT")

def test_model_governance():
    from model_governance import compare,approve_only_after_paper
    champion={"n":100,"win_rate":.72,"expectancy":.8,"max_drawdown":-9}
    challenger={"n":80,"win_rate":.76,"expectancy":1.05,"max_drawdown":-8,"rank_ic":.08}
    r=compare(champion,challenger)
    assert r["promotable"],r
    r["paper_days"]=5
    assert not approve_only_after_paper(r)["approved"]
    r["paper_days"]=12
    assert approve_only_after_paper(r)["approved"]

def test_research_dataset_guard():
    import pandas as pd
    from research_dataset import validate_training_frame
    good=pd.DataFrame([{"date":"20260901","forward_return":1,"decision_time":"2026-09-01 10:00","outcome_time":"2026-09-02 10:00"}])
    assert validate_training_frame(good)==[]
    bad=good.copy();bad["outcome_time"]="2026-09-01 09:00"
    assert validate_training_frame(bad)

def test_v85_pipeline():
    from v85_pipeline import V85Pipeline
    v=V85Pipeline()
    assert v.rule_ml_resonance("A",.99)["grade"]=="A+"
    x=v.validate_challenger(
       {"n":100,"win_rate":.72,"expectancy":.8,"max_drawdown":-9},
       {"n":80,"win_rate":.76,"expectancy":1.1,"max_drawdown":-8,"rank_ic":.1},
       paper_days=12)
    assert x["approval"]["approved"],x


def test_triple_barrier():
    from triple_barrier import label_path
    win=label_path(100,[{"high":105,"low":99,"close":104}],4,2,3)
    assert win["label"]==1 and win["barrier"]=="PROFIT",win
    loss=label_path(100,[{"high":101,"low":97,"close":98}],4,2,3)
    assert loss["label"]==0 and loss["barrier"]=="STOP",loss
    both=label_path(100,[{"high":105,"low":97,"close":101}],4,2,3)
    assert both["label"]==0 and "CONSERVATIVE" in both["barrier"],both
    timeout=label_path(100,[{"high":101,"low":99,"close":100.5},{"high":102,"low":99.5,"close":101}],4,2,2)
    assert timeout["barrier"]=="TIME"

def test_sample_uniqueness():
    from sample_uniqueness import uniqueness_weights
    e=[{"id":"a","start":0,"end":4},{"id":"b","start":2,"end":6},{"id":"c","start":10,"end":11}]
    w=uniqueness_weights(e)
    assert 0<w["a"]<1 and 0<w["b"]<1
    assert w["c"]==1.0

def test_purged_cv():
    from purged_cv import purged_kfold,cpcv_splits
    starts=list(range(30));ends=[i+2 for i in starts]
    folds=list(purged_kfold(starts,ends,n_splits=5,embargo_bars=1))
    assert len(folds)==5
    for tr,te in folds:
        assert not set(tr)&set(te)
        for i in tr:
            for j in te:
                assert ends[i] < starts[j] or starts[i] > ends[j]
    c=list(cpcv_splits(starts,ends,n_groups=5,n_test_groups=2,embargo_bars=1))
    assert len(c)==10

def _meta_frame(rows=900,dates=50,seed=7):
    import numpy as np, pandas as pd
    rng=np.random.default_rng(seed)
    out=[]
    for i in range(rows):
        d=i%dates
        trend=rng.normal(0,1);flow=rng.normal(0,1);auction=rng.normal(0,1);noise=rng.normal(0,1)
        z=.9*trend+.7*flow+.5*auction+.15*noise+rng.normal(0,1)
        p=1/(1+np.exp(-z))
        y=int(rng.random()<p)
        out.append({"date":f"2026{1+d//20:02d}{1+d%20:02d}","event_start":i*2,"event_end":i*2+3,
                    "sample_weight":1.0,"trend":trend,"flow":flow,"auction":auction,"noise":noise,"meta_label":y})
    return pd.DataFrame(out)

def test_meta_labeler_and_calibrator():
    import numpy as np
    from meta_labeler import MetaLabeler
    from probability_calibrator import ProbabilityCalibrator
    df=_meta_frame()
    dates=sorted(df["date"].unique())
    train=df[df["date"].isin(dates[:38])]
    cal=df[df["date"].isin(dates[38:44])]
    test=df[df["date"].isin(dates[44:])]
    feats=["trend","flow","auction","noise"]
    m=MetaLabeler().fit(train,feats,sample_weight_col="sample_weight")
    pcal_raw=m.predict_proba(cal)["meta_probability"].values
    pc=ProbabilityCalibrator("isotonic").fit(pcal_raw,cal["meta_label"].values)
    ptest_raw=m.predict_proba(test)["meta_probability"].values
    ptest=pc.predict(ptest_raw)
    assert len(ptest)==len(test)
    assert np.all((ptest>=0)&(ptest<=1))

def test_conformal_selective():
    import numpy as np
    from conformal_selective import SplitConformalBinary,selective_decision
    rng=np.random.default_rng(4)
    y=np.array([0]*60+[1]*60)
    p=np.concatenate([rng.uniform(.02,.25,60),rng.uniform(.75,.98,60)])
    c=SplitConformalBinary(alpha=.10).fit(p,y)
    good=c.evaluate(.95)
    assert good["singleton_positive"],good
    d=selective_decision(.90,good,.995,True,False)
    assert d["decision"]=="EXECUTE" and d["grade"]=="A+",d
    bad=c.evaluate(.52)
    d2=selective_decision(.52,bad,.5,True,False)
    assert d2["decision"]=="ABSTAIN"

def test_meta_dataset():
    from meta_dataset import build_meta_rows
    bars={"A":[{"close":100,"high":101,"low":99},{"close":104,"high":105,"low":99},{"close":106,"high":106,"low":103}],
          "B":[{"close":100,"high":101,"low":99},{"close":98,"high":101,"low":97},{"close":97,"high":99,"low":96}]}
    sig=[{"event_id":"a","date":"20260901","ts_code":"A","event_index":0,"entry_price":100,"f1":1},
         {"event_id":"b","date":"20260901","ts_code":"B","event_index":0,"entry_price":100,"f1":-1}]
    df=build_meta_rows(sig,bars,["f1"])
    assert len(df)==2
    assert set(df["meta_label"])=={0,1}
    assert "sample_weight" in df

def test_cpcv_meta_validation():
    from cpcv_meta_validation import validate
    df=_meta_frame(rows=900,dates=50)
    r=validate(df,["trend","flow","auction","noise"],max_splits=5)
    assert r["overall"]["n"]>0,r
    assert 0<=r["overall"]["precision"]<=1
    assert 0<=r["overall"]["brier"]<=1

def test_deflated_sharpe():
    from deflated_sharpe import probabilistic_sharpe_ratio,deflated_sharpe_ratio
    r=[1.2,.8,-.4,1.5,.3,-.2,1.1,.9,-.3,1.0]*15
    psr=probabilistic_sharpe_ratio(r,0)
    assert psr is not None and 0<=psr<=1
    dsr=deflated_sharpe_ratio(r,10,trial_sharpes=[.4,.6,.7,.8,.9,.5,.3,.2,.65,.75])
    assert dsr and 0<=dsr["deflated_sharpe_probability"]<=1

def test_precision_sniper():
    from conformal_selective import SplitConformalBinary
    from precision_sniper import sniper_decision
    c=SplitConformalBinary(.10).fit([.05]*40+[.95]*40,[0]*40+[1]*40)
    cr=c.evaluate(.95)
    primary={"pool":"A"}
    ref=sniper_decision(primary,.91,cr,.995,True,False,"启动",False)
    assert ref["decision"]=="REFERENCE"
    live=sniper_decision(primary,.91,cr,.995,True,False,"启动",True)
    assert live["decision"]=="EXECUTE" and live["grade"]=="A+"
    climax=sniper_decision(primary,.91,cr,.995,True,False,"高潮",True)
    assert climax["grade"]=="A"

def test_precision_pipeline():
    from precision_pipeline import PrecisionEngine
    from conformal_selective import SplitConformalBinary
    c=SplitConformalBinary(.10).fit([.05]*40+[.95]*40,[0]*40+[1]*40)
    x=PrecisionEngine().final_decision({"pool":"A"},.92,c.evaluate(.95),.995,True,False,"启动",True)
    assert x["grade"]=="A+"

def test_store():
    from data_store import Store
    p=Path(tempfile.gettempdir())/"radar_v83_test.db";p.unlink(missing_ok=True)
    s=Store(p);sid=s.put_signal({"ts_code":"000001.SZ","name":"测试","pool":"A","score":88})
    assert sid>0 and s.stats()["signals"]==1
    p.unlink(missing_ok=True)

if __name__=="__main__":
    tests=[test_imports,test_market,test_signal_a,test_signal_veto,test_pit,test_event_bus,
           test_provider_guard,test_portfolio_risk,test_calibration,test_walkforward,test_backtest,
           test_latent_radar,test_latent_store_and_runtime,test_missed_opportunity,
           test_precision_recall_guard,test_paid_feature_fusion,test_auction_confirm,
           test_limitup_adapter_and_ecology,test_first_board_potential,test_limitup_runtime,
           test_mainline_rotation,test_first_board_training_and_audit,test_limitup_pipeline,test_v84_pipeline,
           test_factor_lab,test_bootstrap_confidence,test_monte_carlo,test_alpha_ranker,test_regime_router,
           test_execution_simulator,test_model_drift,test_model_governance,test_research_dataset_guard,
           test_v85_pipeline,test_triple_barrier,test_sample_uniqueness,test_purged_cv,
           test_meta_labeler_and_calibrator,test_conformal_selective,test_meta_dataset,
           test_cpcv_meta_validation,test_deflated_sharpe,test_precision_sniper,
           test_precision_pipeline,test_store]
    for t in tests:
        t();print("✅",t.__name__)
    print("\nALL V8.6 OFFLINE TESTS PASSED")
