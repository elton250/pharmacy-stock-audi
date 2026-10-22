// Types mirroring the backend API schemas.

export interface RoleOut {
  id: number
  name: string
  description: string
  permissions: string[]
}

export interface UserOut {
  id: number
  username: string
  full_name: string
  email?: string | null
  is_active: boolean
  must_change_password: boolean
  roles: RoleOut[]
  created_at: string
}

export interface BarcodeOut {
  id: number
  medicine_id: number
  barcode: string
  is_primary: boolean
  note?: string | null
}

export interface PackageOut {
  id: number
  medicine_id: number
  name: string
  parent_id?: number | null
  units_per_parent: string
  sort_order: number
  units_in_base?: string | null
}

export interface MedicineOut {
  id: number
  name: string
  generic_name?: string | null
  brand_name?: string | null
  strength?: string | null
  dosage_form?: string | null
  manufacturer?: string | null
  category?: string | null
  base_unit: string
  opening_stock: string
  reorder_level: string
  is_active: boolean
  created_at: string
  updated_at: string
  barcodes: BarcodeOut[]
  packages: PackageOut[]
  expected_stock?: string | null
}

export interface ScanResult {
  found: boolean
  message?: string | null
  medicine?: MedicineOut | null
  barcode?: string | null
}

export interface InventoryTransactionOut {
  id: number
  medicine_id: number
  medicine_name?: string | null
  transaction_type: string
  signed_quantity: string
  quantity: string
  occurred_at: string
  recorded_by?: number | null
  recorded_by_username?: string | null
  reference_number?: string | null
  notes?: string | null
}

export interface CountEventLineOut {
  id: number
  package_config_id?: number | null
  package_name?: string | null
  quantity: string
  partial_units?: string | null
  base_units: string
  is_partial_package: boolean
}

export interface CountEventOut {
  id: number
  session_id: number
  medicine_id: number
  operator_id: number
  operator_username?: string | null
  total_base_units: string
  idempotency_key: string
  notes?: string | null
  created_at: string
  lines: CountEventLineOut[]
}

export interface CountingSessionOut {
  id: number
  code: string
  status: string
  active_medicine_id?: number | null
  active_medicine_name?: string | null
  active_medicine_barcode?: string | null
  stock_audit_id?: number | null
  created_by: number
  creator_username?: string | null
  submitted_at?: string | null
  reviewed_by?: number | null
  reviewer_username?: string | null
  review_decision?: string | null
  review_notes?: string | null
  review_count: number
  completed_at?: string | null
  cancelled_at?: string | null
  cancelled_by?: number | null
  cancelled_by_username?: string | null
  notes?: string | null
  created_at: string
  updated_at: string
  total_counted_base_units?: string | null
  archived_at?: string | null
  archived_by?: number | null
  archived_by_username?: string | null
  archive_reason?: string | null
  active_medicine?: MedicineOut | null
  events: CountEventOut[]
}

export interface StockAuditItemOut {
  id: number
  medicine_id: number
  medicine_name?: string | null
  medicine_generic?: string | null
  expected_stock: string
  physical_stock?: string | null
  variance?: string | null
  classification?: string | null
  notes?: string | null
  /** User id of the operator who recorded the count (the counter). */
  counted_by?: number | null
  /** User id of the user who approved the session (the reviewer). */
  reviewed_by?: number | null
  counter_username?: string | null
  reviewer_username?: string | null
  counted_at?: string | null
}

export interface StockAuditOut {
  id: number
  code: string
  title: string
  status: string
  audit_date: string
  notes?: string | null
  initiated_by: number
  initiator_username?: string | null
  completed_at?: string | null
  created_at: string
  items: StockAuditItemOut[]
}

export interface VarianceReportRow {
  medicine_id: number
  medicine_name: string
  generic_name?: string | null
  base_unit: string
  opening_stock: string
  received: string
  issued: string
  returns_in: string
  returns_out: string
  adjustments_positive: string
  adjustments_negative: string
  expected_stock: string
  physical_stock?: string | null
  variance?: string | null
  classification: string
  counting_operator?: string | null
  reviewer?: string | null
  audit_date?: string | null
  counting_session_code?: string | null
  notes?: string | null
}

export interface StockLevelRow {
  medicine_id: number
  medicine_name: string
  base_unit: string
  expected_stock: string
  reorder_level: string
  below_reorder: boolean
  is_active: boolean
}

export interface AuditLogModel {
  id: number
  actor_id?: number | null
  actor_username?: string | null
  action: string
  entity_type: string
  entity_id?: string | null
  before_data?: Record<string, unknown> | null
  after_data?: Record<string, unknown> | null
  ip_address?: string | null
  created_at: string
}

// ---------------------------------------------------------------- vision (2A)

export type VisionMatchType = 'barcode_exact' | 'barcode_partial' | 'name'

export interface VisionCandidate {
  medicine_id: number
  name: string
  generic_name?: string | null
  brand_name?: string | null
  strength?: string | null
  barcode?: string | null
  match_type: VisionMatchType
  score: number
}

export interface VisionCapture {
  capture_id: string
  status: string
  ocr_text: string
  ocr_confidence?: number | null
  engine?: string | null
  engine_available: boolean
  candidates: VisionCandidate[]
  matched_medicine_id?: number | null
  created_at?: string | null
  expires_at?: string | null
  confirmed_at?: string | null
  image_deleted: boolean
}

export interface VisionStatus {
  enabled: boolean
  engine: string
  engine_available: boolean
  message?: string | null
  max_upload_bytes: number
  retention_days: number
}
