"""差分隐私算法层测试：冻结分箱、裁剪、拉普拉斯、对外不含明细。"""
import random
from src.constants.enums import FROZEN_BINS, RELEASE_METRICS
from src.services import privacy as dp


def test_clip_and_bin_edges_are_frozen():
    edges = FROZEN_BINS['exposure']['edges']
    # 裁剪生效：超过上界被压到末箱
    assert dp.bin_index(dp.clip(99999, 0, 1440), edges) == len(edges) - 2
    # 下界进首箱
    assert dp.bin_index(0.0, edges) == 0
    # 边界归属：左闭右开
    assert dp.bin_index(edges[1], edges) == 1


def test_histogram_sensitivity_one():
    rows = [{'exposure': 15.0}, {'exposure': 15.0}, {'exposure': 80.0}, {'exposure': None}]
    h = dp.build_histogram(rows, 'exposure')
    # 每个样本至多计入一个箱，总使用数 == 非空样本数
    assert sum(h['raw_counts']) == 3
    assert h['count_used'] == 3


def test_per_shipment_contribution_capped_to_one():
    """同一班次即使有多条样本，对单一直方图至多贡献 1（按班次限贡献）。"""
    rows = [
        {'shipment_id': 1, 'exposure': 12.0},
        {'shipment_id': 1, 'exposure': 55.0},   # 同班次第二条，应被忽略
        {'shipment_id': 1, 'exposure': 90.0},   # 同班次第三条，应被忽略
        {'shipment_id': 2, 'exposure': 18.0},
        {'shipment_id': 2, 'exposure': None},   # 空值不计
        {'shipment_id': 3, 'exposure': 70.0},
    ]
    h = dp.build_histogram(rows, 'exposure')
    # 只有 3 个班次 => 总贡献 3，而不是 5 条非空样本
    assert sum(h['raw_counts']) == 3
    assert h['count_used'] == 3
    # 班次 1 取首个非空值 12.0，落入与 18 同一箱的可能性按边界验证
    edges = FROZEN_BINS['exposure']['edges']
    b12 = dp.bin_index(dp.clip(12.0, 0, 1440), edges)
    b18 = dp.bin_index(dp.clip(18.0, 0, 1440), edges)
    b70 = dp.bin_index(dp.clip(70.0, 0, 1440), edges)
    assert h['raw_counts'][b12] >= 1
    if b18 == b12:
        assert h['raw_counts'][b12] == 2
    assert h['raw_counts'][b70] == 1


def test_laplace_scale_and_nonneg_integer():
    rng = random.Random(7)
    counts = [10, 0, 5, 2]
    noisy, scale = dp.laplace_mechanism(counts, epsilon=1.0, rng=rng)
    assert scale == 1.0  # 敏感度 1 / epsilon 1
    assert all(isinstance(c, int) and c >= 0 for c in noisy)


def test_anonymize_splits_budget_and_has_no_detail():
    rows = [{'exposure': 12.0, 'loss': 3.2}, {'exposure': 70.0, 'loss': 8.8}]
    out = dp.anonymize(rows, total_epsilon=2.0, rng=random.Random(11))
    assert out['mechanism'] == 'Laplace'
    assert len(out['metrics']) == len(RELEASE_METRICS)
    for m in out['metrics']:
        assert m['epsilon'] == 1.0  # 两个分布平分
        assert 'raw_counts' not in m  # 原始频数不外泄
        assert 'count_used' not in m  # 精确贡献数不免费外泄
        assert 'noisy_counts' in m and 'noisy_total' in m
        assert m['noisy_total'] == sum(m['noisy_counts'])  # 后处理
        # 对外结构里没有任何班次/样本明细字段
        for forbidden in ('code', 'route', 'driver', 'shipment_id', 'sample'):
            assert forbidden not in m


def test_deterministic_with_fixed_rng():
    rows = [{'exposure': 12.0, 'loss': 3.2}] * 10
    a = dp.anonymize(rows, 1.0, random.Random(123))
    b = dp.anonymize(rows, 1.0, random.Random(123))
    assert a == b  # 同种子结果一致；不同发布用独立随机流
