"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Camera, ImagePlus, RefreshCw, ScanFace, UserSearch, Video } from "lucide-react";

import { MotionWrapper } from "@/components/motion-wrapper";
import { PageLoader } from "@/components/page-loader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import api, { resolveApiAssetUrl } from "@/lib/api";
import { useCameras, useCompareFaceAgainstRegistry, useCompareFaceFromCamera } from "@/lib/queries";

function similarityLabel(value: number) {
  return `${value.toFixed(1)} 分`;
}

function cosineLabel(value?: number | null) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  return value.toFixed(4);
}

function subjectTypeLabel(value: string) {
  switch (value) {
    case "employee":
      return "员工";
    case "external_person":
      return "外来人员";
    case "external_registration":
      return "外来预约";
    default:
      return value;
  }
}

const FACE_MATCH_SCORE_THRESHOLD = 65;

function MatchSummary({
  title,
  match,
}: {
  title: string;
  match: {
    name: string;
    subject_type: string;
    organization?: string | null;
    similarity: number;
    cosine_similarity?: number | null;
    face_image_url?: string | null;
  } | null;
}) {
  return (
    <div className="rounded-xl border border-border/60 bg-background/50 p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-foreground">
        <UserSearch className="h-4 w-4 text-primary" />
        <span>{title}</span>
      </div>
      {match ? (
        <div className="space-y-3">
          {match.face_image_url ? (
            <img
              src={resolveApiAssetUrl(match.face_image_url) || undefined}
              alt={match.name}
              className="h-36 w-36 rounded-lg border border-border/60 object-cover"
            />
          ) : (
            <div className="flex h-36 w-36 items-center justify-center rounded-lg border border-dashed border-border/60 text-sm text-muted-foreground">
              无原图
            </div>
          )}
          <div className="space-y-1 text-sm">
            <div className="font-medium text-foreground">{match.name}</div>
            <div className="text-muted-foreground">类型：{subjectTypeLabel(match.subject_type)}</div>
            <div className="text-muted-foreground">单位：{match.organization || "-"}</div>
            <div className="text-primary">匹配分：{similarityLabel(match.similarity)}</div>
            <div className="text-xs text-muted-foreground">
              余弦相似度：{cosineLabel(match.cosine_similarity)}
            </div>
          </div>
        </div>
      ) : (
        <div className="flex h-64 items-center justify-center rounded-lg border border-dashed border-border/60 text-sm text-muted-foreground">
          暂无匹配结果
        </div>
      )}
    </div>
  );
}

