"""预算账本测试：共用额度、余额不足拒绝、并发不超支、幂等、退款恢复。"""
import os
import json
import threading
import random

import pytest
from fastapi import HTTPException

from src.services import ledger
from src.services import privacy as dp
from src.models.entities import PrivacyBudget, PrivacyRelease, PrivacyLedger
from conftest import make_zone_data


def _bud(s, zone):
    return s.query(PrivacyBudget).filter_by(zone=zone).one()


def test_budget_init_and_view(session):
    view = ledger.budgets_view(session)
    assert len(view) == 3
    cold = next(b for b in view if b['zone'] == '冷藏')
    assert cold['total_epsilon'] == 4.0
    assert cold['spent_epsilon'] == 0.0
    assert cold['remaining_epsilon'] == 4.0


def test_release_charges_and_writes_public_file(session, artifact_dir):
    make_zone_data(session, '冷藏', n=15)
    rel, created = ledger.request_release(
        session, '冷藏', 1.0, 'analyst@demo.local', 'key-001', rng=random.Random(1)
    )
    assert created is True
    assert rel.state == 'published'
    assert _bud(session, '冷藏').spent_epsilon == pytest.approx(1.0)
    # 对外文件存在且只含匿名分布
    assert os.path.exists(rel.artifact_path)
    payload = json.loads(open(rel.artifact_path, encoding='utf-8').read())
    assert payload['zone'] == '冷藏'
    text = json.dumps(payload, ensure_ascii=False)
    for forbidden in ('S-冷藏', '司机A', '线路X', 'raw_counts', 'shipment_id'):
        assert forbidden not in text


def test_insufficient_budget_rejected_and_no_charge(session):
    make_zone_data(session, '冷冻', n=10)
    with pytest.raises(HTTPException) as e:
        ledger.request_release(session, '冷冻', 99.0, 'a@x', 'key-big')
    assert e.value.status_code == 402
    # 拒绝后没有任何发布单、没有扣费、没有流水
    assert session.query(PrivacyRelease).count() == 0
    assert _bud(session, '冷冻').spent_epsilon == 0.0
    assert session.query(PrivacyLedger).count() == 0


def test_shared_budget_across_overlapping_studies(session):
    """同温区多次（重叠）发布共用同一额度，逐次扣减直至拒绝。"""
    make_zone_data(session, '冷藏', n=10)
    ledger.request_release(session, '冷藏', 1.5, 'a@x', 'k1', random.Random(1))
    ledger.request_release(session, '冷藏', 1.5, 'a@x', 'k2', random.Random(2))
    assert _bud(session, '冷藏').spent_epsilon == pytest.approx(3.0)
    # 仅剩 1.0，再申请 1.5 被拒
    with pytest.raises(HTTPException) as e:
        ledger.request_release(session, '冷藏', 1.5, 'a@x', 'k3', random.Random(3))
    assert e.value.status_code == 402
    assert _bud(session, '冷藏').spent_epsilon == pytest.approx(3.0)  # 未变


def test_idempotent_retry_returns_same_no_recharge(session, artifact_dir):
    make_zone_data(session, '冷藏', n=12)
    first, c1 = ledger.request_release(session, '冷藏', 0.5, 'a@x', 'idem-key', random.Random(5))
    second, c2 = ledger.request_release(session, '冷藏', 0.5, 'a@x', 'idem-key', random.Random(999))
    assert c1 is True and c2 is False
    assert first.id == second.id
    assert first.result == second.result  # 不重新加噪
    assert _bud(session, '冷藏').spent_epsilon == pytest.approx(0.5)  # 只扣一次
    # 流水仅一条 charge
    charges = session.query(PrivacyLedger).filter_by(kind='charge').all()
    assert len(charges) == 1


