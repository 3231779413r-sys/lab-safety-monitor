"use client";

import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { ComplianceEvent, Camera, resolveApiAssetUrl } from "@/lib/api";
import { formatSafetyLabel, formatPersonId } from "@/lib/formatters";
import { Clock, User, AlertTriangle, Video, ImageIcon, ZoomIn } from "lucide-react";
import { cn } from "@/lib/utils";

interface EventsTableProps {
  events: ComplianceEvent[];
  cameras?: Camera[];
  loading?: boolean;
}

interface SnapshotPreviewState {
  url: string;
  overlay?: ComplianceEvent["snapshot_overlay"];
}

function SnapshotOverlayPreview({
  url,
  overlay,
}: {
  url: string;
  overlay?: ComplianceEvent["snapshot_overlay"];
}) {
  const [imageLoaded, setImageLoaded] = useState(false);
  const aspectRatio = useMemo(() => {
    if (!overlay?.image_width || !overlay?.image_height) {
      return undefined;
    }
    return `${overlay.image_width} / ${overlay.image_height}`;
  }, [overlay]);

  const getStrokeClassName = (kind: string) => {
    if (kind === "person") {
      return "stroke-sky-400";
    }
    if (kind === "action_violation") {
      return "stroke-amber-400";
    }
    return "stroke-rose-500";
  };

  const getLabelClassName = (kind: string) => {
    if (kind === "person") {
      return "fill-sky-400";
    }
    if (kind === "action_violation") {
      return "fill-amber-400";
    }
    return "fill-rose-500";
  };

  return (
    <div
      className="relative mx-auto w-full overflow-hidden rounded-lg bg-black"
      style={aspectRatio ? { aspectRatio } : undefined}
    >
      <img
        src={url}
        alt="违规快照大图"
        className="max-h-[80vh] w-full object-contain"
        onLoad={() => setImageLoaded(true)}
      />
      {imageLoaded && overlay?.boxes?.length ? (
        <svg
          viewBox={`0 0 ${overlay.image_width} ${overlay.image_height}`}
          className="pointer-events-none absolute inset-0 h-full w-full"
          preserveAspectRatio="xMidYMid meet"
        >
          {overlay.boxes.map((item, index) => {
            const [x1, y1, x2, y2] = item.box;
            const width = x2 - x1;
            const height = y2 - y1;
            const labelY = Math.max(20, y1 - 8);
            return (
              <g key={`${item.kind}-${item.label}-${index}`}>
                <rect
                  x={x1}
                  y={y1}
                  width={width}
                  height={height}
                  fill="none"
                  strokeWidth={3}
                  className={getStrokeClassName(item.kind)}
                />
                <rect
                  x={x1}
                  y={labelY - 18}
                  width={Math.max(72, item.label.length * 16)}
                  height={20}
                  rx={4}
                  className={getLabelClassName(item.kind)}
                />
                <text
                  x={x1 + 8}
                  y={labelY - 4}
                  fill="white"
                  fontSize={14}
                  fontWeight={600}
                >
                  {item.label}
                </text>
              </g>
            );
          })}
        </svg>
      ) : null}
    </div>
  );
}

