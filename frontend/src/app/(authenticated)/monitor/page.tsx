"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  Camera,
  CameraOff,
  Clock,
  Radio,
  Shield,
  User,
  Users,
  Video,
} from "lucide-react";
import api, {
  Camera as CameraModel,
  ComplianceEvent,
  LivePersonOverlay,
  LivePersonOverlayResponse,
  resolveApiAssetUrl,
} from "@/lib/api";
import { formatPersonId, formatSafetyLabel } from "@/lib/formatters";
import { useCameras, useFloorActivitySnapshots, useLatestViolationSnapshots, useLivePeople, useRecentViolations } from "@/lib/queries";
import { AlertMessage, useWebSocket } from "@/providers/websocket-provider";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

type MonitorAlert = {
  id: string;
  violationType: string;
  timestamp: string;
  severity: "info" | "warning" | "error";
  cameraName: string;
  personName: string;
};

type FloorPanel = {
  floor: string;
  cameraId: string | null;
  cameraName: string | null;
  personCount: number;
  imageUrl: string | null;
  lastFrameAt: string | null;
  frameWidth: number;
  frameHeight: number;
  persons: LivePersonOverlay[];
};

const ALERT_RETENTION_MS = 60 * 60 * 1000;
const MAX_ALERTS = 10;
const FLOOR_OPTIONS = ["一楼", "二楼", "三楼", "四楼"] as const;
const SNAPSHOT_LIMIT = 6;
const MAIN_PEOPLE_REFRESH_MS = 333;