def test_concurrent_requests_never_overspend(session):
    """多线程并发申请，成功者扣费总额不得超过总预算。"""
    make_zone_data(session, '冷藏', n=30)
    ledger.ensure_budgets(session)
    total = _bud(session, '冷藏').total_epsilon
    results = []
    lock = threading.Lock()

    def worker(idx):
        # 每个线程用独立 session 共享同一引擎
        from src.config.database import Session
        with Session() as ts:
            try:
                rel, created = ledger.request_release(
                    ts, '冷藏', 1.0, f'a{idx}@x', f'conc-{idx}', random.Random(idx)
                )
                with lock:
                    results.append(('ok', rel.id))
            except HTTPException as ex:
                with lock:
                    results.append(('reject', ex.status_code))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    spent = _bud(session, '冷藏').spent_epsilon
    assert spent <= total + 1e-9
    ok = [r for r in results if r[0] == 'ok']
    assert len(ok) == int(total)  # 预算 4.0 -> 恰好 4 个成功
    assert spent == pytest.approx(float(len(ok)))
    # 成功发布单均已对外可见
    pub = session.query(PrivacyRelease).filter_by(state='published').count()
    assert pub == len(ok)


def test_recovery_rebuilds_draft_when_artifact_missing(session, artifact_dir):
    """两阶段之间崩溃：draft 已扣费但文件缺失 -> 恢复时重建文件并发布，扣费仍有效。

    结果仍在数据库，发布可被重建，因此保持“已扣费即可见”，不退款。
    """
    make_zone_data(session, '冷藏', n=8)
    rel, _ = ledger.request_release(session, '冷藏', 1.0, 'a@x', 'rc1', random.Random(1))
    # 人为制造中断：删掉对外文件并回退为 draft（扣费记录已持久化）
    os.remove(rel.artifact_path)
    rel.state = 'draft'
    rel.artifact_path = ''
    session.commit()
    assert _bud(session, '冷藏').spent_epsilon == pytest.approx(1.0)

    recovered = ledger.recover_pending(session)
    assert (rel.id, 'published') in recovered
    session.refresh(rel)
    assert rel.state == 'published'
    assert os.path.exists(rel.artifact_path)  # 文件已重建，恢复对外可见
    assert _bud(session, '冷藏').spent_epsilon == pytest.approx(1.0)  # 无退款
    assert session.query(PrivacyLedger).filter_by(kind='refund').count() == 0


def test_recovery_refunds_draft_when_artifact_unwritable(session, artifact_dir, monkeypatch):
    """磁盘不可写导致阶段二失败：draft 无法对外可见 -> 退款并置 failed。"""
    make_zone_data(session, '冷藏', n=8)
    # 先构造一个持久化的 draft（模拟阶段一已提交、阶段二未做）
    from src.models.entities import PrivacyRelease
    rel, _ = ledger.request_release(session, '冷藏', 1.0, 'a@x', 'rcX', random.Random(1))
    os.remove(rel.artifact_path)
    rel.state = 'draft'
    rel.artifact_path = ''
    session.commit()

    def boom(_release):
        raise OSError('disk unavailable')

    monkeypatch.setattr(ledger, '_write_artifact', boom)
    recovered = ledger.recover_pending(session)
    assert (rel.id, 'refunded') in recovered
    session.refresh(rel)
    assert rel.state == 'failed'
    assert _bud(session, '冷藏').spent_epsilon == pytest.approx(0.0)
    refund = session.query(PrivacyLedger).filter_by(kind='refund', release_id=rel.id).one()
    assert refund.epsilon == pytest.approx(-1.0)


def test_idempotent_retry_self_heals_draft(session, artifact_dir):
    """同键重试命中中断的 draft：不重复扣费，驱动其重建文件并发布。"""
    make_zone_data(session, '冷藏', n=8)
    # 用相同键先做阶段一（通过正常发布拿到结果），再人为回退为 draft 并删文件
    rel, first_created = ledger.request_release(
        session, '冷藏', 0.8, 'a@x', 'heal-key', random.Random(4)
    )
    saved_result = rel.result
    os.remove(rel.artifact_path)
    rel.state = 'draft'
    rel.artifact_path = ''
    session.commit()

    rel2, second_created = ledger.request_release(
        session, '冷藏', 0.8, 'a@x', 'heal-key', random.Random(999)
    )
    assert first_created is True and second_created is False
    assert rel2.id == rel.id
    assert rel2.state == 'published'
    assert rel2.result == saved_result  # 未重新加噪
    assert os.path.exists(rel2.artifact_path)
    assert _bud(session, '冷藏').spent_epsilon == pytest.approx(0.8)  # 仅扣一次
    assert session.query(PrivacyLedger).filter_by(kind='charge').count() == 1


