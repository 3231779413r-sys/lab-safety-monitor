"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  Camera,
  Eye,
  Layers3,
  Network,
  Plus,
  Power,
  Settings2,
  TestTube2,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { PageLoader } from "@/components/page-loader";
import { PageHeader } from "@/components/page-header";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import api, { Camera as CameraModel, CameraEventOption, CameraEventOptionsResponse } from "@/lib/api";
import {
  useCameraEventOptions,
  useCameras,
  useCreateCamera,
  useDeleteCamera,
  useDisableCamera,
  useEnableCamera,
  useTestCamera,
  useUpdateCamera,
} from "@/lib/queries";

type FloorOption = "一楼" | "二楼" | "三楼" | "四楼" | "室外";

type CameraFormState = {
  floor: FloorOption;
  name_suffix: string;
  host: string;
  port: number;
  username: string;
  password: string;
  channel: number;
  stream_type: string;
  enabled: boolean;
  vendor: string;
  camera_detection_scope: string[];
  backend_detection_scope: string[];
  area_overcapacity_polygon: number[][];
  area_overcapacity_limit: number | null;
};

const FLOOR_OPTIONS: FloorOption[] = ["一楼", "二楼", "三楼", "四楼", "室外"];
const MAX_POLYGON_POINTS = 4;
const AREA_OVERLAY_SCALE = 1000;

const EMPTY_FORM: CameraFormState = {
  floor: "一楼",
  name_suffix: "",
  host: "",
  port: 8000,
  username: "",
  password: "",
  channel: 1,
  stream_type: "main",
  enabled: true,
  vendor: "hikvision",
  camera_detection_scope: [],
  backend_detection_scope: [],
  area_overcapacity_polygon: [],
  area_overcapacity_limit: null,
};

const FALLBACK_CAMERA_EVENT_OPTIONS: CameraEventOptionsResponse = {
  camera_detection: [
    { key: "hardhat", label: "未佩戴安全帽" },
    { key: "mask", label: "未佩戴口罩" },
    { key: "safety_vest", label: "未穿戴安全背心" },
    { key: "safety_shoes", label: "未穿戴防护鞋" },
    { key: "gloves", label: "未佩戴防护手套" },
    { key: "goggles", label: "未佩戴护目镜" },
    { key: "respirator", label: "未佩戴防毒口罩" },
    { key: "unauthorized_intrusion", label: "违规闯入" },
    { key: "area_overcapacity", label: "区域超员" },
  ],
  backend_detection: [
    { key: "missed_inspection", label: "未巡检" },
    { key: "unauthorized_intrusion", label: "违规闯入" },
    { key: "overtime_stay", label: "超时驻留" },
    { key: "blind_spot_stay", label: "盲区驻留" },
    { key: "workshop_overcapacity", label: "车间超员" },
    { key: "fall_detected", label: "人员跌倒" },
  ],
};

function normalizeCreatePayload(form: CameraFormState) {
  return {
    floor: form.floor,
    name_suffix: form.name_suffix.trim(),
    host: form.host.trim(),
    port: form.port,
    username: form.username.trim(),
    password: form.password,
    channel: form.channel,
    stream_type: form.stream_type,
    enabled: form.enabled,
    vendor: form.vendor,
    camera_detection_scope: form.camera_detection_scope,
    backend_detection_scope: form.backend_detection_scope,
    area_overcapacity_polygon: form.area_overcapacity_polygon,
    area_overcapacity_limit: form.area_overcapacity_limit,
  };
}

function normalizeUpdatePayload(form: CameraFormState) {
  return {
    floor: form.floor,
    name_suffix: form.name_suffix.trim(),
    host: form.host.trim(),
    port: form.port,
    username: form.username.trim(),
    password: form.password || undefined,
    channel: form.channel,
    stream_type: form.stream_type,
    vendor: form.vendor,
    camera_detection_scope: form.camera_detection_scope,
    backend_detection_scope: form.backend_detection_scope,
    area_overcapacity_polygon: form.area_overcapacity_polygon,
    area_overcapacity_limit: form.area_overcapacity_limit,
  };
}

function clampCoordinate(value: number) {
  return Number(Math.min(1, Math.max(0, value)).toFixed(4));
}

function toOverlayPoints(points: number[][]) {
  return points
    .map((point) => `${Math.round(point[0] * AREA_OVERLAY_SCALE)},${Math.round(point[1] * AREA_OVERLAY_SCALE)}`)
    .join(" ");
}

