"use client";

import Image from "next/image";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { ImageIcon, Video } from "lucide-react";
import { resolveApiAssetUrl } from "@/lib/api";

interface SnapshotViewerProps {
  snapshotPath: string | null;
  videoPath?: string | null;
  personId: string | null;
  timestamp: string;
  isLoading?: boolean;
}

export function SnapshotViewer({
  snapshotPath,
  videoPath,
  personId,
  timestamp,
  isLoading,
}: SnapshotViewerProps) {
  const [isImageLoading, setIsImageLoading] = useState(true);
  const [hasImageError, setHasImageError] = useState(false);
  const [hasVideoError, setHasVideoError] = useState(false);

  const snapshotUrl = resolveApiAssetUrl(snapshotPath);
  const videoUrl = resolveApiAssetUrl(videoPath);
  const showVideo = !!videoUrl && !hasVideoError;

  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-xl border border-border/50 bg-muted">
      {isLoading ? (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="flex flex-col items-center gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <span className="text-sm text-muted-foreground">加载媒体...</span>
          </div>
        </div>
      ) : showVideo ? (
        <>
          <video
            src={videoUrl}
            controls
            preload="metadata"
            className="h-full w-full bg-black object-contain"
            onError={() => setHasVideoError(true)}
          />
          <div className="absolute bottom-2 left-2 rounded-md bg-black/70 px-2 py-1 text-xs text-white">
            事件视频
          </div>
        </>
      ) : snapshotUrl && !hasImageError ? (
        <>
          {isImageLoading && (
            <div className="absolute inset-0 flex items-center justify-center bg-muted">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
          )}

          <Image
            src={snapshotUrl}
            alt={`危险行为快照 - ${personId || "未知人员"}`}
            fill
            className={cn(
              "object-contain transition-opacity duration-300",
              isImageLoading ? "opacity-0" : "opacity-100"
            )}
            onLoad={() => setIsImageLoading(false)}
            onError={() => {
              setHasImageError(true);
              setIsImageLoading(false);
            }}
            unoptimized
          />

          <div className="absolute bottom-2 left-2 rounded-md bg-black/70 px-2 py-1 text-xs text-white">
            事件快照
          </div>
        </>
      ) : (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-muted-foreground">
          {videoPath ? <Video className="h-12 w-12 opacity-30" /> : <ImageIcon className="h-12 w-12 opacity-30" />}
          <span className="text-sm">{videoPath ? "暂无视频" : "暂无快照"}</span>
        </div>
      )}

      <div className="absolute bottom-2 right-2 rounded-md bg-black/70 px-2 py-1 text-xs text-white">
        {new Date(timestamp).toLocaleString("zh-CN")}
      </div>
    </div>
  );
}
