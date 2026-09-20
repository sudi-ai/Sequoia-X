from __future__ import annotations

from typing import Any, Mapping


POLICY_ISSUERS = {
    "NATIONAL_CORE": ("国务院", "中共中央", "中央政治局", "全国人大"),
    "REGULATOR_OFFICIAL": ("中国人民银行", "央行", "证监会", "国家金融监督管理总局", "上交所", "深交所", "北交所"),
    "MINISTRY_OFFICIAL": ("国家发展改革委", "发改委", "财政部", "工信部", "商务部", "科技部", "交通运输部", "农业农村部", "国家能源局", "国家药监局"),
    "LOCAL_OFFICIAL": ("省政府", "市政府", "自治区政府", "省发改委", "市发改委"),
    "ASSOCIATION": ("行业协会", "产业协会", "商会"),
}

POLICY_STAGES = (
    ("POLICY_EXIT", ("政策退出", "废止", "停止执行")),
    ("ACTUAL_EXECUTION", ("正式实施", "落地实施", "开工", "执行")),
    ("FUNDING", ("资金拨付", "专项资金下达", "预算下达")),
    ("APPLICATION", ("项目申报", "开始申报", "征集项目")),
    ("IMPLEMENTATION_RULES", ("实施细则", "配套细则")),
    ("FORMAL_RELEASE", ("正式发布", "印发", "发布意见", "发布通知", "出台")),
    ("APPROVED", ("审议通过", "会议通过")),
    ("CONSULTATION", ("征求意见", "公开征求")),
    ("DISCUSSION", ("研究部署", "研究讨论", "拟研究")),
    ("RUMOR", ("传闻", "据悉", "市场消息")),
)

TOPIC_ALIASES = {
    "人工智能": ("人工智能", "AI大模型", "大模型"), "算力": ("算力", "智算中心", "数据中心"),
    "液冷服务器": ("液冷", "冷板", "浸没式散热"), "光模块": ("光模块", "CPO", "光通信"),
    "PCB": ("PCB", "印制电路板", "覆铜板"), "消费电子": ("消费电子", "智能手机", "可穿戴"),
    "显示面板": ("OLED", "显示面板", "面板"), "存储芯片": ("存储芯片", "DRAM", "NAND"),
    "半导体": ("半导体", "芯片", "晶圆", "先进封装"), "国产软件": ("国产软件", "信创", "操作系统"),
    "网络安全": ("网络安全", "数据安全", "信息安全"), "卫星通信": ("卫星通信", "商业航天", "卫星互联网"),
    "机器人": ("机器人", "减速器", "伺服系统"), "工业母机": ("工业母机", "数控机床"),
    "储能": ("储能", "新型储能"), "电网设备": ("电网设备", "电力设备", "智能电网"),
    "特高压": ("特高压", "超高压输电"), "风电": ("风电", "风力发电"), "核电": ("核电", "核能"),
    "氢能": ("氢能", "绿氢", "燃料电池"), "锂电池": ("锂电池", "动力电池"),
    "固态电池": ("固态电池", "固体电解质"), "新能源汽车": ("新能源汽车", "新能源车"),
    "充电桩": ("充电桩", "充换电", "换电站"), "电力市场改革": ("电力市场改革", "电价改革", "绿电交易"),
    "能源保供": ("能源保供", "电力保供", "迎峰度夏"),
    "光伏": ("光伏", "硅料", "硅片", "逆变器"), "原油": ("原油", "油价", "石油"),
    "天然气": ("天然气", "LNG"), "煤炭": ("煤炭", "动力煤", "焦煤"), "黄金": ("黄金", "金价", "贵金属"),
    "有色金属": ("铜价", "铝价", "锂价", "钴", "镍", "小金属"), "稀土": ("稀土", "稀土永磁", "镨钕"),
    "化工": ("化工", "化肥", "农药"), "航运港口": ("航运", "港口", "运价", "航道"),
    "创新药": ("创新药", "新药获批", "临床试验"), "医疗器械": ("医疗器械", "医用设备"),
    "原料药": ("原料药", "API药物"), "医药商业": ("医药商业", "药品流通", "医药零售"),
    "CXO": ("CXO", "CRO", "CDMO"), "中药": ("中药", "中成药"), "医疗服务": ("医疗服务", "医院"),
    "食品饮料": ("食品饮料", "白酒", "乳制品"), "旅游酒店": ("旅游", "酒店", "免税"),
    "影视游戏": ("影视", "电影", "游戏", "版号"), "汽车消费": ("汽车消费", "汽车补贴", "以旧换新"),
    "航空运输": ("航空运输", "航空公司", "民航"),
    "农业种业": ("种业", "种子", "生猪", "农产品", "农机"), "水利": ("水利", "防洪排涝"),
    "证券": ("证券", "券商", "资本市场改革"), "银行": ("银行", "商业银行"), "保险": ("保险", "险资"),
    "房地产": ("房地产", "商品房", "保障房"), "基建": ("基建", "基础设施"),
    "REITs": ("REITs", "公募REITs"), "地方债": ("地方债", "专项债"),
    "军工": ("军工", "导弹", "军事装备"), "无人机": ("无人机", "低空经济", "eVTOL"),
    "应急安全": ("应急装备", "灾后重建", "公共卫生", "疫情", "地震", "洪水"),
}

