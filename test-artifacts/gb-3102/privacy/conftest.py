"""共享测试夹具：用 SQLite + 临时对外目录，无需 MySQL/Redis。

注意：SQLite 不支持 SELECT ... FOR UPDATE（方言会忽略），并发不超支的
行锁保证在 MySQL 上成立；测试中通过串行调用与余额断言验证账务正确性，
另用多线程压测验证最终余额绝不超支。
"""
import os
import tempfile

os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('DATABASE_URL', 'sqlite://')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.config.database as dbmod


@pytest.fixture()
def artifact_dir(monkeypatch):
    d = tempfile.mkdtemp(prefix='privacy-artifacts-')
    monkeypatch.setattr('src.services.ledger.ARTIFACT_DIR', d)
    return d


@pytest.fixture()
def session(artifact_dir):
    engine = create_engine(
        'sqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    # 让业务代码里 import 的 Session/engine 指向测试库
    dbmod.engine = engine
    dbmod.Session = TestSession
    from src.models import entities
    entities.Base.metadata.create_all(engine)
    with TestSession() as s:
        yield s
    engine.dispose()


def make_zone_data(s, zone='冷藏', n=20, with_none=True):
    """造一个温区的班次与样本。"""
    from src.models.entities import Shipment, Sample
    ship = Shipment(code=f'S-{zone}', route='线路X', zone=zone, driver='司机A')
    s.add(ship)
    s.flush()
    for i in range(n):
        exposure = None if (with_none and i == 0) else float(10 + i * 3)
        loss = None if (with_none and i == 1) else float(1 + (i % 5))
        s.add(Sample(shipment_id=ship.id, code=f'{zone}-S{i}',
                     exposure=exposure, excursions=i, loss=loss))
    s.commit()
    return ship
