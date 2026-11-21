from pydantic import BaseModel,Field
from typing import Optional,Literal
class Login(BaseModel):
 email:str;password:str
class ShipmentInput(BaseModel):
 code:str=Field(min_length=1,max_length=60);route:str=Field(min_length=1,max_length=60);zone:Literal['冷藏','冷冻','常温对照'];driver:str=Field(min_length=1,max_length=60)
class SampleInput(BaseModel):
 shipment_id:int;code:str=Field(min_length=1,max_length=60);exposure:Optional[float]=Field(default=None,ge=0,le=1440);excursions:Optional[int]=Field(default=None,ge=0,le=100);loss:Optional[float]=Field(default=None,ge=0,le=100)
class StudyInput(BaseModel):
 name:str=Field(min_length=1,max_length=100);zone:Literal['冷藏','冷冻','常温对照'];feature:Literal['exposure','excursions']='exposure'
class RecommendationInput(BaseModel):
 study_id:int;note:str=Field(min_length=10,max_length=2000);owner:str=Field(min_length=1,max_length=60)
class ReleaseInput(BaseModel):
 zone:Literal['冷藏','冷冻','常温对照'];epsilon:float=Field(gt=0,le=10)
 # 幂等键由前端生成（uuid），同一申请重试复用该键即可获得原结果
 idempotency_key:str=Field(min_length=8,max_length=80)
class BudgetInput(BaseModel):
 zone:Literal['冷藏','冷冻','常温对照'];total_epsilon:float=Field(gt=0,le=1000)