function floorSortOrder(value?: string | null) {
  switch ((value || "").trim()) {
    case "一楼":
      return 1;
    case "二楼":
      return 2;
    case "三楼":
      return 3;
    case "四楼":
      return 4;
    case "室外":
    case "户外":
      return 5;
    default:
      return 99;
  }
}

function ipSortValue(value?: string | null) {
  if (!value) {
    return Number.MAX_SAFE_INTEGER;
  }
  const parts = value.trim().split(".").map((item) => Number(item));
  if (parts.length !== 4 || parts.some((item) => Number.isNaN(item) || item < 0 || item > 255)) {
    return Number.MAX_SAFE_INTEGER;
  }
  return (((parts[0] * 256 + parts[1]) * 256 + parts[2]) * 256 + parts[3]);
}

export default function CamerasPage() {
  const { data: cameras = [], isLoading } = useCameras();
  const { data: eventOptionsData } = useCameraEventOptions();

  const createCamera = useCreateCamera();
  const updateCamera = useUpdateCamera();
  const deleteCamera = useDeleteCamera();
  const testCamera = useTestCamera();
  const enableCamera = useEnableCamera();
  const disableCamera = useDisableCamera();

  const [floorFilter, setFloorFilter] = useState<FloorOption | "全部">("全部");
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [isEditOpen, setIsEditOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<CameraModel | null>(null);
  const [showAreaConfig, setShowAreaConfig] = useState(false);
  const [draftPolygon, setDraftPolygon] = useState<number[][]>([]);
  const [draggingPointIndex, setDraggingPointIndex] = useState<number | null>(null);
  const [form, setForm] = useState<CameraFormState>(EMPTY_FORM);
  const [editing, setEditing] = useState<CameraModel | null>(null);
  const [areaCamera, setAreaCamera] = useState<CameraModel | null>(null);

  const videoAreaRef = useRef<HTMLDivElement | null>(null);
  const dragMovedRef = useRef(false);

  const eventOptions = eventOptionsData ?? FALLBACK_CAMERA_EVENT_OPTIONS;

  const filteredCameras = useMemo(() => {
    const matched = floorFilter === "全部"
      ? cameras
      : cameras.filter((camera) => camera.floor === floorFilter);
    return [...matched].sort((left, right) => {
      const floorDiff = floorSortOrder(left.floor) - floorSortOrder(right.floor);
      if (floorDiff !== 0) {
        return floorDiff;
      }
      const ipDiff = ipSortValue(left.host) - ipSortValue(right.host);
      if (ipDiff !== 0) {
        return ipDiff;
      }
      return (left.host || "").localeCompare(right.host || "", "zh-CN");
    });
  }, [cameras, floorFilter]);

  useEffect(() => {
    if (draggingPointIndex === null) {
      return;
    }

    const handleMouseMove = (event: MouseEvent) => {
      const container = videoAreaRef.current;
      if (!container) {
        return;
      }
      const rect = container.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) {
        return;
      }
      dragMovedRef.current = true;
      const x = clampCoordinate((event.clientX - rect.left) / rect.width);
      const y = clampCoordinate((event.clientY - rect.top) / rect.height);
      setDraftPolygon((current) =>
        current.map((point, index) => (index === draggingPointIndex ? [x, y] : point)),
      );
    };

    const handleMouseUp = () => {
      setDraggingPointIndex(null);
      window.setTimeout(() => {
        dragMovedRef.current = false;
      }, 0);
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [draggingPointIndex]);

  const openCreateDialog = () => {
    setForm(EMPTY_FORM);
    setDraftPolygon([]);
    setEditing(null);
    setIsCreateOpen(true);
  };

  const openEditDialog = (camera: CameraModel) => {
    setEditing(camera);
    setForm({
      floor: (camera.floor as FloorOption) || "一楼",
      name_suffix: camera.name_suffix || "",
      host: camera.host || "",
      port: camera.port || 8000,
      username: camera.username || "",
      password: camera.password || "",
      channel: camera.channel || 1,
      stream_type: camera.stream_type || "main",
      enabled: camera.enabled,
      vendor: camera.vendor || "hikvision",
      camera_detection_scope: camera.camera_detection_scope || [],
      backend_detection_scope: camera.backend_detection_scope || [],
      area_overcapacity_polygon: camera.area_overcapacity_polygon || [],
      area_overcapacity_limit: camera.area_overcapacity_limit || null,
    });
    setDraftPolygon(camera.area_overcapacity_polygon || []);
    setIsEditOpen(true);
  };

  const handleSubmitCreate = async () => {
    if (!form.name_suffix.trim() || !form.host.trim() || !form.username.trim() || !form.password.trim()) {
      toast.error("请补全摄像头基本信息");
      return;
    }
    if (form.camera_detection_scope.includes("area_overcapacity")) {
      if (
        form.area_overcapacity_polygon.length !== 4
        || form.area_overcapacity_limit === null
        || form.area_overcapacity_limit < 0
      ) {
        toast.info("摄像头创建后，请进入编辑完成区域超员四边形区域配置");
      }
    }
    try {
      await createCamera.mutateAsync(normalizeCreatePayload(form));
      toast.success("摄像头已新增");
      setIsCreateOpen(false);
      setForm(EMPTY_FORM);
      setDraftPolygon([]);
    } catch (error) {
      toast.error(String(error));
    }
  };

  const handleSubmitEdit = async () => {
    if (!editing) {
      return;
    }
    if (!form.name_suffix.trim() || !form.host.trim() || !form.username.trim()) {
      toast.error("请补全摄像头基本信息");
      return;
    }
    if (form.camera_detection_scope.includes("area_overcapacity")) {
      if (form.area_overcapacity_polygon.length !== 4 || (!form.area_overcapacity_limit && form.area_overcapacity_limit != 0)) {
        toast.error("请完成区域超员四边形区域划定和人数设置");
        return;
      }
    }
    try {
      await updateCamera.mutateAsync({
        cameraId: editing.id,
        data: normalizeUpdatePayload(form),
      });
      toast.success("摄像头已更新");
      setIsEditOpen(false);
      setEditing(null);
    } catch (error) {
      toast.error(String(error));
    }
  };

  const toggleEvent = (field: "camera_detection_scope", value: string) => {
    setForm((current) => {
      const exists = current[field].includes(value);
      const next = exists
        ? current[field].filter((item) => item !== value)
        : [...current[field], value];
      return {
        ...current,
        [field]: next,
        area_overcapacity_polygon:
          field === "camera_detection_scope" && value === "area_overcapacity" && exists
            ? []
            : current.area_overcapacity_polygon,
        area_overcapacity_limit:
          field === "camera_detection_scope" && value === "area_overcapacity" && exists
            ? null
            : current.area_overcapacity_limit,
      };
    });
  };

  const handleOpenAreaConfig = () => {
    if (!editing) {
      toast.info("请先保存摄像头，再进入编辑配置区域超员");
      return;
    }
    setAreaCamera(editing);
    setDraftPolygon(form.area_overcapacity_polygon);
    setShowAreaConfig(true);
  };

  const handleAreaVideoClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (draggingPointIndex !== null || dragMovedRef.current) {
      return;
    }
    const container = videoAreaRef.current;
    if (!container) {
      return;
    }
    if (draftPolygon.length >= MAX_POLYGON_POINTS) {
      toast.info("区域超员仅支持四边形，请清空后重新选择");
      return;
    }
    const rect = container.getBoundingClientRect();
    const x = clampCoordinate((event.clientX - rect.left) / rect.width);
    const y = clampCoordinate((event.clientY - rect.top) / rect.height);
    setDraftPolygon((current) => [...current, [x, y]]);
  };

  const handleAreaPointMouseDown = (index: number, event: React.MouseEvent<SVGCircleElement>) => {
    event.preventDefault();
    event.stopPropagation();
    dragMovedRef.current = false;
    setDraggingPointIndex(index);
  };

  const saveAreaConfig = () => {
    if (draftPolygon.length !== MAX_POLYGON_POINTS) {
      toast.error("请按顺序点击四个点，形成四边形区域");
      return;
    }
    setForm((current) => ({ ...current, area_overcapacity_polygon: draftPolygon }));
    setShowAreaConfig(false);
  };

  if (isLoading) {
    return <PageLoader />;
  }

  return (
    <div className="space-y-6">
      <Card variant="glass">
        <CardContent className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant={floorFilter === "全部" ? "default" : "outline"}
              onClick={() => setFloorFilter("全部")}
            >
              全部
            </Button>
            {FLOOR_OPTIONS.map((floor) => (
              <Button
                key={floor}
                type="button"
                variant={floorFilter === floor ? "default" : "outline"}
                onClick={() => setFloorFilter(floor)}
              >
                {floor}
              </Button>
            ))}
          </div>
          <Button type="button" onClick={openCreateDialog}>
            <Plus className="h-4 w-4" />
            新增
          </Button>
        </CardContent>
      </Card>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {filteredCameras.map((camera) => (
          <motion.div key={camera.id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
            <Card variant="glass" className="h-full">
              <CardContent className="space-y-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <Camera className="h-4 w-4 text-primary" />
                      <span className="font-semibold text-foreground">{camera.name}</span>
                    </div>
                    <div className="text-sm text-muted-foreground">{camera.host}:{camera.port}</div>
                  </div>
                  <Badge variant={camera.enabled ? "success-soft" : "warning-soft"}>
                    {camera.enabled ? "已启用" : "已禁用"}
                  </Badge>
                </div>

                <div className="grid grid-cols-3 text-sm text-muted-foreground">
                  <div className="flex items-center gap-2">
                    <Layers3 className="h-4 w-4" />
                    <span>{camera.floor || "-"} / {camera.name_suffix || "-"}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Network className="h-4 w-4" />
                    <span>通道 {camera.channel} / {camera.stream_type === "main" ? "主码流" : "子码流"}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Eye className="h-4 w-4" />
                    <span>{camera.video_resolution || "-"} / {camera.video_encoding || "-"}</span>
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center gap-3">
                    <div className="h-px flex-1 bg-border/60" />
                    <div className="text-sm font-medium text-foreground">监管事件</div>
                    <div className="h-px flex-1 bg-border/60" />
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {(camera.camera_detection_scope_labels || []).map((label) => (
                      <span
                        key={`${camera.id}-${label}`}
                        className="rounded-md border border-red-500/60 bg-red-500/10 px-2 py-1 text-xs text-red-600"
                      >
                        {label}
                      </span>
                    ))}
                    {!camera.camera_detection_scope_labels?.length ? (
                      <span className="text-sm text-muted-foreground">未设置</span>
                    ) : null}
                  </div>
                </div>

                <div className="flex flex-wrap justify-center gap-2">
                  <Button type="button" size="sm" variant="outline" onClick={() => openEditDialog(camera)}>
                    <Settings2 className="h-4 w-4" />
                    编辑
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={async () => {
                      const result = await testCamera.mutateAsync(camera.id);
                      toast[result.success ? "success" : "error"](result.message);
                    }}
                  >
                    <TestTube2 className="h-4 w-4" />
                    测试
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={async () => {
                      if (camera.enabled) {
                        await disableCamera.mutateAsync(camera.id);
                        toast.success("摄像头已禁用");
                      } else {
                        await enableCamera.mutateAsync(camera.id);
                        toast.success("摄像头已启用");
                      }
                    }}
                  >
                    <Power className="h-4 w-4" />
                    {camera.enabled ? "禁用" : "启用"}
                  </Button>
                  <Button type="button" size="sm" variant="destructive" onClick={() => setDeleteTarget(camera)}>
                    <Trash2 className="h-4 w-4" />
                    删除
                  </Button>
                </div>
              </CardContent>
            </Card>
          </motion.div>
        ))}
      </section>

      <CameraFormDialog
        open={isCreateOpen}
        onOpenChange={setIsCreateOpen}
        title="新增监控"
        description="添加摄像头、设置楼层、监管事件与启用状态"
        form={form}
        setForm={setForm}
        eventOptions={eventOptions}
        onToggleEvent={toggleEvent}
        onSubmit={handleSubmitCreate}
        submitLabel="创建"
        includeEnabledToggle
        onOpenAreaConfig={handleOpenAreaConfig}
      />

      <CameraFormDialog
        open={isEditOpen}
        onOpenChange={setIsEditOpen}
        title="编辑监控"
        description="修改基础信息和监管事件"
        form={form}
        setForm={setForm}
        eventOptions={eventOptions}
        onToggleEvent={toggleEvent}
        onSubmit={handleSubmitEdit}
        submitLabel="保存"
        includeEnabledToggle={false}
        onOpenAreaConfig={handleOpenAreaConfig}
      />

      <Dialog open={showAreaConfig} onOpenChange={setShowAreaConfig}>
        <DialogContent className="max-w-4xl min-h-[70vh]">
          <DialogHeader>
            <DialogTitle>区域超员配置</DialogTitle>
            <DialogDescription>在实时视频上按顺序点击四个点，划定四边形区域，并设置超员人数</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
            <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
              <div className="mb-3 flex items-center gap-2 text-sm text-muted-foreground">
                <Eye className="h-4 w-4" />
                <span>点击视频画面选取四个角点，选完后可拖动红点微调区域</span>
              </div>
              <div
                ref={videoAreaRef}
                onClick={handleAreaVideoClick}
                className="relative aspect-video w-full overflow-hidden rounded-lg border border-border bg-slate-900"
              >
                {areaCamera ? (
                  <img
                    src={api.getCameraPreviewFeedUrl(areaCamera.id)}
                    alt={areaCamera.name}
                    className="h-full w-full object-cover"
                  />
                ) : null}
                <svg
                  className="absolute inset-0 h-full w-full"
                  viewBox={`0 0 ${AREA_OVERLAY_SCALE} ${AREA_OVERLAY_SCALE}`}
                  preserveAspectRatio="none"
                >
                  {draftPolygon.length > 0 ? (
                    <>
                      {draftPolygon.length >= 2 ? (
                        draftPolygon.length === MAX_POLYGON_POINTS ? (
                          <polygon
                            points={toOverlayPoints(draftPolygon)}
                            fill="rgba(239,68,68,0.16)"
                            stroke="rgba(239,68,68,0.9)"
                            strokeWidth="2"
                            className="pointer-events-none"
                          />
                        ) : (
                          <polyline
                            points={toOverlayPoints(draftPolygon)}
                            fill="none"
                            stroke="rgba(239,68,68,0.9)"
                            strokeWidth="2"
                            className="pointer-events-none"
                          />
                        )
                      ) : null}
                      {draftPolygon.map((point, index) => (
                        <circle
                          key={`${index}-${point[0]}-${point[1]}`}
                          cx={Math.round(point[0] * AREA_OVERLAY_SCALE)}
                          cy={Math.round(point[1] * AREA_OVERLAY_SCALE)}
                          r="6"
                          fill="#ef4444"
                          stroke="#ffffff"
                          strokeWidth="1.5"
                          className="cursor-move pointer-events-auto"
                          onClick={(event) => event.stopPropagation()}
                          onMouseDown={(event) => handleAreaPointMouseDown(index, event)}
                        />
                      ))}
                    </>
                  ) : null}
                </svg>
              </div>
            </div>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>超员人数</Label>
                <Input
                  type="number"
                  min={0}
                  value={form.area_overcapacity_limit ?? ""}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      area_overcapacity_limit: event.target.value ? Number(event.target.value) : null,
                    }))
                  }
                />
              </div>
              <div className="space-y-2">
                <div className="text-sm font-medium text-foreground">已选点位</div>
                <div className="rounded-lg border border-border/60 p-3 text-xs text-muted-foreground">
                  {draftPolygon.length > 0 ? (
                    draftPolygon.map((point, index) => (
                      <div key={`${point[0]}-${point[1]}`}>{index + 1}. ({point[0]}, {point[1]})</div>
                    ))
                  ) : (
                    <span>尚未选点</span>
                  )}
                </div>
              </div>
              <div className="flex gap-2">
                <Button type="button" variant="outline" onClick={() => setDraftPolygon([])}>
                  清空
                </Button>
                <Button type="button" onClick={saveAreaConfig}>
                  应用
                </Button>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除监控？</AlertDialogTitle>
            <AlertDialogDescription>
              确定删除 {deleteTarget?.name} 吗？此操作无法撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                if (!deleteTarget) return;
                await deleteCamera.mutateAsync(deleteTarget.id);
                toast.success("监控已删除");
                setDeleteTarget(null);
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function CameraFormDialog({
  open,
  onOpenChange,
  title,
  description,
  form,
  setForm,
  eventOptions,
  onToggleEvent,
  onSubmit,
  submitLabel,
  includeEnabledToggle,
  onOpenAreaConfig,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  form: CameraFormState;
  setForm: React.Dispatch<React.SetStateAction<CameraFormState>>;
  eventOptions: CameraEventOptionsResponse;
  onToggleEvent: (field: "camera_detection_scope", value: string) => void;
  onSubmit: () => Promise<void>;
  submitLabel: string;
  includeEnabledToggle: boolean;
  onOpenAreaConfig: () => void;
}) {
  const hasAreaOvercapacity = form.camera_detection_scope.includes("area_overcapacity");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-6">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>楼层</Label>
              <select
                value={form.floor}
                onChange={(event) =>
                  setForm((current) => ({ ...current, floor: event.target.value as FloorOption }))
                }
                className="h-10 w-full rounded-lg border border-input bg-background px-3 py-2 text-sm"
              >
                {FLOOR_OPTIONS.map((floor) => (
                  <option key={floor} value={floor}>{floor}</option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <Label>名称</Label>
              <Input
                value={form.name_suffix}
                onChange={(event) => setForm((current) => ({ ...current, name_suffix: event.target.value }))}
                placeholder="请输入命名"
              />
            </div>
            <div className="space-y-2">
              <Label>IP</Label>
              <Input
                value={form.host}
                onChange={(event) => setForm((current) => ({ ...current, host: event.target.value }))}
                placeholder="请输入摄像头 IP"
              />
            </div>
            <div className="space-y-2">
              <Label>端口</Label>
              <Input
                type="number"
                value={form.port}
                onChange={(event) => setForm((current) => ({ ...current, port: Number(event.target.value) }))}
              />
            </div>
            <div className="space-y-2">
              <Label>用户名</Label>
              <Input
                value={form.username}
                onChange={(event) => setForm((current) => ({ ...current, username: event.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>密码</Label>
              <Input
                type="password"
                value={form.password}
                onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))}
                placeholder={submitLabel === "保存修改" ? "留空则保持原密码" : "请输入摄像头密码"}
              />
            </div>
            <div className="space-y-2">
              <Label>通道号</Label>
              <Input
                type="number"
                value={form.channel}
                onChange={(event) => setForm((current) => ({ ...current, channel: Number(event.target.value) }))}
              />
            </div>
            <div className="space-y-2">
              <Label>码流类型</Label>
              <select
                value={form.stream_type}
                onChange={(event) => setForm((current) => ({ ...current, stream_type: event.target.value }))}
                className="h-10 w-full rounded-lg border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="main">主码流</option>
                <option value="sub">子码流</option>
              </select>
            </div>
          </div>

          {includeEnabledToggle ? (
            <label className="flex items-center gap-3 rounded-lg border border-border/60 px-4 py-3 text-sm">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) => setForm((current) => ({ ...current, enabled: event.target.checked }))}
              />
              <span>启用该监控</span>
            </label>
          ) : null}

          <EventScopeSection
            title="检测事件"
            icon={<Camera className="h-4 w-4 text-primary" />}
            options={eventOptions.camera_detection}
            selected={form.camera_detection_scope}
            onToggle={(value) => onToggleEvent("camera_detection_scope", value)}
          />

          {hasAreaOvercapacity ? (
            <div className="rounded-lg border border-border/60 p-4">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="space-y-1">
                  <div className="text-sm font-medium text-foreground">区域超员配置</div>
                  <div className="text-sm text-muted-foreground">
                    {form.area_overcapacity_polygon.length === 4
                      ? `已配置 ${form.area_overcapacity_polygon.length} 个点，人数上限 ${form.area_overcapacity_limit ?? "-"}`
                      : "尚未配置四边形检测区域"}
                  </div>
                </div>
                <Button type="button" variant="outline" onClick={onOpenAreaConfig}>
                  配置区域
                </Button>
              </div>
            </div>
          ) : null}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button type="button" onClick={() => void onSubmit()}>
            {submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EventScopeSection({
  title,
  icon,
  options,
  selected,
  onToggle,
}: {
  title: string;
  icon: React.ReactNode;
  options: CameraEventOption[];
  selected: string[];
  onToggle: (value: string) => void;
}) {
  return (
    <div className="space-y-3 rounded-lg border border-border/60 p-4">
      <div className="flex items-center gap-2 text-sm font-medium text-foreground">
        {icon}
        {title}
      </div>
      <div className="flex flex-wrap gap-2">
        {options.map((item) => {
          const active = selected.includes(item.key);
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onToggle(item.key)}
              className={[
                "rounded-md border px-3 py-1.5 text-sm transition-colors",
                active
                  ? "border-red-500/60 bg-red-500/10 text-red-600"
                  : "border-border bg-background text-foreground hover:bg-muted",
              ].join(" ")}
            >
              {item.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
