# 事件与情绪提前观察 — EVENT_RESEARCH_1

## 设计与边界

事件是研究假设的来源，不是低位或买点的替代。日历已知并不意味着收益可以预测。
本模块独立保存首次事件发现、首次公司关联、首次有效价格和后续观察。
不会修改老 V8B，不写原研究交易日志，不改变 execution_eligible，不产生胜率。

## 已连接

- fusion_runtime_v2.Runtime.publish 驱动事件模块，独立异常不阻断原扫描。
- fusion_workspace_v2 显示事件卡片、板块逻辑、关联股票、缺失证据和失效状态。
- 2026 年官方七类假期；60 天滚动窗口；不将农历日期外推到下一年。
- 主题关联优先复用现有 p0_stock_basic_cache.json（只读）及其原始时间；休市阶段每天刷新，最多一小时重试一次。
- 免税没有宽泛行业替代，不将全部零售公司映射为免税公司。
- 60 日历史复权结构来自现有 Preparation；缺历史/时效/口径时显示等待。
- 每事件最多80只行业关联公司，按主题轮流取样避免单一行业占满，主题内代码稳定排序；界面披露非全市场排名。
- 航空客运使用空运/航空运输分类，不用涵盖航空制造的“航空”分类；仍需核对客运主营。

## 云端部署包

python fusion_event_package.py 生成 data/event_acceptance/FUSION_EVENT_RESEARCH_1.zip。
包内只有7个明确列出的代码/文档文件、安装器及manifest，不含密钥、行业缓存或数据库。
在已登录的服务器解压后，使用现有Python运行环境执行：

```
/opt/v8-radar/.venv/bin/python fusion_event_install.py --target /opt/astock-radar-v866/releases/design3_20260905_142250
```

默认仅校验目标和修改前SHA。目标服务目录和源码必须匹配；漂移时停止，不覆盖未知改动。
校验通过后加 --apply 才备份、覆盖指定文件、重启新版并检查HTTP与旧V8 PID；失败会恢复代码。
安装器会先运行断网回归。它不配置群、不复制凭证、不操作旧V8B，也不证明实时交易时段验收成功。
- 相对低位、价格响应、多证据同向等候选最多24只补充原扫描；原 assess 风控继续生效。
- 每分钟最多评估一次；事件新增、每周阶段或状态变化进入独立新版消息台账。
- 只有现有 notify_research 开启才发送；关闭时 HELD，不在之后回放。

## 日期依据

国务院办公厅国办发明〔2025〕7号，2025-11-04公布：
https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm
已核对政府转载：
https://www.beijing.gov.cn/cs/gncs/zcwj/202603/t20260327_4568275.html
核对日期2026-09-05。此日未来60天包含中秋9月25日至27日及国庆10月1日至7日。
假期日期不替代交易所休市日历。2027未维护时会显示缺口。

## 可扩展事件

data/fusion_events.json 为对象列表，不存在时仍运行官方日历。每对象：
id（唯一）、name、start/end（YYYY-MM-DD）、published_at（带时区）、source（https来源）、
themes（tourism/hotel/aviation/film/food/duty_free中的一个或多个）、cancelled（布尔，可选）。
可录入已公告会议、展会、档期等；本版本不声称自动采集这些事件。只接纳当前已公开记录。
应通过 cancelled=true 撤销事件；删除已录入事件也会撤销，保留其原始记录。

## 证据输入

data/fusion_event_evidence.json 对象列表：event_id、ts_code、kind、status、source、fact、
published_at、observed_at、valid_until。后三个时间必须可解析且按先后顺序成立。
kind: business（主营关联）、demand（需求订单）、flow（资金）、sector（板块）、risk（重大风险）。
status: SUPPORTS/CONTRADICTS。risk的SUPPORTS表示风险已得到支持，因此否决研究观察。
business记录可增加theme，例如duty_free；有有效主营证据时可补充行业分类以外的公司映射。
资金/板块证据最长24小时，其他最长7日；有效期之外显示UNKNOWN，同时支持和反对为CONFLICT。
所有事实应有可审计来源，输入记录本身不是第三方真实性认证。
本版本尚未自动获取订单、预订、新闻或公司主营证据，不能把字段存在说成数据已接通。

## 存储、复盘、测试

data/fusion_event_radar.sqlite3:
event_origin / candidate_origin / price_origin 为独立原始记录；observation 是时序快照；
current 仅表示最新状态；metadata 保存行业缓存和日历提醒阶段。状态变化立即留存，稳定状态每15分钟留样。
第一次有效报价可能晚于第一次关联，不使用回填价格伪造提前发现。
当前没有事件专属收益结算，保留快照供后续独立样本外验证；不显示虚构收益。
运行 test_fusion_event_radar 及所有 test_fusion_* 时禁用网络。
部署只打包指定源码，不打包 secrets.local.json、API Key、数据或其他服务配置。