def test_recovery_promotes_draft_when_artifact_intact(session, artifact_dir):
    """文件已完整写出但状态停在 draft：恢复为 published，不退款。"""
    make_zone_data(session, '冷藏', n=8)
    rel, _ = ledger.request_release(session, '冷藏', 1.0, 'a@x', 'rc2', random.Random(1))
    rel.state = 'draft'
    session.commit()
    recovered = ledger.recover_pending(session)
    assert (rel.id, 'published') in recovered
    session.refresh(rel)
    assert rel.state == 'published'
    assert _bud(session, '冷藏').spent_epsilon == pytest.approx(1.0)  # 扣费有效


def test_set_budget_cannot_go_below_spent(session):
    make_zone_data(session, '冷藏', n=8)
    ledger.request_release(session, '冷藏', 2.0, 'a@x', 'b1', random.Random(1))
    with pytest.raises(HTTPException):
        ledger.set_budget(session, '冷藏', 1.0, 'admin@demo.local')
    ok, old = ledger.set_budget(session, '冷藏', 10.0, 'admin@demo.local')
    assert ok.total_epsilon == 10.0


def test_ledger_view_records_balance_after(session):
    make_zone_data(session, '冷藏', n=8)
    ledger.request_release(session, '冷藏', 1.0, 'a@x', 'l1', random.Random(1))
    ledger.request_release(session, '冷藏', 0.5, 'a@x', 'l2', random.Random(2))
    rows = ledger.ledger_view(session, '冷藏')
    assert len(rows) == 2
    # 最新在前，余额逐次下降
    assert rows[0]['balance_after'] == pytest.approx(2.5)
    assert rows[1]['balance_after'] == pytest.approx(3.0)


def test_release_caps_per_shipment_contribution(session):
    """一个班次挂多条样本时，发布结果仍按班次计数，敏感度保持 1。"""
    from src.models.entities import Shipment, Sample
    ship = Shipment(code='MULTI', route='线路M', zone='冷藏', driver='司机M')
    session.add(ship)
    session.flush()
    # 同一班次 5 条样本
    for i in range(5):
        session.add(Sample(shipment_id=ship.id, code=f'MULTI-S{i}',
                           exposure=10.0 + i, loss=2.0 + i))
    other = Shipment(code='OTHER', route='线路O', zone='冷藏', driver='司机O')
    session.add(other)
    session.flush()
    session.add(Sample(shipment_id=other.id, code='OTHER-S', exposure=30.0, loss=5.0))
    session.commit()

    rel, _ = ledger.request_release(session, '冷藏', 1.0, 'a@x', 'cap1', random.Random(2))
    metrics = {m['metric']: m for m in rel.result['metrics']}
    # 对外载荷不含精确贡献数（敏感度 1 的精确统计不免费外泄）
    assert all('count_used' not in m for m in rel.result['metrics'])
    # 按班次限贡献的正确性在内部直方图上验证：仅 2 个班次，而非 6 条样本
    from src.services import privacy as dp
    rows = ledger.zone_rows(session, '冷藏')
    assert dp.build_histogram(rows, 'exposure')['count_used'] == 2
    assert dp.build_histogram(rows, 'loss')['count_used'] == 2
    # 对外仅给加噪频数及其后处理总和
    assert all(m['noisy_total'] == sum(m['noisy_counts']) for m in rel.result['metrics'])
    assert all('raw_counts' not in m for m in rel.result['metrics'])