function parseTimestamp(value?: string | null) {
  if (!value) return 0;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function isRetainedAlert(timestamp: string, now = Date.now()) {
  const time = parseTimestamp(timestamp);
  return time > 0 && now - time <= ALERT_RETENTION_MS;
}

function mergeAlerts(alerts: MonitorAlert[], now = Date.now()) {
  const deduped = new Map<string, MonitorAlert>();
  alerts
    .filter((alert) => isRetainedAlert(alert.timestamp, now))
    .forEach((alert) => {
      deduped.set(alert.id, alert);
    });

  return Array.from(deduped.values())
    .sort((a, b) => parseTimestamp(b.timestamp) - parseTimestamp(a.timestamp))
    .slice(0, MAX_ALERTS);
}

function getViolationTypeFromEvent(event: ComplianceEvent) {
  const labels = event.violation_labels?.length
    ? event.violation_labels
    : [
        ...(event.missing_ppe || []).map(formatSafetyLabel),
        ...((event.action_violations || []).map(formatSafetyLabel)),
      ];
  return labels.join("、") || "危险行为";
}

function getViolationTypeFromAlert(alert: AlertMessage) {
  const labels = alert.violation_labels?.length
    ? alert.violation_labels
    : (alert.missing_ppe || []).map(formatSafetyLabel);
  return labels.join("、") || alert.title || "危险行为";
}

function mapRecentViolationToAlert(event: ComplianceEvent): MonitorAlert {
  return {
    id: event.id,
    violationType: getViolationTypeFromEvent(event),
    timestamp: event.timestamp,
    severity: "error",
    cameraName: event.camera_name ?? event.video_source ?? event.camera_id ?? "",
    personName: event.person_name || formatPersonId(event.person_id),
  };
}

function mapLiveAlertToMonitorAlert(alert: AlertMessage): MonitorAlert {
  return {
    id: alert.event_id || `${alert.timestamp}-${alert.title}`,
    violationType: getViolationTypeFromAlert(alert),
    timestamp: alert.timestamp,
    severity: alert.severity,
    cameraName: alert.camera_name ?? alert.camera_id ?? "",
    personName: alert.person_name || formatPersonId(alert.person_id),
  };
}

function SnapshotOverlayImage({
  event,
  className,
}: {
  event: ComplianceEvent;
  className?: string;
}) {
  const [imageLoaded, setImageLoaded] = useState(false);
  const imageUrl = resolveApiAssetUrl(event.snapshot_url);

  if (!imageUrl) {
    return (
      <div className={cn("flex h-full items-center justify-center bg-muted/20", className)}>
        <ImageFallback />
      </div>
    );
  }

  return (
    <div className={cn("relative h-full w-full overflow-hidden bg-black", className)}>
      <img
        src={imageUrl}
        alt={event.violation_labels?.join("、") || "违规快照"}
        className="h-full w-full object-cover"
        onLoad={() => setImageLoaded(true)}
      />
      {imageLoaded && event.snapshot_overlay?.boxes?.length ? (
        <svg
          viewBox={`0 0 ${event.snapshot_overlay.image_width} ${event.snapshot_overlay.image_height}`}
          className="pointer-events-none absolute inset-0 h-full w-full"
          preserveAspectRatio="none"
        >
          {event.snapshot_overlay.boxes.map((item, index) => {
            const [x1, y1, x2, y2] = item.box;
            const width = x2 - x1;
            const height = y2 - y1;
            const labelY = Math.max(18, y1 - 8);
            const stroke = item.kind === "person" ? "#38bdf8" : item.kind === "action_violation" ? "#f59e0b" : "#ef4444";
            return (
              <g key={`${event.id}-${item.kind}-${index}`}>
                <rect x={x1} y={y1} width={width} height={height} fill="none" stroke={stroke} strokeWidth={3} />
                <rect x={x1} y={labelY - 18} width={Math.max(72, item.label.length * 16)} height={18} rx={4} fill={stroke} />
                <text x={x1 + 6} y={labelY - 5} fill="#fff" fontSize={12} fontWeight={600}>
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

function ImageFallback() {
  return (
    <div className="flex flex-col items-center justify-center gap-2 text-muted-foreground">
      <CameraOff className="h-8 w-8 opacity-50" />
      <span className="text-xs">暂无画面</span>
    </div>
  );
}

function StreamCameraFrame({
  cameraId,
  cameraName,
  className,
}: {
  cameraId: string;
  cameraName: string;
  className?: string;
}) {
  const [displayedSrc, setDisplayedSrc] = useState<string | null>(null);
  const [pendingSrc, setPendingSrc] = useState<string | null>(null);
  const [hasLoadedFrame, setHasLoadedFrame] = useState(false);
  const cameraFeedSrc = useMemo(
    () => `${api.getLiveFeedUrl(cameraId, { raw: true })}&stream=${cameraId}`,
    [cameraId]
  );

  useEffect(() => {
    if (displayedSrc && displayedSrc.startsWith(api.getLiveFeedUrl(cameraId, { raw: true }))) {
      setPendingSrc(null);
      setHasLoadedFrame(true);
      return;
    }
    setHasLoadedFrame(false);
    setPendingSrc(cameraFeedSrc);
  }, [cameraFeedSrc, cameraId, displayedSrc]);

  return (
    <div className={cn("relative h-full w-full bg-black", className)}>
      {displayedSrc ? (
        <img
          src={displayedSrc}
          alt={`${cameraName} 实时画面`}
          className="h-full w-full object-contain"
        />
      ) : (
        <div className="flex h-full items-center justify-center">
          <ImageFallback />
        </div>
      )}
      {pendingSrc && pendingSrc !== displayedSrc ? (
        <img
          src={pendingSrc}
          alt={`${cameraName} 实时画面`}
          className={cn(
            "absolute inset-0 h-full w-full object-contain transition-opacity duration-150",
            displayedSrc ? "opacity-0" : "opacity-100"
          )}
          onLoad={() => {
            setDisplayedSrc(pendingSrc);
            setPendingSrc(null);
            setHasLoadedFrame(true);
          }}
          onError={() => {
            setPendingSrc(cameraFeedSrc);
          }}
        />
      ) : null}
      {!hasLoadedFrame ? (
        <div className="absolute inset-0 flex items-center justify-center bg-black/45">
          <div className="rounded-md bg-black/70 px-3 py-2 text-sm text-white">画面加载中</div>
        </div>
      ) : null}
    </div>
  );
}

function LivePeopleOverlayLayer({
  frameWidth,
  frameHeight,
  persons,
}: {
  frameWidth: number;
  frameHeight: number;
  persons: LivePersonOverlay[];
}) {
  if (!frameWidth || !frameHeight || persons.length === 0) {
    return null;
  }

  return (
    <svg
      viewBox={`0 0 ${frameWidth} ${frameHeight}`}
      className="pointer-events-none absolute inset-0 h-full w-full"
      preserveAspectRatio="none"
    >
      {persons.map((person, index) => {
        const [x1, y1, x2, y2] = person.box;
        const width = Math.max(0, x2 - x1);
        const height = Math.max(0, y2 - y1);
        const label = person.person_name || formatPersonId(person.person_id);
        const labelWidth = Math.max(84, label.length * 14);
        const labelY = Math.max(20, y1 - 8);
        return (
          <g key={`${person.stable_track_id ?? person.track_id ?? index}-${label}`}>
            <rect x={x1} y={y1} width={width} height={height} fill="none" stroke="#22c55e" strokeWidth={3} />
            <rect x={x1} y={labelY - 18} width={labelWidth} height={18} rx={4} fill="#22c55e" />
            <text x={x1 + 6} y={labelY - 5} fill="#04130a" fontSize={12} fontWeight={700}>
              {label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function FloorSnapshotCard({
  panel,
  selected,
  onSelect,
}: {
  panel: FloorPanel;
  selected: boolean;
  onSelect: (cameraId: string) => void;
}) {
  const isClickable = Boolean(panel.cameraId);
  return (
    <button
      type="button"
      disabled={!isClickable}
      onClick={() => {
        if (panel.cameraId) onSelect(panel.cameraId);
      }}
      className={cn(
        "relative flex aspect-video h-full min-h-0 w-full flex-col overflow-hidden rounded-xl border text-left transition",
        selected ? "border-primary shadow-[0_0_0_1px_var(--color-primary)]" : "border-border/60 bg-card/70",
        isClickable ? "hover:border-primary/70" : "cursor-default"
      )}
    >
      <div className="absolute left-3 top-3 z-10 rounded-md bg-black/70 px-2 py-1 text-xs font-medium text-white">
        {panel.floor}
      </div>
      {panel.imageUrl ? (
        <div className="relative h-full w-full flex-1 overflow-hidden bg-black">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={panel.imageUrl} alt={`${panel.floor}有人画面`} className="h-full w-full object-cover" />
          <LivePeopleOverlayLayer
            frameWidth={panel.frameWidth}
            frameHeight={panel.frameHeight}
            persons={panel.persons}
          />
        </div>
      ) : (
        <div className="flex h-full min-h-0 flex-1 items-center justify-center bg-muted/20">
          <ImageFallback />
        </div>
      )}
      <div className="absolute inset-x-0 bottom-0 z-10 flex items-center justify-between gap-2 bg-gradient-to-t from-black/75 via-black/35 to-transparent px-3 py-3 text-white">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{panel.cameraName || `${panel.floor}暂无人员`}</div>
          <div className="text-[11px] text-white/75">
            {panel.lastFrameAt ? new Date(panel.lastFrameAt).toLocaleTimeString() : "等待检测"}
          </div>
        </div>
        <div className="shrink-0 rounded-full bg-white/15 px-2 py-1 text-[11px]">
          {panel.personCount > 0 ? `${panel.personCount} 人` : "空闲"}
        </div>
      </div>
    </button>
  );
}

function AlertsPanel({ alerts }: { alerts: MonitorAlert[] }) {
  return (
    <div className="h-full overflow-y-auto pr-1">
      <AnimatePresence initial={false}>
        {alerts.length > 0 ? (
          <div className="flex flex-col gap-3">
            {alerts.map((alert) => (
              <motion.article
                key={alert.id}
                layout
                initial={{ opacity: 0, x: 24 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 24 }}
                className="rounded-lg border border-danger/20 bg-gradient-to-br from-danger/8 via-card to-card shadow-sm"
              >
                <div className="flex items-center justify-between border-b border-danger/15 bg-danger/8 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "inline-flex h-2.5 w-2.5 rounded-full",
                        alert.severity === "error"
                          ? "bg-danger"
                          : alert.severity === "warning"
                            ? "bg-warning"
                            : "bg-info"
                      )}
                    />
                    <span className="text-xs font-semibold text-foreground">安全违规告警</span>
                  </div>
                  <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
                    <Clock className="h-3 w-3" />
                    {new Date(alert.timestamp).toLocaleTimeString()}
                  </div>
                </div>

                <div className="space-y-3 p-3">
                  <div className="grid gap-2 text-xs">
                    <div className="grid grid-cols-[64px_minmax(0,1fr)] items-start gap-2">
                      <span className="text-muted-foreground">违规类型</span>
                      <span className="font-medium text-foreground">{alert.violationType}</span>
                    </div>
                    <div className="grid grid-cols-[64px_minmax(0,1fr)] items-start gap-2">
                      <span className="text-muted-foreground">相机名称</span>
                      <span className="font-medium text-foreground">{alert.cameraName}</span>
                    </div>
                    <div className="grid grid-cols-[64px_minmax(0,1fr)] items-start gap-2">
                      <span className="text-muted-foreground">时间</span>
                      <span className="font-medium text-foreground">{new Date(alert.timestamp).toLocaleString()}</span>
                    </div>
                    <div className="grid grid-cols-[64px_minmax(0,1fr)] items-start gap-2">
                      <span className="flex items-center gap-1 text-muted-foreground">
                        <User className="h-3 w-3" />
                        人员名称
                      </span>
                      <span className="font-medium text-foreground">{alert.personName}</span>
                    </div>
                  </div>
                </div>
              </motion.article>
            ))}
          </div>
        ) : (
          <motion.div
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            className="flex h-full min-h-[260px] flex-col items-center justify-center rounded-lg border border-dashed border-border/70 bg-muted/20 px-6 text-center"
          >
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-success/10 text-success">
              <Shield className="h-7 w-7" />
            </div>
            <h3 className="mt-4 text-base font-semibold text-foreground">最近一小时暂无告警</h3>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function buildCameraLabel(camera: CameraModel) {
  return camera.name || `${camera.floor || ""}${camera.name_suffix || ""}` || camera.id;
}

export default function MonitorPage() {
  const [alerts, setAlerts] = useState<MonitorAlert[]>([]);
  const [selectedFloor, setSelectedFloor] = useState<string>("全部");
  const [selectedCameraId, setSelectedCameraId] = useState<string | null>(null);
  const [stableFloorPanels, setStableFloorPanels] = useState<Record<string, FloorPanel>>({});
  const shownToastEventIdsRef = useRef<Set<string>>(new Set());

  const { data: cameras = [] } = useCameras();
  const { data: recentViolations = [] } = useRecentViolations(20);
  const { data: floorActivity } = useFloorActivitySnapshots([...FLOOR_OPTIONS], true);
  const { data: latestSnapshotEvents = [], isLoading: snapshotsLoading } = useLatestViolationSnapshots(SNAPSHOT_LIMIT);
  const { lastMessage, isConnected } = useWebSocket();

  const activeCameras = useMemo(() => cameras.filter((camera) => camera.enabled), [cameras]);
  const filteredCameras = useMemo(
    () =>
      selectedFloor === "全部"
        ? activeCameras
        : activeCameras.filter((camera) => camera.floor === selectedFloor),
    [activeCameras, selectedFloor]
  );

  useEffect(() => {
    if (!selectedCameraId && filteredCameras.length > 0) {
      setSelectedCameraId(filteredCameras[0].id);
      return;
    }
    if (selectedCameraId && !filteredCameras.some((camera) => camera.id === selectedCameraId)) {
      setSelectedCameraId(filteredCameras[0]?.id ?? null);
    }
  }, [filteredCameras, selectedCameraId]);

  const selectedCamera = useMemo(
    () => activeCameras.find((camera) => camera.id === selectedCameraId) ?? null,
    [activeCameras, selectedCameraId]
  );
  const { data: selectedCameraPeople } = useLivePeople(selectedCameraId ?? "", !!selectedCameraId, MAIN_PEOPLE_REFRESH_MS);

  const floorActivityItems = useMemo(() => floorActivity?.items ?? [], [floorActivity]);

  const floorSnapshotCandidates = useMemo(() => {
    const map = new Map(floorActivityItems.map((item) => [item.floor, item]));
    return FLOOR_OPTIONS.map((floor) => {
      const item = map.get(floor);
      const lastFrameAt = item?.last_frame_at ?? null;
      const frameToken = lastFrameAt ?? "latest";
      return {
        floor,
        cameraId: item?.camera_id ?? null,
        cameraName: item?.camera_name ?? null,
        personCount: item?.persons?.length ?? item?.person_count ?? 0,
        imageUrl: item?.frame_url ? `${resolveApiAssetUrl(item.frame_url) ?? item.frame_url}&t=${encodeURIComponent(frameToken)}` : null,
        lastFrameAt,
        frameWidth: item?.frame_width ?? 0,
        frameHeight: item?.frame_height ?? 0,
        persons: item?.persons ?? [],
      } satisfies FloorPanel;
    });
  }, [floorActivityItems]);

  useEffect(() => {
    setStableFloorPanels((current) => {
      const next = { ...current };
      let changed = false;

      for (const panel of floorSnapshotCandidates) {
        if (!panel.cameraId || !panel.imageUrl || panel.personCount <= 0) {
          continue;
        }

        const existing = current[panel.floor];
        const hasSameSource =
          existing &&
          existing.cameraId === panel.cameraId &&
          existing.lastFrameAt === panel.lastFrameAt &&
          existing.personCount === panel.personCount &&
          existing.frameWidth === panel.frameWidth &&
          existing.frameHeight === panel.frameHeight &&
          existing.persons.length === panel.persons.length &&
          existing.persons.every((person, index) => {
            const candidate = panel.persons[index];
            return (
              candidate &&
              person.person_id === candidate.person_id &&
              person.person_name === candidate.person_name &&
              person.box.join(",") === candidate.box.join(",")
            );
          });

        if (hasSameSource) {
          continue;
        }

        next[panel.floor] = panel;
        changed = true;
      }

      return changed ? next : current;
    });
  }, [floorSnapshotCandidates]);

  const floorPersonCountMap = useMemo(() => {
    const result = new Map<string, number>();
    floorSnapshotCandidates.forEach((panel) => {
      const stableCount = stableFloorPanels[panel.floor]?.personCount ?? 0;
      result.set(panel.floor, Math.max(panel.personCount, stableCount));
    });
    return result;
  }, [floorSnapshotCandidates, stableFloorPanels]);

  useEffect(() => {
    if (recentViolations.length === 0) {
      return;
    }
    setAlerts((current) => mergeAlerts([...current, ...recentViolations.map(mapRecentViolationToAlert)]));
  }, [recentViolations]);

  useEffect(() => {
    if (!lastMessage || (lastMessage.type !== "violation" && lastMessage.type !== "violation_update")) {
      return;
    }

    const nextAlert = mapLiveAlertToMonitorAlert(lastMessage);
    setAlerts((current) => mergeAlerts([...current, nextAlert]));

    if (lastMessage.type !== "violation") {
      return;
    }

    const toastId = lastMessage.event_id || `${lastMessage.timestamp}-${lastMessage.title}`;
    if (shownToastEventIdsRef.current.has(toastId)) {
      return;
    }
    shownToastEventIdsRef.current.add(toastId);

    toast.error("安全违规告警", {
      id: toastId,
      description: (
        <div className="flex min-w-[220px] flex-col gap-1 text-xs normal-case">
          <p>违规类型：{nextAlert.violationType}</p>
          <p>相机名称：{nextAlert.cameraName}</p>
          <p>人员名称：{nextAlert.personName}</p>
          <p>时间：{new Date(nextAlert.timestamp).toLocaleTimeString()}</p>
        </div>
      ),
      icon: <AlertTriangle className="h-4 w-4" />,
      duration: 5000,
    });
  }, [lastMessage]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      setAlerts((current) => mergeAlerts(current, Date.now()));
    }, 30000);
    return () => window.clearInterval(interval);
  }, []);

  const latestSnapshots = useMemo(
    () => latestSnapshotEvents.filter((event) => !!event.snapshot_url).slice(0, SNAPSHOT_LIMIT),
    [latestSnapshotEvents]
  );

  return (
    <div className="flex min-h-[calc(100dvh-5.5rem)] flex-col gap-3 xl:gap-4 lg:min-h-[calc(100dvh-2rem)]">
      <div className="grid shrink-0 items-center gap-3 xl:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] xl:gap-4">
        <div className="flex flex-wrap items-center gap-3 xl:justify-self-start">
          <div className="flex items-center gap-2 rounded-full border border-border/60 bg-card px-4 py-2 shadow-sm">
            <Radio className={cn("h-4 w-4", isConnected ? "text-success" : "text-muted-foreground")} />
            <span className="text-sm font-medium">{isConnected ? "实时监控中" : "信号重连中"}</span>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-border/60 bg-card px-4 py-2 shadow-sm">
            <Camera className="h-4 w-4 text-primary" />
            <span className="text-sm text-muted-foreground">在线摄像头</span>
            <span className="text-sm font-semibold text-foreground">{activeCameras.length}</span>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-danger/20 bg-danger/5 px-4 py-2 shadow-sm">
            <AlertTriangle className="h-4 w-4 text-danger" />
            <span className="text-sm text-muted-foreground">告警消息</span>
            <span className="text-sm font-semibold text-foreground">{alerts.length}</span>
          </div>
        </div>

        <div className="text-center xl:justify-self-center">
          <h1 className="mt-2 text-3xl font-semibold text-foreground">实时监控大屏</h1>
        </div>

        <div className="flex items-center justify-start gap-2 xl:justify-self-end">
          <div className="hidden items-center gap-2 rounded-full border border-border/60 bg-card px-4 py-2 text-sm text-muted-foreground shadow-sm lg:flex">
            <Users className="h-4 w-4 text-primary" />
            楼层侧图保留最新一张有人检测画面
          </div>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 content-start gap-3 xl:grid-cols-[minmax(0,4fr)_minmax(320px,1fr)] xl:gap-4">
        <section className="min-h-0 overflow-hidden">
          <div className="grid min-h-0 content-start gap-3 xl:gap-4">
            <Card className="overflow-hidden border-border/60 bg-card/95 py-0 shadow-sm">
              <CardContent className="grid gap-3 p-3 lg:p-4">
                <div className="flex flex-wrap items-center gap-3">
                  <Select value={selectedFloor} onValueChange={setSelectedFloor}>
                    <SelectTrigger className="h-10 w-[140px]">
                      <SelectValue placeholder="选择楼层" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="全部">全部楼层</SelectItem>
                      {FLOOR_OPTIONS.map((floor) => (
                        <SelectItem key={floor} value={floor}>
                          {floor}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  <Select
                    value={selectedCameraId ?? undefined}
                    onValueChange={setSelectedCameraId}
                    disabled={filteredCameras.length === 0}
                  >
                    <SelectTrigger className="h-10 min-w-[220px] flex-1 xl:max-w-[360px]">
                      <SelectValue placeholder="选择监控" />
                    </SelectTrigger>
                    <SelectContent>
                      {filteredCameras.map((camera) => (
                        <SelectItem key={camera.id} value={camera.id}>
                          {buildCameraLabel(camera)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
                  <div className="flex flex-col">
                    <div className="relative aspect-video w-full overflow-hidden rounded-xl border border-border/60 bg-black">
                      {selectedCamera ? (
                        <>
                          <StreamCameraFrame
                            cameraId={selectedCamera.id}
                            cameraName={selectedCamera.name}
                            className="h-full w-full"
                          />
                          <LivePeopleOverlayLayer
                            frameWidth={(selectedCameraPeople as LivePersonOverlayResponse | undefined)?.frame_width ?? 0}
                            frameHeight={(selectedCameraPeople as LivePersonOverlayResponse | undefined)?.frame_height ?? 0}
                            persons={(selectedCameraPeople as LivePersonOverlayResponse | undefined)?.persons ?? []}
                          />
                          <div className="absolute left-3 top-3 flex max-w-[75%] items-center gap-2 rounded-md bg-black/72 px-3 py-2 text-white backdrop-blur-sm">
                            <span className="inline-flex h-2 w-2 rounded-full bg-success" />
                            <span className="truncate text-sm font-medium">{selectedCamera.name}</span>
                          </div>
                          <div className="absolute bottom-3 left-3 flex items-center gap-2 rounded-md bg-primary/85 px-3 py-2 text-xs font-medium text-white">
                            <Video className="h-3.5 w-3.5" />
                            实时视频流
                          </div>
                        </>
                      ) : (
                        <div className="flex h-full items-center justify-center">
                          <div className="flex flex-col items-center gap-3 text-center text-muted-foreground">
                            <CameraOff className="h-10 w-10 opacity-50" />
                            <div>
                              <div className="text-base font-medium text-foreground">暂无可用监控</div>
                              <div className="text-sm">请先选择楼层或启用摄像头</div>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    {FLOOR_OPTIONS.map((floor) => {
                      const panel = stableFloorPanels[floor] ?? {
                        floor,
                        cameraId: null,
                        cameraName: null,
                        personCount: 0,
                        imageUrl: null,
                        lastFrameAt: null,
                        frameWidth: 0,
                        frameHeight: 0,
                        persons: [],
                      };
                      return (
                        <FloorSnapshotCard
                          key={floor}
                          panel={{
                            ...panel,
                            personCount: floorPersonCountMap.get(floor) ?? panel.personCount,
                          }}
                          selected={panel.cameraId === selectedCameraId}
                          onSelect={setSelectedCameraId}
                        />
                      );
                    })}
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card className="min-h-0 overflow-hidden border-border/60 bg-card/95 py-0 shadow-sm">
              <CardContent className="flex h-full min-h-0 flex-col p-3 lg:p-4">
                {snapshotsLoading ? (
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
                    {Array.from({ length: SNAPSHOT_LIMIT }).map((_, index) => (
                      <Skeleton key={index} className="aspect-video w-full rounded-xl" />
                    ))}
                  </div>
                ) : latestSnapshots.length > 0 ? (
                  <div className="grid h-full min-h-0 grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
                    {latestSnapshots.map((event) => (
                      <div
                        key={event.id}
                        className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-border/60 bg-card shadow-sm"
                      >
                        <div className="aspect-video overflow-hidden bg-black">
                          <SnapshotOverlayImage event={event} />
                        </div>
                        <div className="border-t border-border/60 bg-gradient-to-b from-background/10 via-card to-muted/20 px-3 py-2.5">
                          <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                              <div className="line-clamp-1 text-sm font-semibold text-foreground">
                                {event.violation_labels?.join("、") || "危险行为"}
                              </div>
                              <div className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
                                <span className="line-clamp-1">
                                  {event.camera_name || event.camera_id || "未知点位"}
                                </span>
                                <span className="h-1 w-1 rounded-full bg-border" />
                                <span className="shrink-0">{new Date(event.timestamp).toLocaleTimeString()}</span>
                              </div>
                            </div>
                            <span className="shrink-0 rounded-full border border-danger/20 bg-danger/8 px-2 py-1 text-[10px] font-medium text-danger">
                              告警
                            </span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-border/70 bg-muted/20 text-sm text-muted-foreground">
                    暂无违规快照
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </section>

        <aside className="min-h-0 overflow-hidden">
          <Card className="flex h-full min-h-0 flex-col overflow-hidden border-border/60 bg-card/95 py-0 shadow-sm">
            <CardContent className="min-h-0 flex-1 p-4">
              <AlertsPanel alerts={alerts} />
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
