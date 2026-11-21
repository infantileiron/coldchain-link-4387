"""HTTP 接口测试：鉴权、下载与扣费可见性一致、对外载荷无明细、viewer 只读。"""
import os
import json
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.config.database as dbmod
from src.models import entities
from src.utils.security import hash_password
from conftest import make_zone_data


@pytest.fixture()
def client(tmp_path, monkeypatch):
    art = tmp_path / 'privacy'
    monkeypatch.setattr('src.services.ledger.ARTIFACT_DIR', str(art))
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    dbmod.engine = engine
    dbmod.Session = S
    entities.Base.metadata.create_all(engine)
    with S() as s:
        for role in ('admin', 'analyst', 'viewer'):
            s.add(entities.User(email=f'{role}@demo.local', role=role,
                                password=hash_password('Demo123!')))
        make_zone_data(s, '冷藏', n=18)
        make_zone_data(s, '冷冻', n=6)

    from src.main import app
    with TestClient(app) as c:
        yield c, S, str(art)


def login(c, role):
    r = c.post('/auth/login', json={'email': f'{role}@demo.local', 'password': 'Demo123!'})
    return r.json()['token']


def auth(tok):
    return {'Authorization': f'Bearer {tok}'}


def test_full_flow_budgets_release_download_ledger(client):
    c, S, art = client
    admin = login(c, 'admin')
    analyst = login(c, 'analyst')

    # 初始预算
    buds = c.get('/privacy/budgets', headers=auth(admin)).json()
    cold = next(b for b in buds if b['zone'] == '冷藏')
    assert cold['remaining_epsilon'] == 4.0

    # 申请发布
    key = str(uuid.uuid4())
    r = c.post('/privacy/releases', headers=auth(analyst),
               json={'zone': '冷藏', 'epsilon': 1.0, 'idempotency_key': key})
    assert r.status_code == 200
    rel = r.json()
    assert rel['state'] == 'published'
    rid = rel['release_id']

    # 下载：与扣费一致，文件仅匿名分布
    d = c.get(f'/privacy/releases/{rid}/download', headers=auth(analyst))
    assert d.status_code == 200
    body = d.text
    for forbidden in ('S-冷藏', '司机A', '线路X', 'raw_counts', 'shipment_id'):
        assert forbidden not in body
    data = json.loads(body)
    metrics = {m['metric']: m for m in data['result']['metrics']}
    assert set(metrics) == {'exposure', 'loss'}  # 外部只需温控与货损分布

    # 余额已扣减
    buds = c.get('/privacy/budgets', headers=auth(admin)).json()
    cold = next(b for b in buds if b['zone'] == '冷藏')
    assert cold['spent_epsilon'] == pytest.approx(1.0)

    # 查账：admin 可见流水
    led = c.get('/privacy/ledger?zone=冷藏', headers=auth(admin)).json()
    assert any(x['kind'] == 'charge' for x in led)


def test_idempotent_retry_same_result(client):
    c, S, art = client
    analyst = login(c, 'analyst')
    key = str(uuid.uuid4())
    payload = {'zone': '冷藏', 'epsilon': 0.5, 'idempotency_key': key}
    r1 = c.post('/privacy/releases', headers=auth(analyst), json=payload).json()
    r2 = c.post('/privacy/releases', headers=auth(analyst), json=payload).json()
    assert r1['release_id'] == r2['release_id']
    assert r1['result'] == r2['result']
    admin = login(c, 'admin')
    led = c.get('/privacy/ledger', headers=auth(admin)).json()
    assert len([x for x in led if x['kind'] == 'charge']) == 1


