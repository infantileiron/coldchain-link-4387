"""差分隐私匿名统计：冻结分箱/裁剪 + 拉普拉斯机制。

对外只产出按温区聚合的样本频数直方图（温控暴露分布、货损分布），
不包含任何班次号、样本号、线路、驾驶员等明细字段。
"""
import math
from src.constants.enums import FROZEN_BINS, L1_SENSITIVITY, METRICS_PER_RELEASE, RELEASE_METRICS

BIN_VERSION='frozen-v1'


def clip(value, lo, hi):
    return min(hi, max(lo, value))


def bin_index(value, edges):
    """返回值落入的分箱下标。edges 为单调递增边界，共 len(edges)-1 个箱。"""
    n = len(edges) - 1
    for i in range(n):
        left, right = edges[i], edges[i + 1]
        # 末箱为闭区间，容纳裁剪后的上界
        if i == n - 1:
            if left <= value <= right:
                return i
        elif left <= value < right:
            return i
    return n - 1


def build_histogram(rows, metric):
    """按冻结口径把某指标聚合成原始频数，并按班次限制贡献。

    隐私保护单元是班次（shipment）：同一班次即使有多条样本，对该指标的
    直方图也至多贡献 1（取该班次首个非空值）。因此增删一个班次，任意单个
    分箱频数至多变化 1，L1 敏感度严格为 1。
    返回每个分箱的区间标签与原始计数（加噪前，仅内部计算使用，不外泄）。
    """
    cfg = FROZEN_BINS[metric]
    edges, lo, hi = cfg['edges'], cfg['clip_min'], cfg['clip_max']
    counts = [0] * (len(edges) - 1)
    # 按班次取每个指标的首个非空值，缺失 shipment_id 的行各自独立成组
    first_by_shipment = {}
    order = 0
    for r in rows:
        v = r.get(metric)
        if v is None:
            continue
        key = r.get('shipment_id')
        if key is None:
            key = ('__row__', order)
        order += 1
        if key not in first_by_shipment:
            first_by_shipment[key] = v
    for v in first_by_shipment.values():
        counts[bin_index(clip(float(v), lo, hi), edges)] += 1
    labels = [
        f'[{edges[i]}, {edges[i + 1]}{"]" if i == len(edges) - 2 else ")"}'
        for i in range(len(edges) - 1)
    ]
    return {'metric': metric, 'labels': labels, 'raw_counts': counts,
            'count_used': len(first_by_shipment)}


def laplace_sample(rng, loc, scale):
    """逆 CDF 采样 Laplace(loc, scale)，基于 rng.random()，可种子复现。"""
    u = rng.random() - 0.5  # (-0.5, 0.5) 均匀
    return loc - scale * (1 if u >= 0 else -1) * math.log(1 - 2 * abs(u))


def laplace_mechanism(counts, epsilon, rng):
    """对每个分箱频数叠加尺度为 敏感度/epsilon 的拉普拉斯噪声并取非负整数。"""
    scale = L1_SENSITIVITY / epsilon
    noisy = []
    for c in counts:
        z = laplace_sample(rng, 0.0, scale)
        noisy.append(max(0, int(round(c + z))))
    return noisy, scale


def anonymize(rows, total_epsilon, rng):
    """生成对外匿名结果。两个分布平分本次发布预算。

    对外只暴露加噪量及其后处理（如加噪频数之和 noisy_total）；精确的贡献
    班次数 count_used 敏感度为 1，属未计预算的精确统计，故不放入对外载荷，
    仅在内部 build_histogram 返回值中保留用于校验。
    """
    per_metric = total_epsilon / METRICS_PER_RELEASE
    metrics_out = []
    for metric in RELEASE_METRICS:
        h = build_histogram(rows, metric)
        noisy, scale = laplace_mechanism(h['raw_counts'], per_metric, rng)
        cfg = FROZEN_BINS[metric]
        metrics_out.append({
            'metric': metric,
            'unit': cfg['unit'],
            'clip_min': cfg['clip_min'],
            'clip_max': cfg['clip_max'],
            'edges': cfg['edges'],
            'bins': h['labels'],
            # 对外仅给出加噪后的匿名频数，及其后处理（求和），均不额外耗预算
            'noisy_counts': noisy,
            'noisy_total': sum(noisy),
            'epsilon': per_metric,
            'laplace_scale': scale,
            'sensitivity': L1_SENSITIVITY,
        })
    return {'bin_version': BIN_VERSION, 'metrics': metrics_out,
            'epsilon_total': total_epsilon, 'mechanism': 'Laplace',
            'note': '频数为叠加拉普拉斯噪声后的匿名结果，可能含误差；不含任何班次或样本明细。'}


def public_payload(release):
    """组装对外文件/接口载荷：只含匿名分布与口径，绝不含明细。"""
    return {
        'release_id': release.id,
        'zone': release.zone,
        'state': release.state,
        'bin_version': release.bin_version,
        'bins': release.bins,
        'result': release.result,
        'epsilon_spent': release.epsilon_spent,
        'created': release.created,
        'published': release.published,
    }
