"use client";

import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { FileSearch, Video } from "lucide-react";

import { EventsTable } from "@/components/events-table";
import { MotionWrapper } from "@/components/motion-wrapper";
import { PageLoader } from "@/components/page-loader";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCameras, useEvents } from "@/lib/queries";

const VIOLATION_TYPE_OPTIONS = [
  { value: "all", label: "全部危险行为" },
  { value: "hardhat", label: "未佩戴安全帽" },
  { value: "goggles", label: "未佩戴护目镜" },
  { value: "mask", label: "未佩戴口罩" },
  { value: "safety_vest", label: "未穿戴安全背心" },
  { value: "work_clothes", label: "未穿工作服" },
  { value: "safety_shoes", label: "未穿戴防护鞋" },
  { value: "gloves", label: "未佩戴防护手套" },
  { value: "respirator", label: "未佩戴防毒口罩" },
  { value: "fall_detected", label: "人员跌倒" },
  { value: "missed_inspection", label: "未巡检" },
  { value: "area_missed_inspection", label: "区域漏巡" },
  { value: "unauthorized_intrusion", label: "违规闯入" },
  { value: "overtime_stay", label: "超时驻留" },
  { value: "blind_spot_stay", label: "盲区驻留" },
  { value: "area_overcapacity", label: "区域超员" },
  { value: "workshop_overcapacity", label: "车间超员" },
] as const;

const PAGE_SIZE = 50;

function getTodayDateString() {
  const now = new Date();
  const year = now.getFullYear();
  const month = `${now.getMonth() + 1}`.padStart(2, "0");
  const day = `${now.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function buildDateTimeRange(date: string, startTime: string, endTime: string) {
  if (!date) {
    return {
      startTime: undefined,
      endTime: undefined,
    };
  }

  return {
    startTime: `${date}T${startTime || "00:00"}:00`,
    endTime: `${date}T${endTime || "23:59"}:59`,
  };
}

export default function EventsPage() {
  const [selectedCameraId, setSelectedCameraId] = useState("all");
  const [selectedDate, setSelectedDate] = useState(getTodayDateString);
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [selectedViolationType, setSelectedViolationType] = useState("all");
  const [personNameInput, setPersonNameInput] = useState("");
  const [debouncedPersonName, setDebouncedPersonName] = useState("");
  const [currentPage, setCurrentPage] = useState(1);

  const { data: cameras } = useCameras();
  const resetToFirstPage = () => setCurrentPage(1);

  const handleSelectedDateChange = (value: string) => {
    setSelectedDate(value);
    resetToFirstPage();
  };

  const handleStartTimeChange = (value: string) => {
    setStartTime(value);
    resetToFirstPage();
  };

  const handleEndTimeChange = (value: string) => {
    setEndTime(value);
    resetToFirstPage();
  };

  const handleViolationTypeChange = (value: string) => {
    setSelectedViolationType(value);
    resetToFirstPage();
  };

  const handleCameraChange = (value: string) => {
    setSelectedCameraId(value);
    resetToFirstPage();
  };

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedPersonName(personNameInput.trim());
      setCurrentPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [personNameInput]);

  const queryParams = useMemo(
    () => {
      const range = buildDateTimeRange(selectedDate, startTime, endTime);
      return {
      page: currentPage,
      pageSize: PAGE_SIZE,
      cameraId: selectedCameraId === "all" ? undefined : selectedCameraId,
      personName: debouncedPersonName || undefined,
      startTime: range.startTime,
      endTime: range.endTime,
      violationType: selectedViolationType === "all" ? undefined : selectedViolationType,
      violationsOnly: selectedViolationType !== "all" ? true : undefined,
      };
    },
    [currentPage, selectedCameraId, debouncedPersonName, selectedDate, startTime, endTime, selectedViolationType]
  );

  const { data: eventsData, isLoading } = useEvents(queryParams);
  const events = eventsData?.events ?? [];
  const total = eventsData?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageStart = total === 0 ? 0 : (currentPage - 1) * PAGE_SIZE + 1;
  const pageEnd = Math.min(currentPage * PAGE_SIZE, total);

  if (isLoading) {
    return <PageLoader />;
  }

  return (
    <MotionWrapper className="space-y-6">


      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
        <Card variant="glass">

          <CardContent>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-6">
              <div className="space-y-2">
                <Label htmlFor="event-date">日期</Label>
                <Input
                  id="event-date"
                  type="date"
                  value={selectedDate}
                  onChange={(event) => handleSelectedDateChange(event.target.value)}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="start-time">开始时间</Label>
                <Input
                  id="start-time"
                  type="time"
                  value={startTime}
                  onChange={(event) => handleStartTimeChange(event.target.value)}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="end-time">结束时间</Label>
                <Input
                  id="end-time"
                  type="time"
                  value={endTime}
                  onChange={(event) => handleEndTimeChange(event.target.value)}
                />
              </div>

              <div className="space-y-2">
                <Label>危险行为类型</Label>
                <Select value={selectedViolationType} onValueChange={handleViolationTypeChange}>
                  <SelectTrigger>
                    <SelectValue placeholder="请选择危险行为类型" />
                  </SelectTrigger>
                  <SelectContent>
                    {VIOLATION_TYPE_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label>摄像头点位</Label>
                <Select value={selectedCameraId} onValueChange={handleCameraChange}>
                  <SelectTrigger>
                    <SelectValue placeholder="请选择摄像头点位" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部摄像头</SelectItem>
                    {cameras?.map((camera) => (
                      <SelectItem key={camera.id} value={camera.id}>
                        <div className="flex items-center gap-2">
                          <Video className="h-4 w-4" />
                          {camera.name}
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="person-name-search">姓名搜索</Label>
                <Input
                  id="person-name-search"
                  value={personNameInput}
                  onChange={(event) => setPersonNameInput(event.target.value)}
                  placeholder="输入姓名自动筛选"
                />
              </div>
            </div>
          </CardContent>
        </Card>
      </motion.div>

      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }}>
        <Card variant="glass" className="gap-0 overflow-hidden py-0">
          <CardContent className="p-0">
            {events.length === 0 ? (
              <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                className="flex flex-col items-center justify-center px-6 py-16 text-center"
              >
                <motion.div
                  animate={{ y: [0, -5, 0] }}
                  transition={{ duration: 2, repeat: Infinity }}
                  className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-muted"
                >
                  <FileSearch className="h-8 w-8 text-muted-foreground" />
                </motion.div>
                <p className="font-medium text-foreground">没有查到符合条件的历史记录</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  可以调整筛选条件后重试
                </p>
              </motion.div>
            ) : (
              <>
                <EventsTable events={events} cameras={cameras} loading={false} />
                <div className="flex flex-col gap-3 border-t border-border/50 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-sm text-muted-foreground">
                    第 {currentPage} 页，共 {totalPages} 页，显示 {pageStart}-{pageEnd} / {total} 条
                  </p>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setCurrentPage((page) => Math.max(1, page - 1))}
                      disabled={currentPage <= 1}
                    >
                      上一页
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setCurrentPage((page) => Math.min(totalPages, page + 1))}
                      disabled={currentPage >= totalPages}
                    >
                      下一页
                    </Button>
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </motion.div>
    </MotionWrapper>
  );
}
