import { useCallback, useEffect, useState } from 'react'
import { api, type Page } from '../lib/api'
import type { StockLevelRow, VarianceReportRow } from '../lib/types'
import { Alert, Pagination, Spinner, StatusBadge, fmt } from '../components/ui'

type FinanceSummary = {
  different_medicine_types_received: number
  total_units_received: number
  total_units_sold: number
  sales_revenue: number
  purchase_cost: number
  operating_expenses: number
  gross_margin_before_operating_expenses: number
  net_after_purchase_cost_and_operating_expenses: number
}
type Receipt = { id:number; medicine_name:string; quantity:number; unit_cost:number; purchase_total:number; supplier:string|null; reference_number:string|null; occurred_at:string }
type Sale = { id:number; medicine_name:string; quantity:number; unit_price:number; revenue:number; occurred_at:string }
type Expense = { id:number; category:string; description:string; amount:number; occurred_at:string }

export default function Reports() {
  const [tab, setTab] = useState<'variance' | 'levels' | 'finance'>('variance')
  const [rows, setRows] = useState<Page<VarianceReportRow> | Page<StockLevelRow> | null>(null)
  const [page, setPage] = useState(1)
  const [classFilter, setClassFilter] = useState<'ALL' | 'SHORTAGE' | 'SURPLUS' | 'MATCH' | 'UNCOUNTED'>('ALL')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [month,setMonth]=useState(new Date().toISOString().slice(0,7))
  const [summary,setSummary]=useState<FinanceSummary|null>(null)
  const [receipts,setReceipts]=useState<Receipt[]>([])
  const [sales,setSales]=useState<Sale[]>([])
  const [expenses,setExpenses]=useState<Expense[]>([])

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      if (tab === 'finance') {
        const start = new Date(month+'-01T00:00:00').toISOString()
        const next = new Date(new Date(month+'-01T00:00:00').getTime()).toISOString()
        const [sum,rec,sale,exp] = await Promise.all([
          api.get<FinanceSummary>(`/api/finance/summary?start=${encodeURIComponent(start)}&end=${encodeURIComponent(new Date(new Date(month+'-01T00:00:00').setMonth(new Date(month+'-01T00:00:00').getMonth()+1)).toISOString())}`),
          api.get<Receipt[]>(`/api/finance/receipts?start=${encodeURIComponent(start)}&end=${encodeURIComponent(new Date(new Date(month+'-01T00:00:00').setMonth(new Date(month+'-01T00:00:00').getMonth()+1)).toISOString())}`),
          api.get<Sale[]>(`/api/finance/sales?start=${encodeURIComponent(start)}&end=${encodeURIComponent(new Date(new Date(month+'-01T00:00:00').setMonth(new Date(month+'-01T00:00:00').getMonth()+1)).toISOString())}`),
          api.get<Expense[]>(`/api/finance/expenses?start=${encodeURIComponent(start)}&end=${encodeURIComponent(new Date(new Date(month+'-01T00:00:00').setMonth(new Date(month+'-01T00:00:00').getMonth()+1)).toISOString())}`)
        ])
        setSummary(sum);setReceipts(rec);setSales(sale);setExpenses(exp)
      } else if (tab === 'variance') {
        const res = await api.get<Page<VarianceReportRow>>(`/api/reports/variance?page=${page}&page_size=50`)
        const filtered = classFilter === 'ALL' ? res.items : res.items.filter((r) => r.classification === classFilter)
        setRows({ ...res, items: filtered })
      } else {
        setRows(await api.get<Page<StockLevelRow>>(`/api/reports/stock-levels?page=${page}&page_size=50`))
      }
    } catch (err) { setError(err instanceof Error ? err.message : 'Failed to load report') }
    finally { setLoading(false) }
  }, [tab,page,classFilter,month])

  useEffect(() => { void load() }, [load])
  const money=(v:number)=>'TZS '+fmt(v,2)
  const date=(v:string)=>new Date(v).toLocaleString()

  return <div>
    <div className="page-header"><div><h1>Reports</h1><p>Stock variance, stock levels, receipts, sales and operating costs.</p></div>
      <div className="btn-row">
        <button className={`btn ${tab==='variance'?'':'btn-ghost'}`} onClick={()=>{setTab('variance');setPage(1)}}>Variance report</button>
        <button className={`btn ${tab==='levels'?'':'btn-ghost'}`} onClick={()=>{setTab('levels');setPage(1)}}>Stock levels</button>
        <button className={`btn ${tab==='finance'?'':'btn-ghost'}`} onClick={()=>setTab('finance')}>Finance & receipts</button>
      </div>
    </div>
    {error&&<Alert kind="error">{error}</Alert>}
    {tab==='finance'?<div>
      <div className="toolbar"><label className="field"><span className="field-label">Month</span><input type="month" value={month} onChange={e=>setMonth(e.target.value)}/></label></div>
      {loading?<Spinner/>:summary&&<>
        <div className="grid grid-2">
          {[
            ['Different medicine types received',summary.different_medicine_types_received],
            ['Total units received',summary.total_units_received],
            ['Total units sold',summary.total_units_sold],
            ['Sales revenue',money(summary.sales_revenue)],
            ['Medicine purchase cost',money(summary.purchase_cost)],
            ['Operating expenses',money(summary.operating_expenses)],
            ['Revenue less purchase cost',money(summary.gross_margin_before_operating_expenses)],
            ['After purchase cost and expenses',money(summary.net_after_purchase_cost_and_operating_expenses)]
          ].map(([label,value])=><div className="card" key={String(label)}><div className="muted">{label}</div><h2>{value}</h2></div>)}
        </div>
        <div className="card"><h2>Stock received</h2><p className="muted">Medicine, units, supplier and purchase price. Showing up to 500 records.</p><div className="table-wrap"><table><thead><tr><th>Date</th><th>Medicine</th><th>Quantity</th><th>Supplier</th><th>Unit cost</th><th>Total</th></tr></thead><tbody>{receipts.map(r=><tr key={r.id}><td>{date(r.occurred_at)}</td><td>{r.medicine_name}</td><td>{fmt(r.quantity)}</td><td>{r.supplier||'—'}</td><td>{money(r.unit_cost)}</td><td>{money(r.purchase_total)}</td></tr>)}</tbody></table></div>{!receipts.length&&<p className="muted">No receipts for this month.</p>}</div>
        <div className="card"><h2>Medicine sales</h2><div className="table-wrap"><table><thead><tr><th>Date</th><th>Medicine</th><th>Quantity</th><th>Unit price</th><th>Revenue</th></tr></thead><tbody>{sales.map(r=><tr key={r.id}><td>{date(r.occurred_at)}</td><td>{r.medicine_name}</td><td>{fmt(r.quantity)}</td><td>{money(r.unit_price)}</td><td>{money(r.revenue)}</td></tr>)}</tbody></table></div>{!sales.length&&<p className="muted">No sales for this month.</p>}</div>
        <div className="card"><h2>Operating expenses</h2><div className="table-wrap"><table><thead><tr><th>Date</th><th>Category</th><th>Description</th><th>Amount</th></tr></thead><tbody>{expenses.map(r=><tr key={r.id}><td>{date(r.occurred_at)}</td><td>{r.category}</td><td>{r.description}</td><td>{money(r.amount)}</td></tr>)}</tbody></table></div>{!expenses.length&&<p className="muted">No expenses for this month.</p>}</div>
      </>}
    </div>:<div className="card">
      {tab==='variance'&&<div className="toolbar"><label className="field"><span className="field-label">Classification</span><select value={classFilter} onChange={e=>{setClassFilter(e.target.value as typeof classFilter);setPage(1)}}><option value="ALL">All</option><option value="SHORTAGE">Shortage</option><option value="SURPLUS">Surplus</option><option value="MATCH">Match</option><option value="UNCOUNTED">Uncounted</option></select></label></div>}
      {loading&&!rows?<Spinner/>:tab==='variance'?<>
        <div className="table-wrap"><table><thead><tr><th>Medicine</th><th>Opening</th><th>Recd</th><th>Issued</th><th>± Adj</th><th>Expected</th><th>Physical</th><th>Variance</th><th>Class</th><th>Operator / Reviewer</th><th>Session</th></tr></thead><tbody>{((rows as Page<VarianceReportRow>)?.items??[]).map(r=><tr key={r.medicine_id}><td><strong>{r.medicine_name}</strong>{r.generic_name&&<div className="muted">{r.generic_name}</div>}</td><td>{fmt(r.opening_stock,0)}</td><td>{fmt(r.received,0)}</td><td>{fmt(r.issued,0)}</td><td>{fmt(Number(r.adjustments_positive)-Number(r.adjustments_negative),0)}</td><td><strong>{fmt(r.expected_stock)}</strong></td><td>{fmt(r.physical_stock)}</td><td>{r.variance==null?'—':<strong>{Number(r.variance)>0?'+':''}{fmt(r.variance)}</strong>}</td><td><StatusBadge value={r.classification}/></td><td className="muted">{r.counting_operator??'—'}{r.reviewer&&<> / {r.reviewer}</>}</td><td className="mono">{r.counting_session_code??'—'}</td></tr>)}</tbody></table></div>
        {rows&&<Pagination page={rows.page} pageSize={rows.page_size} total={rows.total} onPage={setPage}/>}
      </>:<>
        <div className="table-wrap"><table><thead><tr><th>Medicine</th><th>Base unit</th><th>Expected stock</th><th>Reorder level</th><th>Status</th></tr></thead><tbody>{((rows as Page<StockLevelRow>)?.items??[]).map(r=><tr key={r.medicine_id}><td>{r.medicine_name}</td><td>{r.base_unit}s</td><td><strong>{fmt(r.expected_stock)}</strong></td><td>{fmt(r.reorder_level)}</td><td>{r.below_reorder?<span className="badge badge-danger">BELOW REORDER</span>:<span className="badge badge-success">OK</span>}</td></tr>)}</tbody></table></div>
        {rows&&<Pagination page={rows.page} pageSize={rows.page_size} total={rows.total} onPage={setPage}/>}
      </>}
    </div>}
  </div>
}
