"use client";

import Image from "next/image";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatSafetyLabel } from "@/lib/formatters";
import { AlertTriangle, X, Check, Clock, User, Video, ImageIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { ViolationAlertMessage } from "./types";
import { resolveApiAssetUrl } from "@/lib/api";

interface ViolationToastProps {
  violation: ViolationAlertMessage | null;
  isVisible: boolean;
  onDismiss: () => void;
  onAcknowledge: () => void;
}

export function ViolationToast({ violation, isVisible, onDismiss, onAcknowledge }: ViolationToastProps) {
  const [showDetails, setShowDetails] = useState(false);

  const snapshotUrl = resolveApiAssetUrl(violation?.snapshot_url || violation?.snapshot_path);

  return (
    <AnimatePresence>
      {isVisible && violation && (
        <motion.div
          initial={{ opacity: 0, x: 100, y: 0 }}
          animate={{ opacity: 1, x: 0, y: 0 }}
          exit={{ opacity: 0, x: 100 }}
          transition={{ type: "spring", damping: 20, stiffness: 300 }}
          className="fixed top-20 right-4 z-50 w-80"
        >
          <div className="bg-card border border-danger/30 rounded-xl shadow-2xl overflow-hidden">
            {/* Header */}
            <div className="flex items-center gap-2 px-3 py-2 bg-danger/10 border-b border-danger/20">
              <AlertTriangle className="w-4 h-4 text-danger" />
              <span className="text-sm font-semibold text-danger">安全违规告警</span>
              <button
                onClick={onDismiss}
                className="ml-auto p-1 hover:bg-danger/10 rounded transition-colors"
              >
                <X className="w-4 h-4 text-muted-foreground" />
              </button>
            </div>

            {/* Content */}
            <div className="p-3 space-y-3">
              {/* 快照预览 - 点击展开/收起 */}
              {snapshotUrl ? (
                <div
                  className="relative cursor-pointer overflow-hidden rounded-lg"
                  onClick={() => setShowDetails(!showDetails)}
                >
                  <div className="relative aspect-video bg-muted">
                    <Image
                      src={snapshotUrl}
                      alt="违规快照"
                      fill
                      className="object-cover"
                      unoptimized
                    />
                    {/* 时间戳水印 */}
                    <div className="absolute bottom-2 right-2 px-1.5 py-0.5 bg-black/70 text-white text-[10px] rounded">
                      {new Date(violation.timestamp).toLocaleTimeString()}
                    </div>
                  </div>
                  {/* 展开指示器 */}
                  <div className="absolute top-2 left-2 flex items-center gap-1 px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs rounded">
                    <ImageIcon className="w-3 h-3" />
                    点击查看详情
                  </div>
                </div>
              ) : (
                <div className="relative aspect-video bg-muted/50 rounded-lg flex items-center justify-center">
                  <div className="text-center">
                    <ImageIcon className="w-8 h-8 text-muted-foreground/50 mx-auto mb-1" />
                    <span className="text-xs text-muted-foreground">暂无快照</span>
                  </div>
                </div>
              )}

              {/* 详细信息 - 展开时显示 */}
              <AnimatePresence>
                {showDetails && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="space-y-2 overflow-hidden"
                  >
                    {/* 违规类型 */}
                    <div className="flex items-start gap-2">
                      <div className="flex items-center gap-1.5 text-xs text-muted-foreground shrink-0">
                        <AlertTriangle className="w-3 h-3" />
                        违规类型
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {violation.missing_ppe?.slice(0, 3).map((ppe) => (
                          <Badge key={ppe} variant="destructive" className="text-[10px] px-1.5 py-0">
                            {formatSafetyLabel(ppe)}
                          </Badge>
                        ))}
                        {(violation.missing_ppe?.length || 0) > 3 && (
                          <span className="text-[10px] text-muted-foreground">
                            +{(violation.missing_ppe?.length || 0) - 3}
                          </span>
                        )}
                      </div>
                    </div>

                    {/* 人员信息 */}
                    <div className="flex items-center gap-2">
                      <User className="w-3 h-3 text-muted-foreground" />
                      <span className="text-xs text-muted-foreground">人员:</span>
                      <span className="text-xs font-medium">{violation.person_id || "未知"}</span>
                    </div>

                    {/* 违规时间 */}
                    <div className="flex items-center gap-2">
                      <Clock className="w-3 h-3 text-muted-foreground" />
                      <span className="text-xs text-muted-foreground">时间:</span>
                      <span className="text-xs">{new Date(violation.timestamp).toLocaleString()}</span>
                    </div>

                    {/* 消息 */}
                    <p className="text-xs text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
                      {violation.message}
                    </p>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* 操作按钮 */}
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  className="flex-1 h-8 text-xs"
                  onClick={onDismiss}
                >
                  稍后处理
                </Button>
                <Button
                  size="sm"
                  className="flex-1 h-8 text-xs bg-success hover:bg-success/90"
                  onClick={() => {
                    onAcknowledge();
                    onDismiss();
                  }}
                >
                  <Check className="w-3 h-3 mr-1" />
                  确认处理
                </Button>
              </div>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
