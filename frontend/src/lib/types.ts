export interface Stats {
  accounts: number
  exports_total: number
  exports_running: number
  storage_bytes: number
}

export interface Account {
  id: number
  phone: string
  status: string
  last_error: string | null
  last_checked_at: string | null
  created_at: string
}

export interface ChatResult {
  id: number
  title: string
  type: string
  username: string | null
}

export interface ExportJob {
  id: number
  account_id: number
  chat_id: number
  chat_title: string
  chat_type: string
  format: string
  status: string
  messages_processed: number
  total_messages_est: number | null
  files_downloaded: number
  files_total: number
  speed_mps: number
  eta_seconds: number | null
  export_dir: string | null
  error: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface ExportProgress {
  status: string
  percent: number | null
  messages_processed: number
  total_messages_est: number | null
  files_downloaded: number
  files_total: number
  speed_mps: number
  eta_seconds: number | null
  error: string | null
}

export interface PreviewMessage {
  id: number
  date: string | null
  sender: string | null
  text: string | null
  media: string[] | null
  reply_to: number | null
}

export interface ExportPreview {
  export_id: number
  status: string
  total_messages: number
  partial: boolean
  messages: PreviewMessage[]
}

export interface MigrationJob {
  id: number
  chat_export_id: number
  format: string
  status: string
  messages_converted: number
  media_copied: number
  output_dir: string | null
  error: string | null
  created_at: string
  finished_at: string | null
}

export interface ImportPackage {
  id: number
  migration_job_id: number | null
  name: string
  package_path: string
  format: string
  messages_count: number
  media_count: number
  users_detected: Record<string, string> | null
  date_min: string | null
  date_max: string | null
  validation_status: string
  validation_report: Record<string, unknown> | null
  created_at: string
}

export interface ValidationResult {
  validation_status: string
  issues: string[]
  stats: { messages: number; media: number; users: string[]; date_min: string | null; date_max: string | null }
}

export interface Instruction {
  step: string
  title: string
  detail: string
}

export interface PeerInfo {
  peer_id: number | null
  peer_type: string | null
  username: string | null
  title: string | null
  mutual_contact: boolean | null
  message_count: number | null
}

export interface PeerValidationResult {
  allowed: boolean
  confirm_text: string
  error_code: string | null
  error_message: string | null
  peer: PeerInfo
}

export type ImportJobStatus =
  | 'queued'
  | 'validating'
  | 'peer_checking'
  | 'import_initialized'
  | 'media_uploading'
  | 'starting_import'
  | 'waiting'
  | 'verifying'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'cancelled'

export interface ImportJobPublic {
  id: number
  source_export_id: number
  target_account_id: number
  target_peer_id: number | null
  message_limit: number
  status: ImportJobStatus
  options: Record<string, unknown>
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
  progress: Record<string, unknown> | null
}