ROLES=('admin','analyst','viewer')
STATES=('queued','running','done','failed')
ZONES=('冷藏','冷冻','常温对照')
# 匿名发布状态：draft 已扣费但尚未对外可见；published 已写文件且对外可见；failed 已退款
RELEASE_STATES=('draft','published','failed')
# 对外仅发布两个分布：温控（暴露分钟）与货损（货损率 %），均为匿名频数直方图
RELEASE_METRICS=('exposure','loss')
# 每个温区的默认累计隐私预算（epsilon），可由负责人在页面调整
DEFAULT_ZONE_EPSILON=4.0
# 单次发布在两个分布之间平分预算
METRICS_PER_RELEASE=len(RELEASE_METRICS)
# 冻结的分箱边界与裁剪范围。所有发布共用同一套口径，保证不同发布之间可比、可复核，
# 且攻击者无法通过改变分箱方式套取额外信息。
FROZEN_BINS={
 'exposure':{'edges':[0,10,20,30,45,60,90,1440],'clip_min':0.0,'clip_max':1440.0,'unit':'分钟'},
 'loss':{'edges':[0,1,2,4,6,10,20,100],'clip_min':0.0,'clip_max':100.0,'unit':'%'},
}
# 拉普拉斯机制：单样本对单个分箱频数的贡献至多为 1，故 L1 敏感度固定为 1
L1_SENSITIVITY=1
