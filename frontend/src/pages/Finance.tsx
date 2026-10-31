import { useEffect, useState, type FormEvent } from 'react'
import { api, type Page } from '../lib/api'
import type { MedicineOut, PackageOut, ScanResult } from '../lib/types'
import { Alert, Field, fmt, useToast } from '../components/ui'

type Mode = 'stock-in' | 'sales' | 'expenses'
export default function Finance({ mode }: { mode: Mode }) {
  const { element: toast, show } = useToast()
  const [search,setSearch]=useState('')
  const [barcode,setBarcode]=useState('')
  const [results,setResults]=useState<MedicineOut[]>([])
  const [medicine,setMedicine]=useState<MedicineOut|null>(null)
  const [packageId,setPackageId]=useState('')
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
  const selectedPackage: PackageOut|undefined=medicine?.packages.find(p=>String(p.id)===packageId)
  const unitsPerPackage=selectedPackage ? Number(selectedPackage.units_in_base ?? selectedPackage.units_per_parent) : 1
  const baseQuantity=Number(quantity||0)*unitsPerPackage

  useEffect(()=>{
    if(!search.trim()){setResults([]);return}
    const timer=setTimeout(()=>{api.get<Page<MedicineOut>>('/api/medicines?search='+encodeURIComponent(search)+'&page_size=8&is_active=true').then(r=>setResults(r.items)).catch(e=>setError(e.message))},250)
    return ()=>clearTimeout(timer)
  },[search])

  function chooseMedicine(m:MedicineOut){
    setMedicine(m);setSearch(m.name);setResults([])
    setPackageId(m.packages.length===1?String(m.packages[0].id):'')
    setQuantity('')
  }
  async function scanBarcode(){
    const code=barcode.trim()
    if(!code)return
    setBusy(true);setError(null)
    try{
      const result=await api.get<ScanResult>('/api/medicines/scan?barcode='+encodeURIComponent(code))
      if(!result.found||!result.medicine)throw new Error(result.message||'Barcode not recognised')
      chooseMedicine(result.medicine)
      setBarcode('')
      show('Medicine identified: '+result.medicine.name)
    }catch(err){setError(err instanceof Error?err.message:'Barcode scan failed')}
    finally{setBusy(false)}
  }
  async function submit(e:FormEvent){
    e.preventDefault();setBusy(true);setError(null)
    try{
      const occurred_at=new Date(date).toISOString()
      if(mode==='expenses'){
        await api.post('/api/finance/expenses',{category,description,amount:Number(price),occurred_at})
      }else{
        if(!medicine)throw new Error('Select or scan a medicine first')
        if(!Number.isFinite(baseQuantity)||baseQuantity<=0)throw new Error('Enter a valid quantity')
        await api.post(mode==='stock-in'?'/api/finance/stock-in':'/api/finance/sales',{
          medicine_id:medicine.id,quantity:baseQuantity,
          ...(mode==='stock-in'?{unit_cost:Number(price),supplier:supplier||null,reference_number:reference||null}:{unit_price:Number(price)}),
          occurred_at
        })
      }
      show(mode==='stock-in'?'Stock receipt saved':mode==='sales'?'Sale recorded':'Expense recorded')
      setMedicine(null);setSearch('');setQuantity('');setPrice('');setSupplier('');setReference('');setDescription('');setPackageId('')
    }catch(err){setError(err instanceof Error?err.message:'Could not save record')}
    finally{setBusy(false)}
  }
  return <div>{toast}<div className="page-header"><div><h1>{title}</h1><p>{mode==='stock-in'?'Record supplier deliveries and purchase cost. Stock increases automatically.':mode==='sales'?'Record medicine and quantity sold. Stock decreases after validation.':'Record running costs separately from stock losses and adjustments.'}</p></div></div>
  {error&&<Alert kind="error">{error}</Alert>}
  <div className="card"><form onSubmit={submit}><div className="grid grid-2">
  {mode==='expenses'?<><Field label="Expense category"><select value={category} onChange={e=>setCategory(e.target.value)}>{['Transport','Electricity','Rent','Water','Salaries','Other'].map(x=><option key={x}>{x}</option>)}</select></Field><Field label="Description"><input value={description} onChange={e=>setDescription(e.target.value)} required maxLength={500}/></Field></>:<>
  <div>
    <Field label="Scan full-box barcode" hint="Use the handheld scanner; strips/blisters are counted with Vision Scan.">
      <input value={barcode} onChange={e=>setBarcode(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();void scanBarcode()}}} placeholder="Scan or type barcode" />
    </Field>
    <button type="button" className="btn btn-sm btn-ghost mb" disabled={busy||!barcode.trim()} onClick={()=>void scanBarcode()}>Identify barcode</button>
    <Field label="Medicine" hint="Search manually or identify it with the barcode above."><input value={medicine?medicine.name:search} onChange={e=>{setMedicine(null);setPackageId('');setSearch(e.target.value)}} required={!medicine}/></Field>
    {!medicine&&results.length>0&&<div className="table-wrap mb"><table><tbody>{results.map(m=><tr key={m.id}><td>{m.name}<div className="muted">{m.strength||''} · stock {fmt(m.expected_stock)} {m.base_unit}</div></td><td><button type="button" className="btn btn-sm btn-ghost" onClick={()=>chooseMedicine(m)}>Select</button></td></tr>)}</tbody></table></div>}
  </div>
  {medicine&&<>
    <Field label="Package scanned" hint="Choose the box configuration; quantity is converted to base units.">
      <select value={packageId} onChange={e=>setPackageId(e.target.value)}>
        <option value="">Loose {medicine.base_unit}s</option>
        {medicine.packages.map(p=><option key={p.id} value={p.id}>{p.name} (1 = {fmt(p.units_in_base??p.units_per_parent,0)} {medicine.base_unit}s)</option>)}
      </select>
    </Field>
    <Field label={selectedPackage?'Number of packages':'Quantity in '+medicine.base_unit+'s'}>
      <input type="number" min="0.001" step="any" value={quantity} onChange={e=>setQuantity(e.target.value)} required/>
    </Field>
    <div className="muted">Stock transaction quantity: <strong>{fmt(baseQuantity)} {medicine.base_unit}s</strong></div>
  </>}
  {mode==='stock-in'&&<><Field label="Supplier"><input value={supplier} onChange={e=>setSupplier(e.target.value)} maxLength={250}/></Field><Field label="Reference / invoice"><input value={reference} onChange={e=>setReference(e.target.value)} maxLength={120}/></Field></>}
  </>}
  <Field label={mode==='stock-in'?'Purchase cost per base unit (TZS)':mode==='sales'?'Selling price per base unit (TZS)':'Amount (TZS)'}><input type="number" min="0" step="0.01" value={price} onChange={e=>setPrice(e.target.value)} required/></Field>
  <Field label="Date and time"><input type="datetime-local" value={date} onChange={e=>setDate(e.target.value)} required/></Field>
  </div><button className="btn" disabled={busy|| (mode!=='expenses'&&(!medicine||!quantity))}>{busy?'Saving…':'Save record'}</button></form></div></div>
}
