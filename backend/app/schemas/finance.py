"""Financial transaction schemas."""
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field
class ReceiptCreate(BaseModel):
    medicine_id:int
    quantity:Decimal=Field(gt=0,le=1_000_000_000)
    unit_cost:Decimal=Field(ge=0,le=1_000_000_000)
    supplier:str|None=Field(default=None,max_length=250)
    reference_number:str|None=Field(default=None,max_length=120)
    occurred_at:datetime|None=None
class SaleCreate(BaseModel):
    medicine_id:int
    quantity:Decimal=Field(gt=0,le=1_000_000_000)
    unit_price:Decimal=Field(ge=0,le=1_000_000_000)
    occurred_at:datetime|None=None
class ExpenseCreate(BaseModel):
    category:str=Field(min_length=1,max_length=100)
    description:str=Field(min_length=1,max_length=500)
    amount:Decimal=Field(ge=0,le=1_000_000_000)
    occurred_at:datetime|None=None
