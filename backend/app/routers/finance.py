"""Stock receipts, medicine sales and operating expenses."""
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session as DbSession
from app.database import get_db, utcnow
from app.deps import require_permission
from app.errors import ApiError
from app.audit import log_action
from app.models.medicines import Medicine
from app.models.inventory import InventoryTransaction, TransactionType
from app.models.finance import StockReceipt, MedicineSale, OperatingExpense
from app.models.users import User
from app.schemas.finance import ReceiptCreate, SaleCreate, ExpenseCreate

router=APIRouter(prefix="/api/finance",tags=["finance"])
def _record(db, request, user, action, entity, row, data):
    log_action(db,action=action,entity_type=entity,entity_id=row.id,actor=user,after=data,request=request)

@router.post("/stock-in",status_code=201)
def stock_in(p:ReceiptCreate,request:Request,db:DbSession=Depends(get_db),user:User=Depends(require_permission("inventory:transact"))):
    m=db.get(Medicine,p.medicine_id)
    if not m or not m.is_active: raise ApiError(404,"Active medicine not found","not_found")
    at=p.occurred_at or utcnow()
    r=StockReceipt(medicine_id=m.id,quantity=p.quantity,unit_cost=p.unit_cost,supplier=p.supplier,reference_number=p.reference_number,occurred_at=at,recorded_by=user.id)
    t=InventoryTransaction(medicine_id=m.id,transaction_type=TransactionType.RECEIVED.value,quantity=p.quantity,occurred_at=at,recorded_by=user.id,reference_number=p.reference_number,notes=f"Supplier: {p.supplier or 'Not specified'}; unit cost: {p.unit_cost}")
    db.add_all([r,t]); db.flush(); _record(db,request,user,"stock.received","stock_receipt",r,{"medicine_id":m.id,"quantity":str(p.quantity),"unit_cost":str(p.unit_cost),"supplier":p.supplier}); db.commit()
    return {"id":r.id,"medicine":m.name,"quantity":r.quantity,"unit_cost":r.unit_cost,"supplier":r.supplier,"occurred_at":r.occurred_at}

@router.post("/sales",status_code=201)
def sale(p:SaleCreate,request:Request,db:DbSession=Depends(get_db),user:User=Depends(require_permission("inventory:transact"))):
    m=db.execute(select(Medicine).where(Medicine.id==p.medicine_id).with_for_update()).scalar_one_or_none()
    if not m or not m.is_active: raise ApiError(404,"Active medicine not found","not_found")
    q=db.execute(select(func.coalesce(func.sum(InventoryTransaction.quantity * __import__('sqlalchemy').case((InventoryTransaction.transaction_type.in_([TransactionType.OPENING.value,TransactionType.RECEIVED.value,TransactionType.RETURN_IN.value,TransactionType.ADJUSTMENT_POSITIVE.value]),1),else_=-1)),0)).where(InventoryTransaction.medicine_id==m.id)).scalar_one()
    if Decimal(str(q)) < p.quantity: raise ApiError(409,"Insufficient stock for this sale","insufficient_stock")
    at=p.occurred_at or utcnow()
    s=MedicineSale(medicine_id=m.id,quantity=p.quantity,unit_price=p.unit_price,occurred_at=at,recorded_by=user.id)
    t=InventoryTransaction(medicine_id=m.id,transaction_type=TransactionType.ISSUED.value,quantity=p.quantity,occurred_at=at,recorded_by=user.id,notes=f"Sale at unit price {p.unit_price}")
    db.add_all([s,t]); db.flush(); _record(db,request,user,"medicine.sold","medicine_sale",s,{"medicine_id":m.id,"quantity":str(p.quantity),"unit_price":str(p.unit_price)}); db.commit()
    return {"id":s.id,"medicine":m.name,"quantity":s.quantity,"unit_price":s.unit_price,"revenue":s.quantity*s.unit_price,"occurred_at":s.occurred_at}

@router.post("/expenses",status_code=201)
def expense(p:ExpenseCreate,request:Request,db:DbSession=Depends(get_db),user:User=Depends(require_permission("inventory:transact"))):
    e=OperatingExpense(category=p.category.strip(),description=p.description.strip(),amount=p.amount,occurred_at=p.occurred_at or utcnow(),recorded_by=user.id)
    db.add(e); db.flush(); _record(db,request,user,"expense.recorded","operating_expense",e,{"category":e.category,"amount":str(e.amount)}); db.commit()
    return {"id":e.id,"category":e.category,"description":e.description,"amount":e.amount,"occurred_at":e.occurred_at}

@router.get("/summary")
def summary(start:datetime|None=None,end:datetime|None=None,db:DbSession=Depends(get_db),user:User=Depends(require_permission("report:view"))):
    def total(model,col):
        q=select(func.coalesce(func.sum(col),0))
        if start:q=q.where(model.occurred_at>=start)
        if end:q=q.where(model.occurred_at<end)
        return Decimal(str(db.execute(q).scalar_one()))
    receipts=total(StockReceipt,StockReceipt.quantity)
    sales=total(MedicineSale,MedicineSale.quantity)
    revenue=total(MedicineSale,MedicineSale.quantity*MedicineSale.unit_price)
    purchases=total(StockReceipt,StockReceipt.quantity*StockReceipt.unit_cost)
    expenses=total(OperatingExpense,OperatingExpense.amount)
    typesq=select(func.count(func.distinct(StockReceipt.medicine_id)))
    for model in [StockReceipt]:
        if start:typesq=typesq.where(model.occurred_at>=start)
        if end:typesq=typesq.where(model.occurred_at<end)
    return {"different_medicine_types_received":db.execute(typesq).scalar_one(),"total_units_received":receipts,"total_units_sold":sales,"sales_revenue":revenue,"purchase_cost":purchases,"operating_expenses":expenses,"gross_margin_before_operating_expenses":revenue-purchases,"net_after_purchase_cost_and_operating_expenses":revenue-purchases-expenses}
