"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Person } from "@/lib/api";
import api from "@/lib/api";
import { Edit2, Check, X, User, Calendar, AlertTriangle, TrendingUp } from "lucide-react";
import { cn } from "@/lib/utils";

interface PersonsTableProps {
  persons: Person[];
  loading?: boolean;
  onUpdate?: () => void;
}

export function PersonsTable({ persons, loading = false, onUpdate }: PersonsTableProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState<string>("");
  const [saving, setSaving] = useState(false);

  if (loading) {
    return (
      <div className="space-y-3">
        {[...Array(5)].map((_, i) => (
          <div
            key={i}
            className="flex items-center gap-4 p-4 rounded-xl border border-border/50"
          >
            <Skeleton className="h-12 w-12 rounded-xl shimmer" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-4 w-32 shimmer" />
              <Skeleton className="h-3 w-48 shimmer" />
            </div>
            <Skeleton className="h-8 w-24 rounded-full shimmer" />
          </div>
        ))}
      </div>
    );
  }

  if (persons.length === 0) {
    return null;
  }

  const handleStartEdit = (person: Person) => {
    setEditingId(person.id);
    setEditName(person.name || "");
  };

  const handleCancelEdit = () => {
    setEditingId(null);
    setEditName("");
  };

  const handleSaveEdit = async (personId: string) => {
    setSaving(true);
    try {
      await api.updatePerson(personId, { name: editName.trim() || null });
      setEditingId(null);
      setEditName("");
      if (onUpdate) {
        onUpdate();
      }
    } catch (error) {
      console.error("Failed to update person:", error);
      alert("更新人员姓名失败");
    } finally {
      setSaving(false);
    }
  };

  const getComplianceColor = (rate: number) => {
    if (rate >= 90) return "success";
    if (rate >= 70) return "warning";
    return "danger";
  };

  const getComplianceProgressColor = (rate: number) => {
    if (rate >= 90) return "bg-success";
    if (rate >= 70) return "bg-warning";
    return "bg-danger";
  };

  return (
    <div className="rounded-xl border border-border/50 overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow className="bg-muted/30 hover:bg-muted/30">
            <TableHead className="font-semibold">人员</TableHead>
            <TableHead className="font-semibold">首次出现</TableHead>
            <TableHead className="font-semibold">最后出现</TableHead>
            <TableHead className="font-semibold text-center">事件</TableHead>
            <TableHead className="font-semibold text-center">违规</TableHead>
            <TableHead className="font-semibold">合规率</TableHead>
            <TableHead className="font-semibold text-center">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {persons.map((person) => {
            const complianceColor = getComplianceColor(person.compliance_rate);
            return (
              <TableRow
                key={person.id}
                className="transition-colors hover:bg-muted/50"
              >
                <TableCell>
                  <div className="flex items-center gap-3">
                    <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-primary/20 to-primary/5 border border-primary/20">
                      <User className="w-5 h-5 text-primary" />
                    </div>
                    <div>
                      {editingId === person.id ? (
                        <>
                          <input
                            type="text"
                            value={editName}
                            onChange={(e) => setEditName(e.target.value)}
                            placeholder="输入姓名"
                            className="w-32 px-3 py-1.5 border border-border rounded-lg text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/50 transition-all"
                            disabled={saving}
                            autoFocus
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                handleSaveEdit(person.id);
                              } else if (e.key === "Escape") {
                                handleCancelEdit();
                              }
                            }}
                          />
                          <p className="text-[10px] text-muted-foreground/60 mt-1">
                            <kbd className="px-1 py-0.5 bg-muted rounded text-[10px]">Enter</kbd> 保存，<kbd className="px-1 py-0.5 bg-muted rounded text-[10px]">Esc</kbd> 取消
                          </p>
                        </>
                      ) : (
                        <>
                          <p className="font-medium">
                            {person.name || (
                              <span className="text-muted-foreground italic">
                                未命名
                              </span>
                            )}
                          </p>
                          <p className="text-xs font-mono text-muted-foreground">
                            {person.id.substring(0, 8)}...
                          </p>
                          <p className="text-[10px] text-muted-foreground/60">
                            点击编辑重命名
                          </p>
                        </>
                      )}
                    </div>
                  </div>
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-2 text-sm">
                    <Calendar className="w-3.5 h-3.5 text-muted-foreground" />
                    <span>{new Date(person.first_seen).toLocaleDateString()}</span>
                  </div>
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-2 text-sm">
                    <Calendar className="w-3.5 h-3.5 text-muted-foreground" />
                    <span>{new Date(person.last_seen).toLocaleDateString()}</span>
                  </div>
                </TableCell>
                <TableCell className="text-center">
                  <Badge variant="secondary" className="tabular-nums">
                    {person.total_events}
                  </Badge>
                </TableCell>
                <TableCell className="text-center">
                  <Badge
                    variant={person.violation_count > 0 ? "danger-soft" : "success-soft"}
                    className="tabular-nums"
                  >
                    <AlertTriangle
                      className={cn(
                        "w-3 h-3 mr-1",
                        person.violation_count > 0 ? "text-danger" : "text-success"
                      )}
                    />
                    {person.violation_count}
                  </Badge>
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-3 min-w-[180px]">
                    <div className="flex-1">
                      <div className="h-2 w-full bg-muted rounded-full overflow-hidden">
                        <div
                          className={cn(
                            "h-full rounded-full transition-all duration-500",
                            getComplianceProgressColor(person.compliance_rate)
                          )}
                          style={{ width: `${person.compliance_rate}%` }}
                        />
                      </div>
                    </div>
                    <Badge variant={complianceColor} className="tabular-nums min-w-[60px] justify-center">
                      <TrendingUp className="w-3 h-3 mr-1" />
                      {person.compliance_rate.toFixed(1)}%
                    </Badge>
                  </div>
                </TableCell>
                <TableCell className="text-center">
                  {editingId === person.id ? (
                    <div className="flex justify-center gap-1">
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        onClick={() => handleSaveEdit(person.id)}
                        disabled={saving}
                        className="h-8 w-8 text-success hover:text-success hover:bg-success/10"
                      >
                        <Check className="w-4 h-4" />
                      </Button>
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        onClick={handleCancelEdit}
                        disabled={saving}
                        className="h-8 w-8 text-danger hover:text-danger hover:bg-danger/10"
                      >
                        <X className="w-4 h-4" />
                      </Button>
                    </div>
                  ) : (
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      onClick={() => handleStartEdit(person)}
                      className="h-8 w-8 hover:bg-primary/10 hover:text-primary"
                    >
                      <Edit2 className="w-4 h-4" />
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
