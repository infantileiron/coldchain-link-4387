"""隐私预算账本：按温区共用累计额度，行锁保证并发不超支。

约定：
- charge（扣费）记正 epsilon，refund（退款）记负 epsilon；
- 两阶段：先持久化 draft + 扣费，再原子写对外文件并置 published，
  使“预算扣除”与“对外可见”保持一致，崩溃可由 startup 恢复；
- 同一幂等键重试只返回原发布单，不重复扣费、不重新加噪，
  并能自愈上一次中断在两阶段之间的 draft。
"""
import os
import json
import random
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from sqlalchemy import select
from fastapi import HTTPException
from src.constants.enums import (
    DEFAULT_ZONE_EPSILON, FROZEN_BINS, ZONES,
)
from src.models.entities import PrivacyBudget, PrivacyRelease, PrivacyLedger, Shipment, Sample
from src.services.business import dump
from src.services import privacy as dp

ARTIFACT_DIR = os.getenv('PRIVACY_ARTIFACT_DIR', '/artifacts/privacy')

# 应用级温区互斥：同一温区的发布申请串行化，配合 DB 行锁双重保证并发不超支。
# 按温区分锁，不同温区互不阻塞；进程内线程锁覆盖单实例，FOR UPDATE 覆盖多实例/MySQL。
_zone_locks = {}
_zone_locks_guard = threading.Lock()


def _zone_lock(zone):
    with _zone_locks_guard:
        lk = _zone_locks.get(zone)
        if lk is None:
            lk = threading.RLock()
            _zone_locks[zone] = lk
        return lk


@contextmanager
def zone_mutex(zone):
    lk = _zone_lock(zone)
    lk.acquire()
    try:
        yield
    finally:
        lk.release()


def now():
    return datetime.now(timezone.utc).isoformat()


def artifact_path(release_id):
    return os.path.join(ARTIFACT_DIR, f'release-{release_id}.json')


def ensure_budgets(s):
    """为每个温区初始化一条共用预算记录（幂等）。"""
    existing = {b.zone: b for b in s.query(PrivacyBudget).all()}
    for z in ZONES:
        if z not in existing:
            s.add(PrivacyBudget(zone=z, total_epsilon=DEFAULT_ZONE_EPSILON, spent_epsilon=0.0))
    s.commit()


def zone_rows(s, zone):
    """取某温区下全部样本（内部计算用）。返回的是 dump，绝不可直接对外。"""
    ids = [r.id for r in s.query(Shipment).filter_by(zone=zone).all()]
    if not ids:
        return []
    return [dump(r) for r in s.query(Sample).filter(Sample.shipment_id.in_(ids)).all()]


def _write_ledger(s, zone, release_id, kind, epsilon, balance_after, actor, note):
    s.add(PrivacyLedger(
        zone=zone, release_id=release_id, kind=kind, epsilon=epsilon,
        balance_after=balance_after, actor=actor, note=note, created=now(),
    ))


