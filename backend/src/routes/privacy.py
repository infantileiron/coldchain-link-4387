import os
import json
from fastapi import APIRouter, Depends, HTTPException, Response
from src.config.database import db
from src.models.entities import PrivacyRelease
from src.schemas.business import ReleaseInput, BudgetInput
from src.middlewares.auth import current, editor, admin
from src.services.business import audit, dump
from src.services import ledger
from src.services import privacy as dp

router = APIRouter(prefix='/privacy')


@router.get('/budgets')
def budgets(u=Depends(current), s=Depends(db)):
    """查账：各温区总额度、已消耗、剩余额度。"""
    return ledger.budgets_view(s)


@router.put('/budgets')
def set_budget(data: BudgetInput, u=Depends(admin), s=Depends(db)):
    """仅负责人可设置某温区累计隐私预算。"""
    row, old = ledger.set_budget(s, data.zone, data.total_epsilon, u.email)
    audit(s, u, f'设置 {data.zone} 温区预算 {old:.4f} -> {data.total_epsilon:.4f}')
    return dump(row)


@router.get('/releases')
def releases(u=Depends(current), s=Depends(db)):
    """发布单列表：对外字段仅匿名分布，不含明细。"""
    rows = s.query(PrivacyRelease).order_by(PrivacyRelease.id.desc()).all()
    return [dp.public_payload(r) for r in rows]


@router.post('/releases')
def request_release(data: ReleaseInput, u=Depends(editor), s=Depends(db)):
    """申请匿名发布。同 idempotency_key 重试返回原结果，不重复扣费/加噪。"""
    release, created = ledger.request_release(
        s, data.zone, data.epsilon, u.email, data.idempotency_key
    )
    if created:
        audit(s, u, f'匿名发布 #{release.id}（{data.zone}）扣费 {data.epsilon:.4f}')
    return dp.public_payload(release)


@router.get('/releases/{i}')
def release_detail(i: int, u=Depends(current), s=Depends(db)):
    row = s.get(PrivacyRelease, i)
    if not row:
        raise HTTPException(404, '发布单不存在')
    return dp.public_payload(row)


@router.get('/releases/{i}/download')
def release_download(i: int, u=Depends(current), s=Depends(db)):
    """下载对外匿名文件。只有 published（已对外可见）才可下载。"""
    row = s.get(PrivacyRelease, i)
    if not row:
        raise HTTPException(404, '发布单不存在')
    if row.state != 'published':
        raise HTTPException(409, '发布未成功或未对外可见，无法下载')
    # 下载可见性与扣费一致：能下载即已扣费；重新读取已写文件，内容仅匿名分布
    if not row.artifact_path or not os.path.exists(row.artifact_path):
        raise HTTPException(409, '对外文件尚不可用')
    with open(row.artifact_path, encoding='utf-8') as f:
        body = f.read()
    return Response(
        body, media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="privacy-release-{i}.json"'},
    )


@router.get('/ledger')
def privacy_ledger(zone: str | None = None, u=Depends(admin), s=Depends(db)):
    """逐笔 epsilon 借贷流水（查账），仅负责人。"""
    return ledger.ledger_view(s, zone)
