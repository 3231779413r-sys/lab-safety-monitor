"use client";

import { useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { SnapshotViewer } from "./snapshot-viewer";
import { PPETags } from "./ppe-tags";
import { ViolationAlertDialogProps } from "./types";
import { formatPersonId } from "@/lib/formatters";
import {
  AlertTriangle,
  Clock,
  Video,
  User,
  CheckCircle2,
  XCircle,
  ExternalLink,
} from "lucide-react";
import { cn } from "@/lib/utils";

export function ViolationAlertDialog({
  isOpen,
  violation,
  onClose,
  onAcknowledge,
  onViewDetails,
}: ViolationAlertDialogProps) {
  if (!violation) return null;
  const cameraLabel =
    violation.camera_name ??
    (violation.camera_ids && violation.camera_ids.length > 0
      ? violation.camera_ids.join("、")
      : violation.camera_id ?? "");
  const personLabel = violation.person_name || formatPersonId(violation.person_id);

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <AnimatePresence mode="wait">
        {isOpen && violation && (
          <DialogContent className="sm:max-w-[600px] max-h-[90vh] overflow-y-auto">
            <DialogHeader className="space-y-4">
              <DialogTitle className="flex items-center gap-3 text-xl">
                <motion.div
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  className="flex items-center justify-center w-10 h-10 rounded-xl bg-danger/10"
                >
                  <AlertTriangle className="w-5 h-5 text-danger" />
                </motion.div>
                <span>{violation.title}</span>
              </DialogTitle>

              <DialogDescription className="sr-only">
                违规详情弹窗
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-6 py-4">
              {/* 快照查看器 */}
              <section>
                <h3 className="text-sm font-medium text-muted-foreground mb-3 flex items-center gap-2">
                  <Video className="w-4 h-4" />
                  违规快照
                </h3>
                <SnapshotViewer
                  snapshotPath={violation.snapshot_url ?? violation.snapshot_path ?? null}
                  videoPath={violation.video_url ?? null}
                  personId={violation.person_id ?? null}
                  timestamp={violation.timestamp}
                />
              </section>

              {/* 违规信息 */}
              <section className="grid grid-cols-2 gap-4">
                {/* 违规类型 */}
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <ShieldOffIcon className="w-4 h-4" />
                    缺失防护装备
                  </div>
                  <PPETags missingPPE={violation.missing_ppe ?? []} />
                </div>

                {/* 人员信息 */}
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <User className="w-4 h-4" />
                    人员信息
                  </div>
                  <div className="px-3 py-2 bg-muted/50 rounded-lg">
                    <span className="font-medium">
                      {personLabel}
                    </span>
                    {violation.person_id && (
                      <p className="text-xs text-muted-foreground mt-0.5">
                        ID: {violation.person_id}
                      </p>
                    )}
                  </div>
                </div>

                {/* 违规时间 */}
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Clock className="w-4 h-4" />
                    违规时间
                  </div>
                  <div className="px-3 py-2 bg-muted/50 rounded-lg">
                    <span className="font-medium">
                      {new Date(violation.timestamp).toLocaleString("zh-CN")}
                    </span>
                  </div>
                </div>

                {/* 摄像头 */}
                {cameraLabel ? (
                  <div className="space-y-2">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Video className="w-4 h-4" />
                      摄像头
                    </div>
                    <div className="px-3 py-2 bg-muted/50 rounded-lg">
                      <span className="font-medium">
                        {cameraLabel}
                      </span>
                    </div>
                  </div>
                ) : null}
              </section>

              {/* 违规消息 */}
              <section className="p-4 bg-danger/5 border border-danger/20 rounded-xl">
                <p className="text-sm text-foreground">{violation.message}</p>
              </section>

              {/* 事件ID */}
              {violation.event_id && (
                <p className="text-xs text-muted-foreground text-center">
                  事件ID: {violation.event_id}
                </p>
              )}
            </div>

            <DialogFooter className="gap-3 sm:gap-0">
              {onViewDetails && violation.event_id && (
                <Button
                  variant="outline"
                  onClick={() => onViewDetails(violation.event_id!)}
                  className="flex items-center gap-2"
                >
                  <ExternalLink className="w-4 h-4" />
                  查看详情
                </Button>
              )}
              <div className="flex-1" />
              <Button
                variant="outline"
                onClick={onClose}
                className="flex items-center gap-2"
              >
                <XCircle className="w-4 h-4" />
                稍后处理
              </Button>
              <Button
                onClick={() => {
                  onAcknowledge(violation);
                  onClose();
                }}
                className="flex items-center gap-2 bg-success hover:bg-success/90"
              >
                <CheckCircle2 className="w-4 h-4" />
                确认已处理
              </Button>
            </DialogFooter>
          </DialogContent>
        )}
      </AnimatePresence>
    </Dialog>
  );
}

function ShieldOffIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10" />
      <line x1="4" x2="20" y1="4" y2="20" />
    </svg>
  );
}