MACRO_WORDS = ("CPI", "PPI", "PMI", "利率", "汇率", "降息", "降准", "货币政策", "财政政策", "GDP")
LOW_VALUE_WORDS = ("早间速递", "盘前必读", "一图看懂", "消息汇总")
FOREIGN_ONLY_WORDS = ("海外地方选举", "当地体育", "海外娱乐")


def classify_event_metadata(text: str, source_api: str, row: Mapping[str, Any], code: str | None,
                            topic_hint: str | None = None) -> dict[str, Any]:
    issuer = issuer_level = None
    for level, names in POLICY_ISSUERS.items():
        hit = next((name for name in names if name in text), None)
        if hit:
            issuer, issuer_level = hit, level
            break
    policy_stage = next((stage for stage, words in POLICY_STAGES if any(word in text for word in words)), None)
    topics = [topic for topic, aliases in TOPIC_ALIASES.items() if any(alias.lower() in text.lower() for alias in aliases)]
    if topic_hint and topic_hint not in topics:
        topics.insert(0, topic_hint)
    tags: list[str] = []
    if code:
        tags.append("SECURITY_MAPPED")
    if issuer:
        tags.extend(["POLICY", issuer_level or ""])
    if policy_stage:
        tags.append(policy_stage)
    tags.extend(f"TOPIC:{topic}" for topic in topics[:5])
    is_policy = bool(issuer and (policy_stage or any(x in text for x in
      ("正式发布", "印发", "出台", "审议", "征求意见", "研究部署", "资金拨付", "项目申报", "实施细则"))))
    is_macro = any(word.lower() in text.lower() for word in MACRO_WORDS)
    if code:
        category, reason = "STOCK_EVENT", "已明确映射上市公司"
    elif is_policy:
        category, reason = "NATIONAL_POLICY", "识别到权威政策主体及政策语义"
    elif topics:
        category, reason = "INDUSTRY_EVENT", "已映射产业主题"
    elif is_macro:
        category, reason = "MACRO_EVENT", "已识别宏观指标或政策变量"
    elif any(word in text for word in LOW_VALUE_WORDS):
        category, reason = "LOW_VALUE_OR_DUPLICATE", "低信息密度资讯"
    elif any(word in text for word in FOREIGN_ONLY_WORDS):
        category, reason = "NO_A_SHARE_LINK", "未发现A股传导路径"
    else:
        category, reason = "UNRESOLVED", "存在事件线索但主题、政策和个股均未识别"
    entities = {"issuer": issuer, "topics": topics[:5], "security_code": code,
                "region": row.get("area") or row.get("region")}
    return {"primary_category": category, "event_tags": [x for x in dict.fromkeys(tags) if x],
            "related_entities": entities, "issuer": issuer, "issuer_level": issuer_level,
            "policy_stage": policy_stage, "topic_primary": topics[0] if topics else topic_hint,
            "topic_secondary": topics[1] if len(topics) > 1 else None,
            "classification_reason": reason, "recognition_failed": category == "UNRESOLVED"}
