"""Schema 与命名常量：Label / 关系名 / 意图的唯一来源。"""
from dataclasses import dataclass

# ---- 五类实体 Label（建图与查询共用）----
DISEASE = "Disease"
DRUG = "Drug"
SYMPTOM = "Symptom"
CHECK = "Check"
PREVENT = "Prevent"
LABELS = (DISEASE, DRUG, SYMPTOM, CHECK, PREVENT)
LABEL_CN = {DISEASE: "疾病", DRUG: "药品", SYMPTOM: "症状", CHECK: "检查", PREVENT: "预防"}

# ---- 关系类型，方向统一 Disease -> 目标实体 ----
HAS_DRUG = "HAS_DRUG"
HAS_SYMPTOM = "HAS_SYMPTOM"
NEED_CHECK = "NEED_CHECK"
PREVENTS = "PREVENTS"
ALIAS_OF = "ALIAS_OF"
RELATIONS = (HAS_DRUG, HAS_SYMPTOM, NEED_CHECK, PREVENTS, ALIAS_OF)
RELATION_CN = {
    HAS_DRUG: "推荐药物",
    HAS_SYMPTOM: "症状表现",
    NEED_CHECK: "相关检查",
    PREVENTS: "预防措施",
    ALIAS_OF: "别名",
}

# ---- 属性键 ----
PROP_NAME = "name"
PROP_DESC = "desc"
PROP_TYPE = "type"
PROP_SOURCE = "source"
PROP_ALIAS = "alias"
PROP_DATE = "date"
PROP_URL = "url"

# ---- 9 类意图 ----
INTENT_DRUG = "ask_drug"
INTENT_PLAN = "ask_plan"
INTENT_INTERACTION = "ask_interaction"
INTENT_SYMPTOM = "ask_symptom"
INTENT_COMPLICATION = "ask_complication"
INTENT_POSSIBLE_DISEASE = "ask_possible_disease"
INTENT_PREVENT = "ask_prevent"
INTENT_CHECK = "ask_check"
INTENT_CYCLE = "ask_cycle"

INTENT_CN = {
    INTENT_DRUG: "推荐药物",
    INTENT_PLAN: "治疗方案",
    INTENT_INTERACTION: "药物相互作用",
    INTENT_SYMPTOM: "症状表现",
    INTENT_COMPLICATION: "并发症",
    INTENT_POSSIBLE_DISEASE: "可能疾病",
    INTENT_PREVENT: "预防措施",
    INTENT_CHECK: "相关检查",
    INTENT_CYCLE: "治疗周期",
}
INTENTS = tuple(INTENT_CN)


@dataclass(frozen=True)
class IntentSpec:
    name: str
    relation: str | None      # 对应图谱边；None 表示待扩展
    supported: bool
    entity_label: str | None
    extension: str            # 未支持时的扩展路径


INTENT_SPECS = {
    INTENT_DRUG: IntentSpec(INTENT_DRUG, HAS_DRUG, True, DISEASE, ""),
    INTENT_SYMPTOM: IntentSpec(INTENT_SYMPTOM, HAS_SYMPTOM, True, DISEASE, ""),
    INTENT_CHECK: IntentSpec(INTENT_CHECK, NEED_CHECK, True, DISEASE, ""),
    INTENT_PREVENT: IntentSpec(INTENT_PREVENT, PREVENTS, True, DISEASE, ""),
    INTENT_PLAN: IntentSpec(INTENT_PLAN, None, False, DISEASE,
                            "疾病节点新增 plan 属性并补查询模板"),
    INTENT_CYCLE: IntentSpec(INTENT_CYCLE, None, False, DISEASE,
                             "疾病节点新增 cycle 属性并补查询模板"),
    INTENT_INTERACTION: IntentSpec(INTENT_INTERACTION, None, False, DRUG,
                                   "新增 Drug-Drug 关系与禁忌属性后补查询模板"),
    INTENT_COMPLICATION: IntentSpec(INTENT_COMPLICATION, None, False, DISEASE,
                                    "反向查询与多跳路径 Disease-[:HAS_SYMPTOM]->Symptom"),
    INTENT_POSSIBLE_DISEASE: IntentSpec(INTENT_POSSIBLE_DISEASE, None, False, SYMPTOM,
                                        "反向查询 Symptom->Disease 多跳路径"),
}

# 记录字段 -> 目标 Label（建图映射）
FIELD_TO_LABEL = {
    "drug": DRUG,
    "symptom": SYMPTOM,
    "check": CHECK,
    "prevent": PREVENT,
}
FIELD_TO_RELATION = {
    "drug": HAS_DRUG,
    "symptom": HAS_SYMPTOM,
    "check": NEED_CHECK,
    "prevent": PREVENTS,
}
RELATION_TO_LABEL = {
    HAS_DRUG: DRUG, HAS_SYMPTOM: SYMPTOM, NEED_CHECK: CHECK, PREVENTS: PREVENT,
}
RECORD_FIELDS = ("name", "desc", "symptom", "drug", "check", "prevent")

# 高风险问句关键词：命中直接走安全兜底，不进入图谱检索
HIGH_RISK_KEYWORDS = (
    "孕妇", "怀孕", "哺乳", "婴儿", "新生儿", "儿童用药", "胸痛", "胸闷", "气短",
    "呼吸困难", "昏迷", "抽搐", "大出血", "出血不止", "自杀", "休克", "剧烈头痛",
)

SAFETY_NOTE = "以上信息仅供健康科普参考，不能替代医生诊断；症状持续或加重请及时就医。"