def _write_artifact(release):
    """写对外文件。文件内容仅匿名分布，不含明细。原子替换，外部只见完整文件。"""
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    path = artifact_path(release.id)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(dp.public_payload(release), f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def _lock_budget(s, zone):
    return s.execute(
        select(PrivacyBudget).where(PrivacyBudget.zone == zone).with_for_update()
    ).scalar_one()


def _artifact_intact(release):
    """对外文件是否存在且完整可解析。"""
    path = artifact_path(release.id)
    if not os.path.exists(path):
        return False, path
    try:
        with open(path, encoding='utf-8') as f:
            json.load(f)
        return True, path
    except (OSError, ValueError):
        return False, path


def request_release(s, zone, epsilon, requester, idempotency_key, rng=None):
    """申请一次匿名发布（带温区互斥锁，并发安全）。返回 (发布单, 是否新建)。"""
    with zone_mutex(zone):
        ensure_budgets(s)

        # 1) 幂等：同键重试只返回原发布单，不重复扣费/加噪；
        #    若上一次中断在两阶段之间（draft），重试驱动它继续完成或退款。
        existing = s.query(PrivacyRelease).filter_by(idempotency_key=idempotency_key).first()
        if existing:
            if existing.state == 'draft':
                try:
                    _resolve_draft(s, existing)
                except OSError:
                    _refund_release(s, existing)
                    raise HTTPException(500, '匿名文件写入失败，已退还预算，请重试')
            return existing, False

        if epsilon <= 0:
            raise HTTPException(422, '隐私预算必须为正数')

        # 2) 锁定该温区预算行，杜绝并发超支（应用锁 + FOR UPDATE 双保险）
        budget = _lock_budget(s, zone)
        remaining = budget.total_epsilon - budget.spent_epsilon
        if epsilon > remaining + 1e-12:
            raise HTTPException(
                402, f'{zone} 温区隐私预算余额不足：需要 {epsilon:.4f}，剩余 {remaining:.4f}'
            )

        # 3) 阶段一：建 draft、冻结口径聚合 + 拉普拉斯加噪、扣费记账并持久化。
        #    此刻预算已扣除，但尚未对外可见（state=draft，无可用下载）。
        release = PrivacyRelease(
            idempotency_key=idempotency_key, zone=zone, requester=requester,
            state='draft', epsilon_spent=epsilon, bin_version=dp.BIN_VERSION,
            bins=FROZEN_BINS, result=None, created=now(),
        )
        s.add(release)
        s.flush()  # 取 release.id
        release.result = dp.anonymize(zone_rows(s, zone), epsilon, rng or random.Random())
        budget.spent_epsilon += epsilon
        _write_ledger(s, zone, release.id, 'charge', epsilon,
                      budget.total_epsilon - budget.spent_epsilon,
                      requester, f'匿名发布申请 #{release.id} 扣费')
        s.commit()

        # 4) 阶段二：原子写对外文件并置 published；写失败则退款，绝不为不可见结果收费。
        try:
            _resolve_draft(s, release)
        except OSError:
            _refund_release(s, release)
            raise HTTPException(500, '匿名文件写入失败，已退还预算，请重试')
        return release, True


def _resolve_draft(s, release):
    """把 draft 推进到 published：文件完整则直接确认，否则重写后确认。

    调用方需已持有该温区互斥锁。
    """
    _lock_budget(s, release.zone)
    intact, path = _artifact_intact(release)
    if not intact:
        path = _write_artifact(release)  # 断点在写文件之前/之中：重新原子写出
    release.artifact_path = path
    release.state = 'published'
    if not release.published:
        release.published = now()
    s.commit()


def _refund_release(s, release):
    """阶段二失败且未对外可见：退还额度、置 failed 并持久化。"""
    budget = _lock_budget(s, release.zone)
    _refund(s, budget, release)
    s.commit()


def recover_pending(s):
    """启动恢复：处理所有 draft（已扣费但未确认对外可见）的发布单。

    - 对外文件已存在且完整：补登为 published（扣费有效，与可见性一致）；
    - 否则尝试重写，仍失败：退还额度并置 failed（不为不可见结果收费）。
    """
    ensure_budgets(s)
    recovered = []
    for release in s.query(PrivacyRelease).filter_by(state='draft').all():
        with zone_mutex(release.zone):
            try:
                _resolve_draft(s, release)
                recovered.append((release.id, 'published'))
            except OSError:
                _refund_release(s, release)
                recovered.append((release.id, 'refunded'))
    return recovered


def _refund(s, budget, release):
    """退款：仅当发布未对外可见时调用。"""
    amount = release.epsilon_spent
    budget.spent_epsilon = max(0.0, budget.spent_epsilon - amount)
    _write_ledger(s, release.zone, release.id, 'refund', -amount,
                  budget.total_epsilon - budget.spent_epsilon,
                  'system', f'发布 #{release.id} 未对外可见，退还预算')
    release.state = 'failed'
    release.error = '发布过程中断且未对外可见，已退还隐私预算'


def set_budget(s, zone, total_epsilon, actor):
    """负责人调整某温区总预算（只能调整到不低于已消耗额度）。"""
    if total_epsilon <= 0:
        raise HTTPException(422, '总预算必须为正数')
    ensure_budgets(s)
    budget = _lock_budget(s, zone)
    if total_epsilon < budget.spent_epsilon - 1e-12:
        raise HTTPException(422, f'不能低于已消耗额度 {budget.spent_epsilon:.4f}')
    old = budget.total_epsilon
    budget.total_epsilon = total_epsilon
    s.commit()
    return budget, old


def ledger_view(s, zone=None):
    q = s.query(PrivacyLedger).order_by(PrivacyLedger.id.desc())
    if zone:
        q = q.filter_by(zone=zone)
    return [dump(r) for r in q.limit(200).all()]


def budgets_view(s):
    ensure_budgets(s)
    out = []
    for b in s.query(PrivacyBudget).order_by(PrivacyBudget.zone).all():
        d = dump(b)
        d['remaining_epsilon'] = round(b.total_epsilon - b.spent_epsilon, 6)
        out.append(d)
    return out
