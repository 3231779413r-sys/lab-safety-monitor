"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Calendar, ChevronDown, X } from "lucide-react";

type Preset = "all" | "today" | "3days" | "7days" | "30days" | "custom";

export interface TimeRange {
  start_time: string;
  end_time: string;
}

interface TimeRangePickerProps {
  value: TimeRange | null;
  onChange: (range: TimeRange | null) => void;
}

const presets: { key: Preset; label: string }[] = [
  { key: "all", label: "不限时间" },
  { key: "today", label: "今天" },
  { key: "3days", label: "近3天" },
  { key: "7days", label: "近7天" },
  { key: "30days", label: "近30天" },
  { key: "custom", label: "自定义" },
];

export function getPresetRange(key: Preset): TimeRange | null {
  if (key === "all") return null;
  const now = new Date();
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59);
  let start: Date;
  switch (key) {
    case "today":
      start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      break;
    case "3days":
      start = new Date(end);
      start.setDate(start.getDate() - 3);
      start.setHours(0, 0, 0, 0);
      break;
    case "7days":
      start = new Date(end);
      start.setDate(start.getDate() - 7);
      start.setHours(0, 0, 0, 0);
      break;
    case "30days":
      start = new Date(end);
      start.setDate(start.getDate() - 30);
      start.setHours(0, 0, 0, 0);
      break;
    default:
      start = new Date(end);
      start.setDate(start.getDate() - 7);
      start.setHours(0, 0, 0, 0);
  }
  return {
    start_time: start.toISOString(),
    end_time: end.toISOString(),
  };
}

export function TimeRangePicker({ value, onChange }: TimeRangePickerProps) {
  const [open, setOpen] = useState(false);
  const [activePreset, setActivePreset] = useState<Preset>(value ? "7days" : "all");

  const handlePreset = (key: Preset) => {
    setActivePreset(key);
    if (key === "all") {
      onChange(null);
      setOpen(false);
    } else if (key !== "custom") {
      onChange(getPresetRange(key)!);
      setOpen(false);
    }
  };

  const activeLabel = value ? presets.find((p) => p.key === activePreset)?.label : "不限时间";

  return (
    <div className="relative">
      <Button
        variant={value ? "default" : "outline"}
        size="sm"
        onClick={() => setOpen(!open)}
        className="gap-2 text-xs h-9 rounded-xl border-border/50"
      >
        <Calendar className="w-3.5 h-3.5" />
        <span className="hidden sm:inline">{activeLabel}</span>
        {value && (
          <X
            className="w-3 h-3 ml-0.5 hover:text-foreground"
            onClick={(e) => {
              e.stopPropagation();
              onChange(null);
              setActivePreset("all");
              setOpen(false);
            }}
          />
        )}
        <ChevronDown className="w-3 h-3" />
      </Button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            className="absolute right-0 top-full mt-1 z-50 w-72 p-3 rounded-xl border border-border/50 bg-card shadow-soft-lg"
          >
            <div className="flex flex-wrap gap-1.5 mb-3">
              {presets.map((p) => (
                <Button
                  key={p.key}
                  variant={activePreset === p.key ? "default" : "ghost"}
                  size="sm"
                  onClick={() => handlePreset(p.key)}
                  className="text-xs h-7 rounded-lg"
                >
                  {p.label}
                </Button>
              ))}
            </div>
            {activePreset === "custom" && value && (
              <div className="space-y-2 border-t border-border/50 pt-2">
                <div className="flex items-center gap-2">
                  <label className="text-xs text-muted-foreground w-8 shrink-0">从</label>
                  <input
                    type="datetime-local"
                    value={value.start_time.slice(0, 16)}
                    onChange={(e) => {
                      const dt = new Date(e.target.value);
                      onChange({ ...value, start_time: dt.toISOString() });
                    }}
                    className="flex-1 text-xs rounded-lg border border-border/50 bg-muted/30 px-2 py-1.5"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-xs text-muted-foreground w-8 shrink-0">至</label>
                  <input
                    type="datetime-local"
                    value={value.end_time.slice(0, 16)}
                    onChange={(e) => {
                      const dt = new Date(e.target.value);
                      onChange({ ...value, end_time: dt.toISOString() });
                    }}
                    className="flex-1 text-xs rounded-lg border border-border/50 bg-muted/30 px-2 py-1.5"
                  />
                </div>
                <Button
                  size="sm"
                  className="w-full text-xs h-7 rounded-lg"
                  onClick={() => setOpen(false)}
                >
                  确定
                </Button>
              </div>
            )}
          </motion.div>
        </>
      )}
    </div>
  );
}