export function EventsTable({ events, cameras, loading = false }: EventsTableProps) {
  const [previewImage, setPreviewImage] = useState<SnapshotPreviewState | null>(null);
  const [previewVideo, setPreviewVideo] = useState<string | null>(null);
  const resolveSnapshotUrl = (url: string) => {
    return resolveApiAssetUrl(url) || url;
  };
  const resolveVideoUrl = (url: string) => {
    return resolveApiAssetUrl(url) || url;
  };

  // Create camera lookup map
  const cameraMap = new Map(cameras?.map((c) => [c.id, c]) ?? []);
  const getCameraName = (cameraId: string | null) => {
    if (!cameraId) return null;
    return cameraMap.get(cameraId)?.name ?? cameraId.slice(0, 8);
  };
  const resolveEventCameraName = (event: ComplianceEvent) => {
    if (event.camera_name !== undefined && event.camera_name !== null) {
      return event.camera_name;
    }
    if (event.camera_ids && event.camera_ids.length > 0) {
      return event.camera_ids
        .map((cameraId) => getCameraName(cameraId) || cameraId)
        .join("、");
    }
    if (event.camera_id) {
      return getCameraName(event.camera_id);
    }
    return null;
  };
  if (loading) {
    return (
      <div className="space-y-3">
        {[...Array(5)].map((_, i) => (
          <div
            key={i}
            className="flex items-center gap-4 p-4 rounded-xl border border-border/50"
          >
            <Skeleton className="h-10 w-10 rounded-xl shimmer" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-4 w-48 shimmer" />
              <Skeleton className="h-3 w-32 shimmer" />
            </div>
            <Skeleton className="h-6 w-20 rounded-full shimmer" />
          </div>
        ))}
      </div>
    );
  }

  if (events.length === 0) {
    return null;
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow className="bg-muted/30 hover:bg-muted/30">
            <TableHead className="text-center font-semibold">时间</TableHead>
            <TableHead className="text-center font-semibold">摄像头</TableHead>
            <TableHead className="text-center font-semibold">人员</TableHead>
            <TableHead className="w-[60px] text-center font-semibold">快照</TableHead>
            <TableHead className="w-[60px] text-center font-semibold">视频</TableHead>
            <TableHead className="text-center font-semibold">违规</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {events.map((event) => (
            <TableRow
              key={event.id}
              className={cn(
                "transition-colors hover:bg-muted/50",
                event.is_violation && "hover:bg-danger/5"
              )}
            >
              <TableCell className="text-center">
                <div className="flex items-center justify-center gap-2 text-center">
                  <div
                    className={cn(
                      "flex items-center justify-center w-8 h-8 rounded-lg",
                      event.is_violation ? "bg-danger/10" : "bg-success/10"
                    )}
                  >
                    <Clock
                      className={cn(
                        "w-4 h-4",
                        event.is_violation ? "text-danger" : "text-success"
                      )}
                    />
                  </div>
                  <div className="text-center">
                    <p className="font-medium text-sm">
                      {new Date(event.timestamp).toLocaleTimeString()}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {new Date(event.timestamp).toLocaleDateString()}
                    </p>
                  </div>
                </div>
              </TableCell>
              <TableCell className="text-center">
                {resolveEventCameraName(event) ? (
                  <div className="flex items-center justify-center gap-2 text-center">
                    <div className="flex items-center justify-center w-7 h-7 rounded-full bg-primary/10">
                      <Video className="w-3.5 h-3.5 text-primary" />
                    </div>
                    <span className="font-medium text-sm">
                      {resolveEventCameraName(event)}
                    </span>
                  </div>
                ) : (
                  <span className="text-sm text-muted-foreground">-</span>
                )}
              </TableCell>
              <TableCell className="text-center">
                <div className="flex items-center justify-center gap-2 text-center">
                  <div className="flex items-center justify-center w-7 h-7 rounded-full bg-muted">
                    <User className="w-3.5 h-3.5 text-muted-foreground" />
                  </div>
                  <span className="font-medium text-sm">
                    {event.person_name || formatPersonId(event.person_id)}
                  </span>
                </div>
              </TableCell>
              <TableCell className="text-center">
                {event.snapshot_url ? (
                  <div
                    className="group relative mx-auto cursor-pointer"
                    onClick={() =>
                      setPreviewImage({
                        url: resolveSnapshotUrl(event.snapshot_url!),
                        overlay: event.snapshot_overlay,
                      })
                    }
                  >
                    <img
                      src={resolveSnapshotUrl(event.snapshot_url)}
                      alt="快照"
                      className="w-10 h-10 rounded-md object-cover border border-border/50 group-hover:ring-2 ring-primary transition-all"
                      loading="lazy"
                    />
                    <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-black/30 rounded-md">
                      <ZoomIn className="w-3.5 h-3.5 text-white" />
                    </div>
                  </div>
                ) : (
                  <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-md border border-border/30 bg-muted/30">
                    <ImageIcon className="w-4 h-4 text-muted-foreground/50" />
                  </div>
                )}
              </TableCell>
              <TableCell className="text-center">
                {event.video_url ? (
                  <button
                    type="button"
                    className="group relative mx-auto flex h-10 w-10 items-center justify-center rounded-md border border-border/50 bg-primary/5 transition hover:bg-primary/10 hover:ring-2 hover:ring-primary"
                    onClick={() => setPreviewVideo(resolveVideoUrl(event.video_url!))}
                  >
                    <Video className="h-4 w-4 text-primary" />
                  </button>
                ) : (
                  <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-md border border-border/30 bg-muted/30">
                    <Video className="h-4 w-4 text-muted-foreground/50" />
                  </div>
                )}
              </TableCell>
              <TableCell className="text-center">
                <div className="mx-auto flex max-w-[200px] flex-wrap justify-center gap-1.5">
                  {/* Missing PPE violations */}
                  {event.missing_ppe.length > 0 &&
                    event.missing_ppe.map((ppe) => (
                      <Badge
                        key={ppe}
                        variant="danger-soft"
                        className="text-[10px]"
                      >
                        {formatSafetyLabel(ppe)}
                      </Badge>
                    ))}
                  {/* Special violations such as fall detection */}
                  {event.action_violations &&
                    event.action_violations.length > 0 &&
                    event.action_violations.map((action) => (
                      <Badge
                        key={action}
                        variant="warning-soft"
                        className="text-[10px]"
                      >
                        {formatSafetyLabel(action)}
                      </Badge>
                    ))}
                  {/* Show compliant if no violations */}
                  {event.missing_ppe.length === 0 &&
                    (!event.action_violations ||
                      event.action_violations.length === 0) && (
                      <Badge variant="success-soft" className="text-[10px]">
                        全部正常
                      </Badge>
                    )}
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {/* Snapshot Preview Dialog */}
      <Dialog open={!!previewImage} onOpenChange={() => setPreviewImage(null)}>
        <DialogContent className="max-w-4xl p-2 bg-black/95 border-white/10" aria-describedby={undefined}>
          <DialogTitle className="sr-only">违规快照预览</DialogTitle>
          {previewImage ? (
            <SnapshotOverlayPreview
              url={previewImage.url}
              overlay={previewImage.overlay}
            />
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog open={!!previewVideo} onOpenChange={() => setPreviewVideo(null)}>
        <DialogContent className="max-w-4xl border-white/10 bg-black/95 p-2" aria-describedby={undefined}>
          <DialogTitle className="sr-only">违规视频预览</DialogTitle>
          {previewVideo ? (
            <video
              src={previewVideo}
              controls
              preload="metadata"
              className="max-h-[85vh] w-full rounded-lg bg-black"
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
