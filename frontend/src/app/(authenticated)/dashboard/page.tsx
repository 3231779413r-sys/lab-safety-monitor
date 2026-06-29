"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import Image from "next/image";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertTriangle,
  Cctv,
  TrendingUp,
  Users,
} from "lucide-react";

import { MotionWrapper } from "@/components/motion-wrapper";
import { PageLoader } from "@/components/page-loader";
import { AnimatedCounter } from "@/components/ui/animated-counter";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { resolveApiAssetUrl, VisualizationPeriod } from "@/lib/api";
import { formatPersonId, formatSafetyLabel } from "@/lib/formatters";
import { useLatestViolationSnapshots, useVisualizationStats } from "@/lib/queries";
import { cn } from "@/lib/utils";

const PERIOD_OPTIONS: Array<{ value: VisualizationPeriod; label: string }> = [
  { value: "today", label: "当日" },
  { value: "7d", label: "近7天" },
  { value: "30d", label: "近30天" },
];

const TREND_OPTIONS: Array<{ value: 7 | 30; label: string }> = [
  { value: 7, label: "近7天" },
  { value: 30, label: "近30天" },
];

const TYPE_COLORS = ["#ef4444", "#f97316", "#f59e0b", "#14b8a6", "#3b82f6"];
const SNAPSHOT_LIMIT = 5;

function areSnapshotListsEqual(
  left: Array<{ id: string }>,
  right: Array<{ id: string }>
) {
  return left.length === right.length && left.every((item, index) => item.id === right[index]?.id);
}

function formatDateTime(value?: string | null) {
  if (!value) {
    return "暂无记录";
  }
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatChartDate(value: string) {
  return new Date(value).toLocaleDateString("zh-CN", {
    month: "numeric",
    day: "numeric",
  });
}

function SegmentControl<T extends string | number>({
  options,
  value,
  onChange,
}: {
  options: Array<{ value: T; label: string }>;
  value: T;
  onChange: (nextValue: T) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-border/60 bg-muted/30 p-1">
      {options.map((option) => (
        <Button
          key={String(option.value)}
          type="button"
          size="sm"
          variant={value === option.value ? "default" : "ghost"}
          className="h-8 rounded-md px-3 text-xs"
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </Button>
      ))}
    </div>
  );
}

function EmptyBlock({ message, className = "h-[240px]" }: { message: string; className?: string }) {
  return (
    <div
      className={`flex items-center justify-center rounded-xl border border-dashed border-border/60 bg-muted/20 px-4 text-sm text-muted-foreground ${className}`}
    >
      {message}
    </div>
  );
}

function MetricPill({
  label,
  value,
  tone = "default",
  align = "left",
  split = false,
}: {
  label: string;
  value: string | number;
  tone?: "default" | "danger" | "primary";
  align?: "left" | "right";
  split?: boolean;
}) {
  const isNumber = typeof value === "number";

  return (
    <motion.div
      layout
      transition={{ duration: 0.28, ease: "easeOut" }}
      className={cn(
        "flex min-w-0 flex-1 rounded-full border px-4 py-3 shadow-sm backdrop-blur-sm md:px-5",
        tone === "danger" && "border-danger/20 bg-danger/6",
        tone === "primary" && "border-primary/20 bg-primary/6",
        tone === "default" && "border-border/70 bg-card/80",
        split ? "justify-between" : align === "right" ? "justify-end" : "justify-start"
      )}
    >
      <div
        className={cn(
          "min-w-0 flex w-full items-center gap-3",
          split ? "justify-between" : align === "right" ? "justify-end" : "justify-between"
        )}
      >
        <div className="text-[11px] tracking-normal text-muted-foreground md:text-xs whitespace-nowrap">
          {label}
        </div>
        <div className={cn("text-xl font-semibold text-foreground md:text-s", split && "text-right")}>
          {isNumber ? (
            <AnimatedCounter value={value} duration={650} />
          ) : (
            <AnimatePresence mode="wait" initial={false}>
              <motion.span
                key={`${label}-${value}`}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.22, ease: "easeOut" }}
                className="block truncate"
              >
                {value}
              </motion.span>
            </AnimatePresence>
          )}
        </div>
      </div>
    </motion.div>
  );
}

