/**
 * Barrel de la entity `catalog-sync` — contrato público que consumen las
 * features del plugin catalog. Espeja el backend `/api/catalog/*`.
 */

export {
  syncStepStatusSchema,
  syncStatusSchema,
  syncStepSchema,
  syncDetailSchema,
  triggerSyncResponseSchema,
  syncHistoryItemSchema,
  syncHistoryResponseSchema,
  snapshotInfoSchema,
  type SyncStepStatus,
  type SyncStatus,
  type SyncStep,
  type SyncDetail,
  type TriggerSyncResponse,
  type SyncHistoryItem,
  type SyncHistoryResponse,
  type SnapshotInfo,
} from "./contracts";

export {
  SYNC_STEP_KEYS,
  isSyncActive,
  syncStatusLabel,
  syncStatusTone,
  formatRelativeTime,
  type SyncTone,
} from "./model";

export { catalogSyncKeys } from "./keys";

export {
  useSyncHistory,
  useSnapshotInfo,
  useSyncStatus,
  useTriggerSync,
} from "./api";
