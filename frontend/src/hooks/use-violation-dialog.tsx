"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useWebSocket } from "@/providers/websocket-provider";
import { toast, Toaster } from "sonner";
import Image from "next/image";
import { ViolationAlertMessage, UseViolationDialogOptions, UseViolationDialogReturn } from "@/components/violation-dialog/types";
import { AlertTriangle, Check, X } from "lucide-react";
import { formatSafetyLabel } from "@/lib/formatters";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { resolveApiAssetUrl } from "@/lib/api";

function getSnapshotUrl(snapshotPath?: string) {
  return resolveApiAssetUrl(snapshotPath);
}

// 自定义违规 Toast 组件
function ViolationToastContent({
  violation,
  onAcknowledge,
  onDismiss,
}: {
  violation: ViolationAlertMessage;
  onAcknowledge: () => void;
  onDismiss: () => void;
}) {
  const snapshotUrl = getSnapshotUrl(violation.snapshot_url || violation.snapshot_path);

  return (
    <div className="flex gap-3">
      {/* 快照图片 */}
      {snapshotUrl && (
        <div className="relative w-24 h-16 rounded-lg overflow-hidden shrink-0 bg-muted">
          <Image
            src={snapshotUrl}
            alt="违规快照"
            fill
            className="object-cover"
            unoptimized
          />
          <div className="absolute bottom-1 right-1 px-1 py-0.5 bg-black/70 text-white text-[9px] rounded">
            {new Date(violation.timestamp).toLocaleTimeString()}
          </div>
        </div>
      )}

      {/* 违规信息 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-foreground">{violation.title}</p>
            <p className="text-xs text-muted-foreground mt-0.5">{violation.message}</p>

            {/* 违规类型标签 */}
            <div className="flex flex-wrap gap-1 mt-2">
              {violation.missing_ppe?.slice(0, 2).map((ppe) => (
                <Badge key={ppe} variant="destructive" className="text-[10px] px-1.5 py-0">
                  {formatSafetyLabel(ppe)}
                </Badge>
              ))}
              {(violation.missing_ppe?.length || 0) > 2 && (
                <span className="text-[10px] text-muted-foreground">
                  +{(violation.missing_ppe?.length || 0) - 2}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* 操作按钮 */}
        <div className="flex gap-2 mt-3">
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs flex-1"
            onClick={onDismiss}
          >
            <X className="w-3 h-3 mr-1" />
            稍后
          </Button>
          <Button
            size="sm"
            className="h-7 text-xs bg-success hover:bg-success/90 flex-1"
            onClick={onAcknowledge}
          >
            <Check className="w-3 h-3 mr-1" />
            处理
          </Button>
        </div>
      </div>
    </div>
  );
}

// 导出 Toaster 组件，放在页面中使用
export { Toaster };

export function useViolationDialog(
  options: UseViolationDialogOptions = {}
): UseViolationDialogReturn {
  const {
    maxQueueSize = 50,
  } = options;

  const { lastMessage } = useWebSocket();

  const [pendingViolations, setPendingViolations] = useState<ViolationAlertMessage[]>([]);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [currentViolation, setCurrentViolation] = useState<ViolationAlertMessage | null>(null);
  const shownToastEventIdsRef = useRef<Set<string>>(new Set());

  const handleAcknowledge = useCallback((violation: ViolationAlertMessage) => {
    toast.success("违规已确认处理", {
      description: `事件 ${violation.event_id?.slice(0, 8) || "N/A"} 已标记为已处理`,
    });
  }, []);

  useEffect(() => {
    if (lastMessage && (lastMessage.type === "violation" || lastMessage.type === "violation_update")) {
      const violation = lastMessage as ViolationAlertMessage;
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPendingViolations((current) => {
        if (violation.event_id) {
          const existingIndex = current.findIndex((v) => v.event_id === violation.event_id);
          if (existingIndex >= 0) {
            const nextQueue = [...current];
            nextQueue[existingIndex] = {
              ...nextQueue[existingIndex],
              ...violation,
            };
            return nextQueue;
          }
        }
        return [violation, ...current].slice(0, maxQueueSize);
      });

      if (violation.type !== "violation") {
        return;
      }

      const toastId = violation.event_id || `violation-${violation.timestamp}`;
      if (shownToastEventIdsRef.current.has(toastId)) {
        return;
      }
      shownToastEventIdsRef.current.add(toastId);

      toast.custom(
        (t) => (
          <ViolationToastContent
            violation={violation}
            onAcknowledge={() => {
              handleAcknowledge(violation);
              toast.dismiss(t);
            }}
            onDismiss={() => toast.dismiss(t)}
          />
        ),
        {
          id: toastId,
          duration: 10000,
          className: "bg-card border-danger/30",
        }
      );
    }
  }, [lastMessage, maxQueueSize, handleAcknowledge]);

  const handleClose = useCallback(() => {
    setIsDialogOpen(false);
    if (currentViolation) {
      setPendingViolations((prev) => [currentViolation, ...prev]);
      setCurrentViolation(null);
    }
  }, [currentViolation]);

  const handleDismiss = useCallback(() => {
    setIsDialogOpen(false);
    setCurrentViolation(null);
  }, []);

  const handleNext = useCallback(() => {
    setIsDialogOpen(false);
    setCurrentViolation(null);
  }, []);

  const clearAll = useCallback(() => {
    setPendingViolations([]);
    setCurrentViolation(null);
    setIsDialogOpen(false);
  }, []);

  return {
    currentViolation,
    pendingViolations,
    isDialogOpen,
    handleNext,
    handleAcknowledge,
    handleClose,
    handleDismiss,
    clearAll,
  };
}