export default function FaceTestPage() {
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [selectedCameraId, setSelectedCameraId] = useState<string>("");
  const [cameraPolling, setCameraPolling] = useState(false);
  const cameraPollingLock = useRef(false);
  const compareFace = useCompareFaceAgainstRegistry();
  const compareCameraFace = useCompareFaceFromCamera();
  const compareCameraFaceAsync = compareCameraFace.mutateAsync;
  const resetCameraFaceResult = compareCameraFace.reset;
  const { data: cameras = [], isLoading: camerasLoading } = useCameras();

  const imageResult = compareFace.data;
  const imageBestMatch = imageResult?.best_match ?? null;
  const cameraResult = compareCameraFace.data;
  const cameraBestMatch = cameraResult?.best_match ?? null;
  const imageErrorText = compareFace.error instanceof Error ? compareFace.error.message : null;
  const cameraErrorText = compareCameraFace.error instanceof Error ? compareCameraFace.error.message : null;

  const activeCameras = useMemo(() => cameras, [cameras]);
  const selectedCamera = useMemo(
    () => activeCameras.find((item) => item.id === selectedCameraId) ?? null,
    [activeCameras, selectedCameraId]
  );

  useEffect(() => {
    if (!selectedCameraId && activeCameras.length > 0) {
      setSelectedCameraId(activeCameras[0].id);
    }
  }, [activeCameras, selectedCameraId]);

  useEffect(() => {
    resetCameraFaceResult();
  }, [resetCameraFaceResult, selectedCameraId]);

  useEffect(() => {
    if (!cameraPolling || !selectedCameraId) {
      return;
    }

    let cancelled = false;
    const tick = async () => {
      if (cancelled || cameraPollingLock.current) {
        return;
      }
      cameraPollingLock.current = true;
      try {
        await compareCameraFaceAsync(selectedCameraId);
      } catch {
        // keep polling; status is reflected by existing result/error state
      } finally {
        cameraPollingLock.current = false;
      }
    };

    void tick();
    const timer = window.setInterval(() => {
      void tick();
    }, 3000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [cameraPolling, selectedCameraId, compareCameraFaceAsync]);

  const imageStatusText = useMemo(() => {
    if (imageErrorText) {
      return imageErrorText;
    }
    if (!imageResult) {
      return "上传一张来自摄像头截图或单独采集的人脸图片，验证是否能识别到人脸库中的人员。";
    }
    if (imageResult.matched && imageBestMatch) {
      return `已识别：${imageBestMatch.name}（${subjectTypeLabel(imageBestMatch.subject_type)}）`;
    }
    return `未达到匹配阈值（>${FACE_MATCH_SCORE_THRESHOLD} 分）`;
  }, [imageBestMatch, imageErrorText, imageResult]);

  const cameraStatusText = useMemo(() => {
    if (cameraErrorText) {
      return cameraErrorText;
    }
    if (!selectedCameraId) {
      return "请先选择一个系统摄像头。";
    }
    if (!cameraResult) {
      return "显示原始监控画面，并定时抓取最新帧做人脸比对。不会叠加其他安全检测结果。";
    }
    if (!cameraResult.face_detected) {
      return "当前画面未检测到可用人脸。";
    }
    if (cameraResult.matched && cameraBestMatch) {
      return `已识别：${cameraBestMatch.name}（${subjectTypeLabel(cameraBestMatch.subject_type)}）`;
    }
    return `检测到人脸，但未达到匹配阈值（>${FACE_MATCH_SCORE_THRESHOLD} 分）。`;
  }, [cameraBestMatch, cameraErrorText, cameraResult, selectedCameraId]);

  const handleFileChange = (nextFile: File | null) => {
    setFile(nextFile);
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
    }
    setPreviewUrl(nextFile ? URL.createObjectURL(nextFile) : null);
  };

  const handleCompare = async () => {
    if (!file) {
      return;
    }
    await compareFace.mutateAsync(file);
  };

  const handleCameraCompare = async () => {
    if (!selectedCameraId) {
      return;
    }
    await compareCameraFaceAsync(selectedCameraId);
  };

  if (compareFace.isPending && !imageResult) {
    return <PageLoader />;
  }

  return (
    <MotionWrapper className="space-y-6">
      <motion.section
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="grid gap-6 xl:grid-cols-[1.1fr_1.4fr]"
      >
        <Card variant="glass" className="gap-4">
          <CardContent className="space-y-5">
            <div className="flex items-center gap-2 text-lg font-semibold text-foreground">
              <ScanFace className="h-5 w-5 text-primary" />
              <span>图片人脸比对</span>
            </div>
            <div className="rounded-xl border border-border/60 bg-background/50 p-4 text-sm text-muted-foreground">
              {imageStatusText}
            </div>
            <div className="space-y-2">
              <div className="text-sm font-medium text-foreground">待识别人脸</div>
              <Input
                type="file"
                accept="image/*"
                onChange={(event) => handleFileChange(event.target.files?.[0] ?? null)}
              />
              <div className="text-xs text-muted-foreground">
                建议上传摄像头截图中裁剪出的人脸，或直接上传单张正脸照片。
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-xl border border-border/60 bg-background/50 p-4">
                <div className="mb-3 flex items-center gap-2 text-sm font-medium text-foreground">
                  <ImagePlus className="h-4 w-4 text-primary" />
                  <span>上传预览</span>
                </div>
                {previewUrl ? (
                  <img
                    src={previewUrl}
                    alt="上传人脸预览"
                    className="h-64 w-full rounded-lg object-cover"
                  />
                ) : (
                  <div className="flex h-64 items-center justify-center rounded-lg border border-dashed border-border/60 text-sm text-muted-foreground">
                    暂未选择图片
                  </div>
                )}
              </div>
              <MatchSummary title="最佳匹配" match={imageBestMatch} />
            </div>
            <Button
              type="button"
              onClick={handleCompare}
              disabled={!file || compareFace.isPending}
              className="w-full"
            >
              <Camera className="h-4 w-4" />
              {compareFace.isPending ? "识别中..." : "开始比对"}
            </Button>
          </CardContent>
        </Card>

        <Card variant="glass" className="gap-4">
          <CardContent className="space-y-4">
            <div className="flex items-center gap-2 text-lg font-semibold text-foreground">
              <UserSearch className="h-5 w-5 text-primary" />
              <span>图片候选结果</span>
            </div>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
              {imageResult?.candidates?.length ? (
                imageResult.candidates.map((candidate) => (
                  <div
                    key={`${candidate.subject_type}-${candidate.subject_id}`}
                    className="flex gap-4 rounded-xl border border-border/60 bg-background/50 p-4"
                  >
                    {candidate.face_image_url ? (
                      <img
                        src={resolveApiAssetUrl(candidate.face_image_url) || undefined}
                        alt={candidate.name}
                        className="h-24 w-24 rounded-lg border border-border/60 object-cover"
                      />
                    ) : (
                      <div className="flex h-24 w-24 shrink-0 items-center justify-center rounded-lg border border-dashed border-border/60 text-xs text-muted-foreground">
                        无图
                      </div>
                    )}
                    <div className="min-w-0 flex-1 space-y-1 text-sm">
                      <div className="truncate font-medium text-foreground">{candidate.name}</div>
                      <div className="text-muted-foreground">类型：{subjectTypeLabel(candidate.subject_type)}</div>
                      <div className="truncate text-muted-foreground">单位：{candidate.organization || "-"}</div>
                      <div className="text-primary">匹配分：{similarityLabel(candidate.similarity)}</div>
                      <div className="text-xs text-muted-foreground">
                        余弦相似度：{cosineLabel(candidate.cosine_similarity)}
                      </div>
                    </div>
                  </div>
                ))
              ) : (
                <div className="flex min-h-[320px] items-center justify-center rounded-xl border border-dashed border-border/60 text-sm text-muted-foreground">
                  暂无候选结果
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      </motion.section>

      <motion.section
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="grid gap-6 xl:grid-cols-[1.2fr_1.3fr]"
      >
        <Card variant="glass" className="gap-4">
          <CardContent className="space-y-5">
            <div className="flex items-center gap-2 text-lg font-semibold text-foreground">
              <Video className="h-5 w-5 text-primary" />
              <span>摄像头实时人脸比对</span>
            </div>
            <div className="rounded-xl border border-border/60 bg-background/50 p-4 text-sm text-muted-foreground">
              {cameraStatusText}
            </div>
            <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto_auto] md:items-end">
              <div className="space-y-2">
                <div className="text-sm font-medium text-foreground">系统摄像头</div>
                <Select value={selectedCameraId} onValueChange={setSelectedCameraId}>
                  <SelectTrigger>
                    <SelectValue placeholder={camerasLoading ? "加载中..." : "选择摄像头"} />
                  </SelectTrigger>
                  <SelectContent>
                    {activeCameras.map((camera) => (
                      <SelectItem key={camera.id} value={camera.id}>
                        {camera.name}{camera.enabled ? "" : "（未启用）"}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button
                type="button"
                variant={cameraPolling ? "default" : "outline"}
                disabled={!selectedCameraId}
                onClick={() => setCameraPolling((current) => !current)}
                className="min-w-[128px]"
              >
                <Video className="h-4 w-4" />
                {cameraPolling ? "停止轮询" : "开始轮询"}
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={!selectedCameraId || compareCameraFace.isPending}
                onClick={handleCameraCompare}
                className="min-w-[128px]"
              >
                <RefreshCw className={`h-4 w-4 ${compareCameraFace.isPending ? "animate-spin" : ""}`} />
                单次识别
              </Button>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-xl border border-border/60 bg-background/50 p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                    <Camera className="h-4 w-4 text-primary" />
                    <span>原始监控画面</span>
                  </div>
                  <Badge variant={cameraPolling ? "success-soft" : "outline"}>
                    {cameraPolling ? "轮询中" : "已停止"}
                  </Badge>
                </div>
                {selectedCamera ? (
                  <img
                    src={api.getCameraFacePreviewUrl(selectedCamera.id)}
                    alt={`${selectedCamera.name} 原始画面`}
                    className="h-72 w-full rounded-lg border border-border/60 object-cover"
                  />
                ) : (
                  <div className="flex h-72 items-center justify-center rounded-lg border border-dashed border-border/60 text-sm text-muted-foreground">
                    暂无可用摄像头
                  </div>
                )}
              </div>
              <MatchSummary title="实时最佳匹配" match={cameraBestMatch} />
            </div>
          </CardContent>
        </Card>

        <Card variant="glass" className="gap-4">
          <CardContent className="space-y-4">
            <div className="flex items-center gap-2 text-lg font-semibold text-foreground">
              <UserSearch className="h-5 w-5 text-primary" />
              <span>摄像头候选结果</span>
            </div>
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Badge variant={cameraResult?.face_detected ? "success-soft" : "outline"}>
                {cameraResult?.face_detected ? "已检测到人脸" : "未检测到人脸"}
              </Badge>
              {selectedCamera ? (
                <Badge variant={selectedCamera.enabled ? "info-soft" : "warning-soft"}>
                  {selectedCamera.enabled ? "已启用" : "未启用"}
                </Badge>
              ) : null}
              <span>{selectedCamera?.name || "未选择摄像头"}</span>
            </div>
            <div className="grid gap-4">
              {cameraResult?.candidates?.length ? (
                cameraResult.candidates.map((candidate) => (
                  <div
                    key={`camera-${candidate.subject_type}-${candidate.subject_id}`}
                    className="flex gap-4 rounded-xl border border-border/60 bg-background/50 p-4"
                  >
                    {candidate.face_image_url ? (
                      <img
                        src={resolveApiAssetUrl(candidate.face_image_url) || undefined}
                        alt={candidate.name}
                        className="h-24 w-24 rounded-lg border border-border/60 object-cover"
                      />
                    ) : (
                      <div className="flex h-24 w-24 shrink-0 items-center justify-center rounded-lg border border-dashed border-border/60 text-xs text-muted-foreground">
                        无图
                      </div>
                    )}
                    <div className="min-w-0 flex-1 space-y-1 text-sm">
                      <div className="truncate font-medium text-foreground">{candidate.name}</div>
                      <div className="text-muted-foreground">类型：{subjectTypeLabel(candidate.subject_type)}</div>
                      <div className="truncate text-muted-foreground">单位：{candidate.organization || "-"}</div>
                      <div className="text-primary">匹配分：{similarityLabel(candidate.similarity)}</div>
                      <div className="text-xs text-muted-foreground">
                        余弦相似度：{cosineLabel(candidate.cosine_similarity)}
                      </div>
                    </div>
                  </div>
                ))
              ) : (
                <div className="flex min-h-[320px] items-center justify-center rounded-xl border border-dashed border-border/60 text-sm text-muted-foreground">
                  暂无候选结果
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      </motion.section>
    </MotionWrapper>
  );
}