def test_insufficient_budget_402(client):
    c, *_ = client
    analyst = login(c, 'analyst')
    # epsilon 在合法范围内（<=10），但超过该温区默认总预算 4.0
    r = c.post('/privacy/releases', headers=auth(analyst),
               json={'zone': '冷藏', 'epsilon': 5.0, 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 402
    assert '余额不足' in r.json()['detail']


def test_viewer_cannot_publish_or_set_budget(client):
    c, *_ = client
    viewer = login(c, 'viewer')
    r = c.post('/privacy/releases', headers=auth(viewer),
               json={'zone': '冷藏', 'epsilon': 1.0, 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 403
    r2 = c.put('/privacy/budgets', headers=auth(viewer),
               json={'zone': '冷藏', 'total_epsilon': 9})
    assert r2.status_code == 403
    # viewer 仍可只读查预算与发布列表
    assert c.get('/privacy/budgets', headers=auth(viewer)).status_code == 200
    assert c.get('/privacy/releases', headers=auth(viewer)).status_code == 200


def test_analyst_cannot_set_budget_only_admin(client):
    c, *_ = client
    analyst = login(c, 'analyst')
    r = c.put('/privacy/budgets', headers=auth(analyst),
              json={'zone': '冷藏', 'total_epsilon': 9})
    assert r.status_code == 403
    admin = login(c, 'admin')
    r2 = c.put('/privacy/budgets', headers=auth(admin),
               json={'zone': '冷藏', 'total_epsilon': 9})
    assert r2.status_code == 200
    assert r2.json()['total_epsilon'] == 9


def test_download_requires_published(client):
    c, S, art = client
    analyst = login(c, 'analyst')
    r = c.post('/privacy/releases', headers=auth(analyst),
               json={'zone': '冷藏', 'epsilon': 1.0, 'idempotency_key': str(uuid.uuid4())}).json()
    # 删除对外文件后下载应失败（未对外可见）
    path = os.path.join(art, f"release-{r['release_id']}.json")
    os.remove(path)
    d = c.get(f"/privacy/releases/{r['release_id']}/download", headers=auth(analyst))
    assert d.status_code == 409


def test_original_study_still_readable_and_contains_detail(client):
    """内部原研究仍可读，明细不受匿名发布影响。"""
    c, S, art = client
    analyst = login(c, 'analyst')
    # 直接插一条 done 研究（含快照明细）
    with S() as s:
        st = entities.Study(name='内部研究', zone='冷藏', feature='exposure',
                            state='done', result={'n': 1, 'snapshot': [{'code': 'X1'}]})
        s.add(st)
        s.commit()
        sid = st.id
    r = c.get(f'/studies/{sid}', headers=auth(analyst))
    assert r.status_code == 200
    assert r.json()['result']['snapshot'][0]['code'] == 'X1'  # 内部明细仍在


def test_unauthenticated_rejected(client):
    c, *_ = client
    assert c.get('/privacy/budgets').status_code == 401
    assert c.post('/privacy/releases', json={}).status_code == 401


def test_startup_lifespan_recovers_interrupted_draft(client):
    """应用重启（lifespan）时，中断在 draft 的发布单被自动恢复为可下载。"""
    c, S, art = client
    analyst = login(c, 'analyst')
    rel = c.post('/privacy/releases', headers=auth(analyst),
                 json={'zone': '冷藏', 'epsilon': 1.0,
                       'idempotency_key': str(uuid.uuid4())}).json()
    rid = rel['release_id']
    # 模拟崩溃：删文件、回退 draft
    os.remove(os.path.join(art, f'release-{rid}.json'))
    with S() as s:
        r = s.get(entities.PrivacyRelease, rid)
        r.state = 'draft'
        r.artifact_path = ''
        s.commit()
    # 重启应用，触发 lifespan 中的 recover_pending
    from src.main import app
    with TestClient(app) as c2:
        with S() as s:
            r = s.get(entities.PrivacyRelease, rid)
            assert r.state == 'published'
            assert os.path.exists(r.artifact_path)
        d = c2.get(f'/privacy/releases/{rid}/download', headers=auth(analyst))
        assert d.status_code == 200
