from sqlalchemy import Column,Integer,String,Float,JSON,Text,UniqueConstraint
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True);password=Column(String(200));role=Column(String(20))
class Shipment(Base):
 __tablename__='shipments'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True);route=Column(String(60));zone=Column(String(20));driver=Column(String(60))
class Sample(Base):
 __tablename__='samples'
 id=Column(Integer,primary_key=True);shipment_id=Column(Integer,index=True);code=Column(String(60),unique=True);exposure=Column(Float,nullable=True);excursions=Column(Integer,nullable=True);loss=Column(Float,nullable=True)
class Study(Base):
 __tablename__='studies'
 id=Column(Integer,primary_key=True);name=Column(String(100));zone=Column(String(20));feature=Column(String(30));state=Column(String(20),default='queued');result=Column(JSON,nullable=True);error=Column(Text,default='')
class Recommendation(Base):
 __tablename__='recommendations'
 id=Column(Integer,primary_key=True);study_id=Column(Integer);note=Column(Text);owner=Column(String(60));status=Column(String(20),default='draft')
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
class PrivacyBudget(Base):
 __tablename__='privacy_budgets'
 # 每个温区共用一条累计预算记录
 id=Column(Integer,primary_key=True);zone=Column(String(20),unique=True)
 total_epsilon=Column(Float);spent_epsilon=Column(Float,default=0.0)
class PrivacyRelease(Base):
 __tablename__='privacy_releases'
 id=Column(Integer,primary_key=True)
 # 幂等键：同一申请（申请人+温区+键）重试只返回原结果，不重复扣费/加噪
 idempotency_key=Column(String(80),unique=True)
 zone=Column(String(20),index=True);requester=Column(String(100))
 state=Column(String(20),default='draft')
 epsilon_spent=Column(Float,default=0.0)
 # 冻结口径的版本与具体分箱/裁剪，发布后不可变
 bin_version=Column(String(40));bins=Column(JSON)
 # 加噪后的匿名结果，仅含分箱频数，不含任何班次/样本明细
 result=Column(JSON,nullable=True)
 artifact_path=Column(String(200),default='');error=Column(Text,default='')
 created=Column(String(40));published=Column(String(40),nullable=True)
class PrivacyLedger(Base):
 __tablename__='privacy_ledger'
 id=Column(Integer,primary_key=True)
 zone=Column(String(20),index=True);release_id=Column(Integer,index=True)
 # charge 为扣费（正数 epsilon），refund 为退款（负 epsilon）
 kind=Column(String(20));epsilon=Column(Float);balance_after=Column(Float)
 actor=Column(String(100));note=Column(String(200));created=Column(String(40))