export default function DashboardPage() {
  const [trendDays, setTrendDays] = useState<7 | 30>(7);
  const [typePeriod, setTypePeriod] = useState<VisualizationPeriod>("today");
  const [rankingPeriod, setRankingPeriod] = useState<VisualizationPeriod>("today");
  const [cameraPeriod, setCameraPeriod] = useState<VisualizationPeriod>("7d");
  const [activeSnapshotId, setActiveSnapshotId] = useState<string | null>(null);
  const [displayedSnapshotItems, setDisplayedSnapshotItems] = useState<
    Array<{
      id: string;
      imageUrl: string;
      label: string;
      timestamp: string;
      personName: string;
      cameraName: string;
    }>
  >([]);
  const snapshotSyncTimerRef = useRef<number | null>(null);
  const snapshotTransitionTimerRef = useRef<number | null>(null);

  const { data, isLoading, isError } = useVisualizationStats({
    trendDays,
    typePeriod,
    rankingPeriod,
    cameraPeriod,
  });
  const { data: latestSnapshotEvents, isLoading: snapshotsLoading } = useLatestViolationSnapshots(SNAPSHOT_LIMIT);

  const snapshotItems = useMemo(
    () =>
      (latestSnapshotEvents ?? [])
        .map((event) => ({
          id: event.id,
          imageUrl: resolveApiAssetUrl(event.snapshot_url),
          label:
            event.violation_labels?.filter(Boolean).join("、") ||
            event.danger_event_types?.map((item) => formatSafetyLabel(item)).join("、") ||
            "危险事件",
          timestamp: event.timestamp,
          personName: event.person_name || formatPersonId(event.person_id),
          cameraName: event.camera_name || event.camera_id || "未知点位",
        }))
        .filter(
          (item): item is {
            id: string;
            imageUrl: string;
            label: string;
            timestamp: string;
            personName: string;
            cameraName: string;
          } => Boolean(item.imageUrl)
        ),
    [latestSnapshotEvents]
  );

  useEffect(() => {
    if (snapshotSyncTimerRef.current) {
      window.clearTimeout(snapshotSyncTimerRef.current);
      snapshotSyncTimerRef.current = null;
    }
    if (snapshotTransitionTimerRef.current) {
      window.clearTimeout(snapshotTransitionTimerRef.current);
      snapshotTransitionTimerRef.current = null;
    }

    snapshotSyncTimerRef.current = window.setTimeout(() => {
      if (snapshotItems.length === 0) {
        setDisplayedSnapshotItems([]);
        snapshotSyncTimerRef.current = null;
        return;
      }

      setDisplayedSnapshotItems((current) => {
        if (current.length === 0 || areSnapshotListsEqual(current, snapshotItems)) {
          return snapshotItems;
        }

        const hasHeadChanged = current[0]?.id !== snapshotItems[0]?.id;
        const shouldAnimateQueueShift =
          hasHeadChanged && current.length === SNAPSHOT_LIMIT && snapshotItems.length === SNAPSHOT_LIMIT;

        if (!shouldAnimateQueueShift) {
          return snapshotItems;
        }

        snapshotTransitionTimerRef.current = window.setTimeout(() => {
          setDisplayedSnapshotItems(snapshotItems);
          snapshotTransitionTimerRef.current = null;
        }, 300);

        return current.slice(0, SNAPSHOT_LIMIT - 1);
      });

      snapshotSyncTimerRef.current = null;
    }, 0);
  }, [snapshotItems]);

  useEffect(() => {
    return () => {
      if (snapshotSyncTimerRef.current) {
        window.clearTimeout(snapshotSyncTimerRef.current);
      }
      if (snapshotTransitionTimerRef.current) {
        window.clearTimeout(snapshotTransitionTimerRef.current);
      }
    };
  }, []);

  const activeSnapshot =
    displayedSnapshotItems.find((item) => item.id === activeSnapshotId) ?? displayedSnapshotItems[0] ?? null;
  const activeSnapshotIndex = activeSnapshot
    ? displayedSnapshotItems.findIndex((item) => item.id === activeSnapshot.id)
    : -1;

  const typeBreakdown = useMemo(
    () =>
      (data?.type_breakdown ?? [])
        .filter((item) => item.count > 0)
        .sort((a, b) => b.count - a.count),
    [data?.type_breakdown]
  );

  const topViolators = useMemo(
    () =>
      (data?.top_violators ?? []).slice(0, 5).map((item) => ({
        ...item,
        displayName: item.person_name || formatPersonId(item.person_id),
      })),
    [data?.top_violators]
  );

  const topCameraChartData = useMemo(
    () =>
      (data?.top_cameras ?? []).slice(0, 5).map((camera) => {
        const row: Record<string, string | number> = {
          camera_id: camera.camera_id,
          camera_name: camera.camera_name,
          total: camera.violation_count,
        };

        for (const item of camera.type_breakdown) {
          row[item.event_type] = item.count;
        }

        return row;
      }),
    [data?.top_cameras]
  );

  const topCameraTypeKeys = useMemo(() => {
    const orderedKeys: string[] = [];
    for (const camera of data?.top_cameras ?? []) {
      for (const item of camera.type_breakdown) {
        if (!orderedKeys.includes(item.event_type)) {
          orderedKeys.push(item.event_type);
        }
      }
    }
    return orderedKeys.slice(0, TYPE_COLORS.length);
  }, [data?.top_cameras]);

  const maxViolationCount = Math.max(...topViolators.map((item) => item.violation_count), 1);

  if (isLoading) {
    return <PageLoader />;
  }

  if (isError || !data) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center">
        <AlertTriangle className="h-12 w-12 text-danger" />
        <div className="space-y-1">
          <p className="text-lg font-semibold">可视化数据加载失败</p>
          <p className="text-sm text-muted-foreground">请检查后端接口或刷新页面后重试</p>
        </div>
      </div>
    );
  }

  return (
    <MotionWrapper className="flex h-full min-h-0 flex-col gap-4">
      <motion.section
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] xl:items-center"
      >
        <div className="order-2 grid grid-cols-2 gap-3 xl:order-none xl:max-w-[420px]">
          <MetricPill label="今日违规数" value={data.today_violation_count} tone="danger" />
          <MetricPill label="本周违规总数" value={data.week_violation_count} tone="danger" />
        </div>

        <div className="order-1 flex justify-center xl:order-none">
          <h1 className="text-center text-2xl font-semibold tracking-normal text-foreground md:text-3xl">
            可视化数据展示
          </h1>
        </div>

        <div className="order-3 grid grid-cols-2 gap-3 xl:justify-self-end xl:max-w-[460px]">
          <MetricPill
            label="在线监控数"
            value={data.online_camera_count}
            tone="primary"
            align="right"
            split
          />
          <MetricPill
            label="上次巡查时间"
            value={formatDateTime(data.last_inspection_time)}
            align="right"
            tone="primary"
            split
          />
        </div>
      </motion.section>

      <section className="grid min-h-0 flex-[1.18] gap-4 xl:grid-cols-[1.05fr_1.6fr_1.05fr]">
        <Card variant="glass" className="flex h-full min-h-0 gap-4 pt-3">
          <CardHeader className="flex flex-col gap-4 px-4">
            <div className="flex w-full items-center justify-between gap-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <AlertTriangle className="h-5 w-5 text-warning" />
                危险事件类型统计
              </CardTitle>
              <div className="ml-auto flex justify-end">
                <SegmentControl options={PERIOD_OPTIONS} value={typePeriod} onChange={setTypePeriod} />
              </div>
            </div>
          </CardHeader>
          <CardContent className="flex flex-1 px-4">
            {typeBreakdown.length > 0 ? (
              <div className="h-full min-h-0 flex-1">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={typeBreakdown}
                    layout="vertical"
                    margin={{ top: 8, right: 10, left: 8, bottom: 8 }}
                    barCategoryGap="18%"
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="currentColor" strokeOpacity={0.08} horizontal={false} />
                    <XAxis
                      type="number"
                      allowDecimals={false}
                      tick={{ fill: "currentColor", opacity: 0.65, fontSize: 11 }}
                      tickLine={false}
                      axisLine={false}
                    />
                    <YAxis
                      dataKey="label"
                      type="category"
                      width={92}
                      tick={{ fill: "currentColor", opacity: 0.75, fontSize: 11 }}
                      tickLine={false}
                      axisLine={false}
                    />
                    <Tooltip formatter={(value) => [`${value ?? 0}`, "次数"]} />
                    <Bar dataKey="count" radius={[0, 6, 6, 0]} maxBarSize={24}>
                      {typeBreakdown.map((entry, index) => (
                        <Cell key={entry.event_type} fill={TYPE_COLORS[index % TYPE_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <EmptyBlock message="当前时间范围内暂无危险事件类型统计数据" className="h-full min-h-[220px] flex-1" />
            )}
          </CardContent>
        </Card>

        <Card
          variant="glass"
          className="flex h-full min-h-0 gap-4 border-0 bg-transparent shadow-none py-0"
          style={{ border: "none", boxShadow: "none" }}
        >
          <CardContent className="flex h-full min-h-0 flex-col space-y-4 px-0">
            {activeSnapshot ? (
              <>
                <AnimatePresence mode="wait" initial={false}>
                  <motion.div
                    key={activeSnapshot.id}
                    initial={{ opacity: 0, y: 18, scale: 0.98 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: -18, scale: 0.98 }}
                    transition={{ duration: 0.3, ease: "easeOut" }}
                    className="relative mb-2 overflow-hidden rounded-xl bg-black/80"
                  >
                    <div className="relative aspect-[16/10] w-full">
                      <Image
                        src={activeSnapshot.imageUrl}
                        alt={activeSnapshot.label}
                        fill
                        unoptimized
                        className="object-cover"
                      />
                    </div>
                    <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/75 via-black/25 to-transparent px-4 py-4 text-white">
                      <div className="text-sm font-medium">{activeSnapshot.label}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-white/80">
                        <span>{activeSnapshot.personName}</span>
                        <span>{activeSnapshot.cameraName}</span>
                        <span>{formatDateTime(activeSnapshot.timestamp)}</span>
                      </div>
                    </div>
                  </motion.div>
                </AnimatePresence>

                <motion.div layout className="grid grid-cols-5 gap-3 ">
                  <AnimatePresence initial={false}>
                    {displayedSnapshotItems.map((item, index) => (
                      <motion.button
                        layout
                        key={item.id}
                        type="button"
                        onClick={() => setActiveSnapshotId(item.id)}
                        initial={{ opacity: 0, x: -28, scale: 0.92 }}
                        animate={{ opacity: 1, x: 0, scale: 1 }}
                        exit={{ opacity: 0, x: 28, scale: 0.9 }}
                        transition={{ duration: 0.28, ease: "easeOut" }}
                        className={cn(
                          "overflow-hidden rounded-lg border transition-all",
                          index === activeSnapshotIndex
                            ? "border-primary shadow-[0_0_0_1px_var(--color-primary)]"
                            : "border-border/50 opacity-75 hover:opacity-100"
                        )}
                      >
                        <div className="relative aspect-[4/3] bg-muted/30">
                          <Image
                            src={item.imageUrl}
                            alt={item.label}
                            fill
                            unoptimized
                            className="object-cover"
                          />
                        </div>
                      </motion.button>
                    ))}
                  </AnimatePresence>
                </motion.div>
              </>
            ) : snapshotsLoading ? (
              <PageLoader />
            ) : (
              <EmptyBlock message="暂无可展示的违规事件快照" className="h-full min-h-[220px] flex-1" />
            )}
          </CardContent>
        </Card>

        <Card variant="glass" className="flex h-full min-h-0 gap-4 pt-3">
          <CardHeader className="flex flex-col gap-4">
            <div className="flex w-full items-center justify-between gap-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Users className="h-5 w-5 text-primary" />
                违规人员统计
              </CardTitle>
              <div className="ml-auto flex justify-end">
                <SegmentControl options={PERIOD_OPTIONS} value={rankingPeriod} onChange={setRankingPeriod} />
              </div>
            </div>
          </CardHeader>
          <CardContent className="flex flex-1">
            {topViolators.length > 0 ? (
              <div className="flex h-full min-h-0 flex-1 flex-col space-y-3">
                {topViolators.map((item, index) => (
                  <div
                    key={item.person_id}
                    className="rounded-xl border border-border/50 bg-muted/15 px-4 py-3"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                          {index + 1}
                        </div>
                        <div className="flex min-w-0 items-center gap-2 text-sm">
                          <div className="truncate font-medium text-foreground">{item.displayName}</div>
                          <div className="shrink-0 text-xs text-muted-foreground">Top {index + 1}</div>
                        </div>
                      </div>
                      <div className="shrink-0 text-right text-sm font-medium text-foreground">
                        {item.violation_count} 次
                      </div>
                    </div>
                    <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary"
                        style={{ width: `${(item.violation_count / maxViolationCount) * 100}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyBlock message="当前时间范围内暂无违规人员统计数据" className="h-full min-h-[220px] flex-1" />
            )}
          </CardContent>
        </Card>
      </section>

      <section className="grid min-h-0 flex-[0.82] gap-4 xl:grid-cols-[1.3fr_1fr]">
        <Card variant="glass" className="flex h-full min-h-0 gap-4 pt-3">
          <CardHeader className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <CardTitle className="flex items-center gap-2 text-base">
              <TrendingUp className="h-5 w-5 text-danger" />
              危险事件数量趋势
            </CardTitle>
            <SegmentControl options={TREND_OPTIONS} value={trendDays} onChange={setTrendDays} />
          </CardHeader>
          <CardContent className="flex flex-1">
            {data.trend.length > 0 ? (
              <div className="h-full min-h-0 flex-1">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={data.trend} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="dangerTrendFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#ef4444" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="#ef4444" stopOpacity={0.03} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="currentColor" strokeOpacity={0.08} vertical={false} />
                    <XAxis
                      dataKey="date"
                      tickFormatter={formatChartDate}
                      tick={{ fill: "currentColor", opacity: 0.6, fontSize: 11 }}
                      tickLine={false}
                      axisLine={false}
                    />
                    <YAxis
                      allowDecimals={false}
                      tick={{ fill: "currentColor", opacity: 0.6, fontSize: 11 }}
                      tickLine={false}
                      axisLine={false}
                      width={32}
                    />
                    <Tooltip
                      formatter={(value) => [`${value ?? 0}`, "危险事件数"]}
                      labelFormatter={(label) => formatDateTime(`${label}T00:00:00`)}
                    />
                    <Area
                      type="monotone"
                      dataKey="violations"
                      stroke="#ef4444"
                      strokeWidth={2}
                      fill="url(#dangerTrendFill)"
                      activeDot={{ r: 4 }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <EmptyBlock message="当前时间范围内暂无危险事件趋势数据" className="h-full min-h-[220px] flex-1" />
            )}
          </CardContent>
        </Card>

        <Card variant="glass" className="flex h-full min-h-0 gap-4 pt-3">
          <CardHeader className="flex flex-col gap-4">
            <div className="flex w-full items-center justify-between gap-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Cctv className="h-5 w-5 text-primary" />
                区域违规统计
              </CardTitle>
              <div className="ml-auto flex justify-end">
                <SegmentControl options={PERIOD_OPTIONS} value={cameraPeriod} onChange={setCameraPeriod} />
              </div>
            </div>
          </CardHeader>
          <CardContent className="flex flex-1">
            {topCameraChartData.length > 0 ? (
              <div className="h-full min-h-0 flex-1">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={topCameraChartData}
                    layout="vertical"
                    margin={{ top: 8, right: 6, left: 8, bottom: 8 }}
                    barCategoryGap="18%"
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="currentColor" strokeOpacity={0.08} horizontal={false} />
                    <XAxis
                      type="number"
                      allowDecimals={false}
                      tick={{ fill: "currentColor", opacity: 0.65, fontSize: 11 }}
                      tickLine={false}
                      axisLine={false}
                    />
                    <YAxis
                      dataKey="camera_name"
                      type="category"
                      width={92}
                      tick={{ fill: "currentColor", opacity: 0.75, fontSize: 11 }}
                      tickLine={false}
                      axisLine={false}
                    />
                    <Tooltip
                      formatter={(value, name) => [`${value ?? 0}`, String(name)]}
                      labelFormatter={(label) => `摄像头：${label}`}
                    />
                    {topCameraTypeKeys.map((eventType, index) => (
                      <Bar
                        key={eventType}
                        dataKey={eventType}
                        name={
                          data.top_cameras
                            .flatMap((camera) => camera.type_breakdown)
                            .find((item) => item.event_type === eventType)?.label ?? eventType
                        }
                        stackId="camera"
                        radius={index === topCameraTypeKeys.length - 1 ? [0, 6, 6, 0] : [0, 0, 0, 0]}
                        fill={TYPE_COLORS[index % TYPE_COLORS.length]}
                        maxBarSize={24}
                      />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <EmptyBlock message="当前时间范围内暂无区域违规统计数据" className="h-full min-h-[220px] flex-1" />
            )}
          </CardContent>
        </Card>
      </section>
    </MotionWrapper>
  );
}
