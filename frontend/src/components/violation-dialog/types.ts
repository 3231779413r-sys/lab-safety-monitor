import type { AlertMessage } from "@/providers/websocket-provider";

export interface ViolationAlertMessage extends AlertMessage {
  snapshot_path?: string;
  snapshot_url?: string;
  video_url?: string;
  camera_id?: string;
  camera_ids?: string[];
  camera_name?: string;
}

export interface ViolationDialogState {
  isOpen: boolean;
  currentViolation: ViolationAlertMessage | null;
  pendingViolations: ViolationAlertMessage[];
}

export interface SnapshotViewerProps {
  snapshotPath: string | null;
  videoPath?: string | null;
  personId: string | null;
  timestamp: string;
  isLoading?: boolean;
}

export interface PPETagsProps {
  missingPPE: string[];
  maxDisplay?: number;
}

export interface ViolationAlertDialogProps {
  isOpen: boolean;
  violation: ViolationAlertMessage | null;
  onClose: () => void;
  onAcknowledge: (violation: ViolationAlertMessage) => void;
  onViewDetails?: (eventId: string) => void;
}

export interface UseViolationDialogOptions {
  autoOpen?: boolean;
  maxQueueSize?: number;
  autoAcknowledgeDelay?: number;
}

export interface UseViolationDialogReturn {
  currentViolation: ViolationAlertMessage | null;
  pendingViolations: ViolationAlertMessage[];
  isDialogOpen: boolean;
  handleNext: () => void;
  handleAcknowledge: (violation: ViolationAlertMessage) => void;
  handleClose: () => void;
  handleDismiss: () => void;
  clearAll: () => void;
}
