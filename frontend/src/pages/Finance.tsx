import { useEffect, useState, type FormEvent } from 'react'
import { api, type Page } from '../lib/api'
import type { MedicineOut } from '../lib/types'
import { Alert, Field, fmt, Spinner, useToast } from '../components/ui'

type Mode = 'stock-in' | 'sales' | 'expenses'
export default function Finance({ mode }: { mode: Mode }) {
  const { element: toast, show } = useToast()
  const [search,setSearch]=useState('')
  const [results,setResults]=useState<MedicineOut[]>([])
  const [medicine,setMedicine]=useState<MedicineOut|null>(null)
  const [quantity,setQuantity]=useState('')
  const [price,setPrice]=useState('')
  const [supplier,setSupplier]=useState('')
  const [reference,setReference]=useState('')
  const [category,setCategory]=useState('Transport')
  const [description,setDescription]=useState('')
  const [date,setDate]=useState(new Date().toISOString().slice(0,16))
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState<string|null>(null)
  const title=mode==='stock-in'?'Receive stock':mode==='sales'?'Record medicine sale':'Operating expenses'
  useEffect(()=>{
    if(!search.trim()){setResults([]);return}
    const timer=setTimeout(()=>{api.get<Page<MedicineOut>>('/api/medicines?search='+encodeURIComponent(search)+'&page_size=8&is_active=true').then(r=>setResults(r.items)).catch(e=>setError(e.message))},250)
    return ()=>clearTimeout(timer)
  },[search])
  async function submit(e:FormEvent){
    e.preventDefault();setBusy(true);setError(null)
    try{
      const occurred_at=new Date(date).toISOString()
      if(mode==='expenses'){
        await api.post('/api/finance/expenses',{category,description,amount:Number(price),occurred_at})
      }else{
        if(!medicine)throw new Error('Select a medicine first')
        await api.post(mode==='stock-in'?'/api/finance/stock-in':'/api/finance/sales',{
          medicine_id:medicine.id,quantity:Number(quantity),
          ...(mode==='stock-in'?{unit_cost:Number(price),supplier:supplier||null,reference_number:reference||null}:{unit_price:Number(price)}),
          occurred_at
        })
      }
      show(mode==='stock-in'?'Stock receipt saved':mode==='sales'?'Sale recorded':'Expense recorded')
      setMedicine(null);setSearch('');setQuantity('');setPrice('');setSupplier('');setReference('');setDescription('')
    }catch(err){setError(err instanceof Error?err.message:'Could not save record')}
    finally{setBusy(false)}
  }
  return <div>{toast}<div className="page-header"><div><h1>{title}</h1><p>{mode==='stock-in'?'Record supplier deliveries and purchase cost. Stock increases automatically.':mode==='sales'?'Record medicine and quantity sold. Stock decreases after validation.':'Record running costs separately from stock losses and adjustments.'}</p></div></div>
  {error&&<Alert kind="error">{error}</Alert>}
  <div className="card"><form onSubmit={submit}><div className="grid grid-2">
  {mode==='expenses'?<><Field label="Expense category"><select value={category} onChange={e=>setCategory(e.target.value)}>{['Transport','Electricity','Rent','Water','Salaries','Other'].map(x=><option key={x}>{x}</option>)}</select></Field><Field label="Description"><input value={description} onChange={e=>setDescription(e.target.value)} required maxLength={500}/></Field></>:<>
  <div><Field label="Medicine" hint="Search by medicine name or generic name."><input value={medicine?medicine.name:search} onChange={e=>{setMedicine(null);setSearch(e.target.value)}} required={!medicine}/></Field>
  {!medicine&&results.length>0&&<div className="table-wrap mb"><table><tbody>{results.map(m=><tr key={m.id}><td>{m.name}<div className="muted">{m.strength||''} · stock {fmt(m.expected_stock)} {m.base_unit}</div></td><td><button type="button" className="btn btn-sm btn-ghost" onClick={()=>setMedicine(m)}>Select</button></td></tr>)}</tbody></table></div>}</div>
  <Field label="Quantity"><input type="number" min="0.001" step="any" value={quantity} onChange={e=>setQuantity(e.target.value)} required/></Field>
  {mode==='stock-in'&&<><Field label="Supplier"><input value={supplier} onChange={e=>setSupplier(e.target.value)} maxLength={250}/></Field><Field label="Reference / invoice"><input value={reference} onChange={e=>setReference(e.target.value)} maxLength={120}/></Field></>}
  </>}
  <Field label={mode==='stock-in'?'Purchase unit cost (TZS)':mode==='sales'?'Selling unit price (TZS)':'Amount (TZS)'}><input type="number" min="0" step="0.01" value={price} onChange={e=>setPrice(e.target.value)} required/></Field>
  <Field label="Date and time"><input type="datetime-local" value={date} onChange={e=>setDate(e.target.value)} required/></Field>
  </div><button className="btn" disabled={busy|| (mode!=='expenses'&&!medicine)}>{busy?'Saving…':'Save record'}</button></form></div></div>
}
