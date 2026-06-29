"use client";

import { ChangeEvent, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  CalendarDays,
  Check,
  ChevronsUpDown,
  Edit3,
  Plus,
  Search,
  Trash2,
  UserRound,
  Users,
} from "lucide-react";
import { toast } from "sonner";

import { MotionWrapper } from "@/components/motion-wrapper";
import { PageLoader } from "@/components/page-loader";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import api, {
  ExternalPersonListItem,
  JobTitleOption,
  PersonListItem,
  resolveApiAssetUrl,
  ShiftScheduleRow,
  SupervisionCamera,
  SupervisionEventOption,
} from "@/lib/api";
import {
  useCreateExternalPerson,
  useCreateNextSchedule,
  useCreatePerson,
  useDeleteExternalPerson,
  useDeletePerson,
  useExternalPersons,
  useJobTitleOptions,
  usePersons,
  useSupervisionCameras,
  useSupervisionEvents,
  useTodaySchedule,
  useUpdateExternalPerson,
  useUpdatePerson,
  useUpdateTodaySchedule,
  useUploadExternalPersonFace,
  useUploadPersonFace,
} from "@/lib/queries";
import { cn } from "@/lib/utils";

type EmployeeFormState = {
  name: string;
  workshop: string;
  job_title: string;
  supervision_scope: string[];
};

type ExternalFormState = {
  name: string;
  organization: string;
  supervision_scope: string[];
  allowed_camera_ids: string[];
};

const EMPTY_EMPLOYEE_FORM: EmployeeFormState = {
  name: "",
  workshop: "",
  job_title: "",
  supervision_scope: [],
};

const EMPTY_EXTERNAL_FORM: ExternalFormState = {
  name: "",
  organization: "",
  supervision_scope: [],
  allowed_camera_ids: [],
};

const FALLBACK_SUPERVISION_EVENTS: SupervisionEventOption[] = [
  { key: "hardhat", label: "未佩戴安全帽" },
  { key: "mask", label: "未佩戴口罩" },
  { key: "safety_vest", label: "未穿戴安全背心" },
  { key: "safety_shoes", label: "未穿戴防护鞋" },
  { key: "gloves", label: "未佩戴防护手套" },
  { key: "goggles", label: "未佩戴护目镜" },
  { key: "respirator", label: "未佩戴防毒口罩" },
  { key: "unauthorized_intrusion", label: "违规闯入" },
  { key: "overtime_stay", label: "超时驻留" },
  { key: "fall_detected", label: "人员跌倒" },
];

function normalizeEmployeePayload(form: EmployeeFormState) {
  return {
    name: form.name.trim(),
    workshop: form.workshop.trim() || null,
    job_title: form.job_title.trim() || null,
    supervision_scope: form.supervision_scope,
  };
}

function normalizeExternalPayload(form: ExternalFormState) {
  return {
    name: form.name.trim(),
    organization: form.organization.trim(),
    supervision_scope: form.supervision_scope,
    allowed_camera_ids: form.allowed_camera_ids,
  };
}

function renderShiftNames(names: string[]) {
  return names.length > 0 ? names : ["未安排"];
}

function faceLabel(faceRegistered: boolean) {
  return faceRegistered ? "已录入" : "未录入";
}

function formatDateLabel(value: string) {
  return new Date(value).toLocaleDateString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  });
}

function filterVisibleEmployees(rows: PersonListItem[]) {
  return rows.filter((person) => {
    const name = person.name?.trim();
    if (!name || name === person.id) {
      return false;
    }
    const lower = name.toLowerCase();
    return !lower.startsWith("track_") && !lower.startsWith("unknown");
  });
}

export default function PersonsPage() {
  const [employeeSearch, setEmployeeSearch] = useState("");
  const [debouncedEmployeeSearch, setDebouncedEmployeeSearch] = useState("");
  const [externalSearch, setExternalSearch] = useState("");
  const [debouncedExternalSearch, setDebouncedExternalSearch] = useState("");
  const [page] = useState(1);
  const [pageSize] = useState(100);
  const [historyScheduleRows, setHistoryScheduleRows] = useState<ShiftScheduleRow[]>([]);
  const [scheduleHasMore, setScheduleHasMore] = useState(true);
  const [scheduleLoading, setScheduleLoading] = useState(false);
  const [scheduleOverrides, setScheduleOverrides] = useState<Record<string, ShiftScheduleRow>>({});

  const [isEmployeeCreateOpen, setIsEmployeeCreateOpen] = useState(false);
  const [isEmployeeEditOpen, setIsEmployeeEditOpen] = useState(false);
  const [isEmployeeDeleteOpen, setIsEmployeeDeleteOpen] = useState(false);
  const [isExternalCreateOpen, setIsExternalCreateOpen] = useState(false);
  const [isExternalEditOpen, setIsExternalEditOpen] = useState(false);
  const [isExternalDeleteOpen, setIsExternalDeleteOpen] = useState(false);
  const [isScheduleOpen, setIsScheduleOpen] = useState(false);

  const [employeeCreateForm, setEmployeeCreateForm] = useState<EmployeeFormState>(EMPTY_EMPLOYEE_FORM);
  const [employeeEditForm, setEmployeeEditForm] = useState<EmployeeFormState>(EMPTY_EMPLOYEE_FORM);
  const [externalCreateForm, setExternalCreateForm] = useState<ExternalFormState>(EMPTY_EXTERNAL_FORM);
  const [externalEditForm, setExternalEditForm] = useState<ExternalFormState>(EMPTY_EXTERNAL_FORM);

  const [selectedEmployee, setSelectedEmployee] = useState<PersonListItem | null>(null);
  const [selectedExternal, setSelectedExternal] = useState<ExternalPersonListItem | null>(null);
  const [employeeCreateFaceFile, setEmployeeCreateFaceFile] = useState<File | null>(null);
  const [employeeEditFaceFile, setEmployeeEditFaceFile] = useState<File | null>(null);
  const [externalCreateFaceFile, setExternalCreateFaceFile] = useState<File | null>(null);
  const [externalEditFaceFile, setExternalEditFaceFile] = useState<File | null>(null);

  const employeeSearchTimeoutRef = useRef<number | null>(null);
  const externalSearchTimeoutRef = useRef<number | null>(null);
  const scheduleScrollRef = useRef<HTMLDivElement | null>(null);

  const { data: employeeData, isLoading: employeeLoading, isError: employeeError } = usePersons({
    page,
    pageSize,
    search: debouncedEmployeeSearch,
  });
  const { data: externalData, isLoading: externalLoading, isError: externalError } = useExternalPersons({
    page,
    pageSize,
    search: debouncedExternalSearch,
  });
  const { data: todaySchedule, isLoading: todayScheduleLoading } = useTodaySchedule();
  const { data: supervisionEventsData = [] } = useSupervisionEvents();
  const { data: supervisionCameras = [] } = useSupervisionCameras();
  const { data: jobTitleOptions = [] } = useJobTitleOptions();

  const createPerson = useCreatePerson();
  const updatePerson = useUpdatePerson();
  const deletePerson = useDeletePerson();
  const uploadPersonFace = useUploadPersonFace();

  const createExternalPerson = useCreateExternalPerson();
  const updateExternalPerson = useUpdateExternalPerson();
  const deleteExternalPerson = useDeleteExternalPerson();
  const uploadExternalPersonFace = useUploadExternalPersonFace();

  const createNextSchedule = useCreateNextSchedule();
  const updateTodaySchedule = useUpdateTodaySchedule();

  const employees = useMemo(
    () => filterVisibleEmployees(employeeData?.persons ?? []),
    [employeeData?.persons]
  );
  const externals = useMemo(() => externalData?.persons ?? [], [externalData?.persons]);
  const supervisionEvents =
    supervisionEventsData.length > 0 ? supervisionEventsData : FALLBACK_SUPERVISION_EVENTS;

  const loadScheduleHistory = async (pageToLoad: number, reset: boolean = false) => {
    if (scheduleLoading) {
      return;
    }
    setScheduleLoading(true);
    try {
      const response = await api.getScheduleHistory({ page: pageToLoad, pageSize: 30 });
      setHistoryScheduleRows((current) => {
        const base = reset ? [] : current.slice();
        for (const item of response.items) {
          if (!base.some((row) => row.id === item.id)) {
            base.push(item);
          }
        }
        return base;
      });
      setScheduleHasMore(response.has_more);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载排班记录失败");
    } finally {
      setScheduleLoading(false);
    }
  };

  const personOptions = useMemo(
    () =>
      employees.map((person) => ({
        value: person.id,
        label: person.name?.trim() || person.id,
      })),
    [employees]
  );

  const todayShiftSummary = useMemo(() => {
    return {
      day: renderShiftNames(todaySchedule?.day_person_names ?? []),
      night: renderShiftNames(todaySchedule?.night_person_names ?? []),
    };
  }, [todaySchedule?.day_person_names, todaySchedule?.night_person_names]);

  const scheduleRows = useMemo(() => {
    const baseRows: ShiftScheduleRow[] = [];
    if (todaySchedule) {
      baseRows.push(todaySchedule);
    }
    for (const item of historyScheduleRows) {
      if (!baseRows.some((row) => row.shift_date === item.shift_date)) {
        baseRows.push(item);
      }
    }

    const mergedRows = baseRows.map((row) => scheduleOverrides[row.shift_date] ?? row);
    return mergedRows.sort((a, b) => b.shift_date.localeCompare(a.shift_date));
  }, [todaySchedule, historyScheduleRows, scheduleOverrides]);

  const onEmployeeSearchChange = (event: ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value;
    setEmployeeSearch(value);
    if (employeeSearchTimeoutRef.current !== null) {
      window.clearTimeout(employeeSearchTimeoutRef.current);
    }
    employeeSearchTimeoutRef.current = window.setTimeout(() => {
      setDebouncedEmployeeSearch(value);
    }, 250);
  };

  const onExternalSearchChange = (event: ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value;
    setExternalSearch(value);
    if (externalSearchTimeoutRef.current !== null) {
      window.clearTimeout(externalSearchTimeoutRef.current);
    }
    externalSearchTimeoutRef.current = window.setTimeout(() => {
      setDebouncedExternalSearch(value);
    }, 250);
  };

  const openEmployeeCreateDialog = () => {
    setEmployeeCreateForm(EMPTY_EMPLOYEE_FORM);
    setEmployeeCreateFaceFile(null);
    setIsEmployeeCreateOpen(true);
  };

  const openEmployeeEditDialog = (person: PersonListItem) => {
    setSelectedEmployee(person);
    setEmployeeEditForm({
      name: person.name ?? "",
      workshop: person.workshop ?? "",
      job_title: person.job_title ?? "",
      supervision_scope: person.supervision_scope ?? [],
    });
    setEmployeeEditFaceFile(null);
    setIsEmployeeEditOpen(true);
  };

  const openEmployeeDeleteDialog = (person: PersonListItem) => {
    setSelectedEmployee(person);
    setIsEmployeeDeleteOpen(true);
  };

  const openExternalCreateDialog = () => {
    setExternalCreateForm(EMPTY_EXTERNAL_FORM);
    setExternalCreateFaceFile(null);
    setIsExternalCreateOpen(true);
  };

  const openExternalEditDialog = (person: ExternalPersonListItem) => {
    setSelectedExternal(person);
    setExternalEditForm({
      name: person.name,
      organization: person.organization,
      supervision_scope: person.supervision_scope ?? [],
      allowed_camera_ids: person.allowed_camera_ids ?? [],
    });
    setExternalEditFaceFile(null);
    setIsExternalEditOpen(true);
  };

  const openExternalDeleteDialog = (person: ExternalPersonListItem) => {
    setSelectedExternal(person);
    setIsExternalDeleteOpen(true);
  };

  const openScheduleDialog = () => {
    setHistoryScheduleRows([]);
    setScheduleHasMore(true);
    setScheduleOverrides({});
    setIsScheduleOpen(true);
    void loadScheduleHistory(1, true);
  };

  const handleEmployeeCreate = async () => {
    const payload = normalizeEmployeePayload(employeeCreateForm);
    if (!payload.name) {
      toast.error("请输入姓名");
      return;
    }

    try {
      const created = await createPerson.mutateAsync(payload);
      if (employeeCreateFaceFile) {
        await uploadPersonFace.mutateAsync({ personId: created.id, file: employeeCreateFaceFile });
      }
      toast.success("员工已新增");
      setIsEmployeeCreateOpen(false);
      setEmployeeCreateForm(EMPTY_EMPLOYEE_FORM);
      setEmployeeCreateFaceFile(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "新增员工失败");
    }
  };

  const handleEmployeeEdit = async () => {
    if (!selectedEmployee) {
      return;
    }
    const payload = normalizeEmployeePayload(employeeEditForm);
    if (!payload.name) {
      toast.error("请输入姓名");
      return;
    }

    try {
      await updatePerson.mutateAsync({
        personId: selectedEmployee.id,
        data: payload,
      });
      if (employeeEditFaceFile) {
        await uploadPersonFace.mutateAsync({ personId: selectedEmployee.id, file: employeeEditFaceFile });
      }
      toast.success("员工信息已更新");
      setIsEmployeeEditOpen(false);
      setSelectedEmployee(null);
      setEmployeeEditFaceFile(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新员工失败");
    }
  };

  const handleEmployeeDelete = async () => {
    if (!selectedEmployee) {
      return;
    }

    try {
      await deletePerson.mutateAsync(selectedEmployee.id);
      toast.success("员工已删除");
      setIsEmployeeDeleteOpen(false);
      setSelectedEmployee(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除员工失败");
    }
  };

  const handleExternalCreate = async () => {
    const payload = normalizeExternalPayload(externalCreateForm);
    if (!payload.name || !payload.organization) {
      toast.error("请填写姓名和单位/部门");
      return;
    }
    if (payload.supervision_scope.includes("unauthorized_intrusion") && payload.allowed_camera_ids.length === 0) {
      toast.error("选择违规闯入时必须设置允许出现的监控画面");
      return;
    }

    try {
      const created = await createExternalPerson.mutateAsync(payload);
      if (externalCreateFaceFile) {
        await uploadExternalPersonFace.mutateAsync({ personId: created.id, file: externalCreateFaceFile });
      }
      toast.success("外来人员已新增");
      setIsExternalCreateOpen(false);
      setExternalCreateForm(EMPTY_EXTERNAL_FORM);
      setExternalCreateFaceFile(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "新增外来人员失败");
    }
  };

  const handleExternalEdit = async () => {
    if (!selectedExternal) {
      return;
    }
    const payload = normalizeExternalPayload(externalEditForm);
    if (!payload.name || !payload.organization) {
      toast.error("请填写姓名和单位/部门");
      return;
    }
    if (payload.supervision_scope.includes("unauthorized_intrusion") && payload.allowed_camera_ids.length === 0) {
      toast.error("选择违规闯入时必须设置允许出现的监控画面");
      return;
    }

    try {
      await updateExternalPerson.mutateAsync({
        personId: selectedExternal.id,
        data: payload,
      });
      if (externalEditFaceFile) {
        await uploadExternalPersonFace.mutateAsync({ personId: selectedExternal.id, file: externalEditFaceFile });
      }
      toast.success("外来人员信息已更新");
      setIsExternalEditOpen(false);
      setSelectedExternal(null);
      setExternalEditFaceFile(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新外来人员失败");
    }
  };

  const handleExternalDelete = async () => {
    if (!selectedExternal) {
      return;
    }

    try {
      await deleteExternalPerson.mutateAsync(selectedExternal.id);
      toast.success("外来人员已删除");
      setIsExternalDeleteOpen(false);
      setSelectedExternal(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除外来人员失败");
    }
  };

  const handleScheduleChange = async (
    row: ShiftScheduleRow,
    field: "day_person_ids" | "night_person_ids",
    values: string[]
  ) => {
    try {
      await updateTodaySchedule.mutateAsync({
        shift_date: row.shift_date,
        day_person_ids: field === "day_person_ids" ? values : row.day_person_ids,
        night_person_ids: field === "night_person_ids" ? values : row.night_person_ids,
      });
      setScheduleOverrides((current) => ({
        ...current,
        [row.shift_date]: {
          ...row,
          [field]: values,
          [`${field === "day_person_ids" ? "day" : "night"}_person_names`]:
            personOptions
              .filter((option) => values.includes(option.value))
              .map((option) => option.label),
        },
      }));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新排班失败");
    }
  };

  const handleCreateNextSchedule = async () => {
    try {
      await createNextSchedule.mutateAsync({
        base_shift_date: scheduleRows[0]?.shift_date,
      });
      toast.success("已新增下一日排班");
      setHistoryScheduleRows([]);
      setScheduleHasMore(true);
      void loadScheduleHistory(1, true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "新增排班失败");
    }
  };

  const handleScheduleScroll = () => {
    const container = scheduleScrollRef.current;
    if (!container || scheduleLoading || !scheduleHasMore) {
      return;
    }
    const remaining = container.scrollHeight - container.scrollTop - container.clientHeight;
    if (remaining < 32) {
      const nextPage = Math.floor(historyScheduleRows.length / 30) + 1;
      void loadScheduleHistory(nextPage, false);
    }
  };

  if (employeeLoading || externalLoading || todayScheduleLoading) {
    return <PageLoader />;
  }

  if (employeeError || externalError) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center text-sm text-muted-foreground">
        人员信息加载失败，请刷新后重试
      </div>
    );
  }

  return (
    <MotionWrapper className="space-y-6">
      <motion.section
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="space-y-6"
      >
        <Card variant="glass" className="gap-4">
          <CardContent className="flex flex-col gap-6 md:flex-row md:items-start md:justify-between">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:gap-10">
              <div className="flex items-center gap-2 text-lg font-semibold text-foreground">
                <CalendarDays className="h-5 w-5 text-primary" />
                <span>今日排班</span>
              </div>
              <div className="flex flex-col gap-2 text-sm text-foreground md:flex-row md:gap-8 md:text-base">
                <span className="flex flex-wrap items-center gap-2">
                  <span>白班：</span>
                  {todayShiftSummary.day.map((name) => (
                    <span
                      key={`day-${name}`}
                      className="rounded-md border border-emerald-500/60 bg-emerald-500/10 px-2.5 py-1 text-emerald-600"
                    >
                      {name}
                    </span>
                  ))}
                </span>
                <span className="flex flex-wrap items-center gap-2">
                  <span>夜班：</span>
                  {todayShiftSummary.night.map((name) => (
                    <span
                      key={`night-${name}`}
                      className="rounded-md border border-emerald-500/60 bg-emerald-500/10 px-2.5 py-1 text-emerald-600"
                    >
                      {name}
                    </span>
                  ))}
                </span>
              </div>
            </div>
            <Button type="button" onClick={openScheduleDialog} className="shrink-0">
              排班表
            </Button>
          </CardContent>
        </Card>

        <PersonnelSectionCard
          icon={Users}
          title="员工信息"
          search={employeeSearch}
          onSearchChange={onEmployeeSearchChange}
          onCreate={openEmployeeCreateDialog}
          createLabel="新增员工"
        >
          <div className="rounded-lg border border-border/60">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-center">姓名</TableHead>
                  <TableHead className="text-center">车间</TableHead>
                  <TableHead className="text-center">岗位</TableHead>
                  <TableHead className="text-center">监管事件</TableHead>
                  <TableHead className="text-center">面容</TableHead>
                  <TableHead className="text-center">当日违规数</TableHead>
                  <TableHead className="text-center">7日违规数</TableHead>
                  <TableHead className="text-center">30日违规数</TableHead>
                  <TableHead className="min-w-[200px] text-center">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {employees.length > 0 ? (
                  employees.map((person) => (
                    <TableRow key={person.id}>
                      <TableCell className="text-center">{person.name || "-"}</TableCell>
                      <TableCell className="text-center">{person.workshop || "-"}</TableCell>
                      <TableCell className="text-center">{person.job_title || "-"}</TableCell>
                      <TableCell className="max-w-64 text-center">
                        <ScopeBadges labels={person.supervision_scope_labels} />
                      </TableCell>
                      <TableCell className="text-center">
                        <FaceCell registered={person.face_registered} imageUrl={person.face_image_url} />
                      </TableCell>
                      <TableCell className="text-center">{person.today_violation_count}</TableCell>
                      <TableCell className="text-center">{person.seven_day_violation_count}</TableCell>
                      <TableCell className="text-center">{person.thirty_day_violation_count}</TableCell>
                      <TableCell>
                        <ActionButtons
                          onEdit={() => openEmployeeEditDialog(person)}
                          onDelete={() => openEmployeeDeleteDialog(person)}
                        />
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <EmptyTableRow colSpan={9} message="暂无员工信息" />
                )}
              </TableBody>
            </Table>
          </div>
        </PersonnelSectionCard>

        <PersonnelSectionCard
          icon={UserRound}
          title="外来人员信息"
          search={externalSearch}
          onSearchChange={onExternalSearchChange}
          onCreate={openExternalCreateDialog}
          createLabel="新增外来人员"
        >
          <div className="rounded-lg border border-border/60">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-center">姓名</TableHead>
                  <TableHead className="text-center">单位/部门</TableHead>
                  <TableHead className="text-center">监管事件</TableHead>
                  <TableHead className="text-center">面容</TableHead>
                  <TableHead className="min-w-[200px] text-center">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {externals.length > 0 ? (
                  externals.map((person) => (
                    <TableRow key={person.id}>
                      <TableCell className="text-center">{person.name}</TableCell>
                      <TableCell className="text-center">{person.organization}</TableCell>
                      <TableCell className="max-w-64 text-center">
                        <ScopeBadges labels={person.supervision_scope_labels} />
                      </TableCell>
                      <TableCell className="text-center">
                        <FaceCell registered={person.face_registered} imageUrl={person.face_image_url} />
                      </TableCell>
                      <TableCell>
                        <ActionButtons
                          onEdit={() => openExternalEditDialog(person)}
                          onDelete={() => openExternalDeleteDialog(person)}
                        />
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <EmptyTableRow colSpan={5} message="暂无外来人员信息" />
                )}
              </TableBody>
            </Table>
          </div>
        </PersonnelSectionCard>
      </motion.section>

      <Dialog open={isScheduleOpen} onOpenChange={setIsScheduleOpen}>
        <DialogContent className="min-h-[40vh] min-w-[40vw] max-w-6xl">
          <DialogHeader className="flex flex-row items-center justify-between space-y-0">
            <div className="space-y-1.5">
              <DialogTitle>排班表</DialogTitle>
              <DialogDescription>展示最近排班记录，默认加载最新 30 条，滚动到底部继续加载</DialogDescription>
            </div>
            <Button type="button" onClick={handleCreateNextSchedule} disabled={createNextSchedule.isPending}>
              <Plus className="h-4 w-4" />
              新增排班
            </Button>
          </DialogHeader>
          <div
            ref={scheduleScrollRef}
            onScroll={handleScheduleScroll}
            className="max-h-[70vh] overflow-y-auto rounded-lg border border-border/60"
          >
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-center">日期</TableHead>
                  <TableHead className="text-center">白班</TableHead>
                  <TableHead className="text-center">夜班</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {scheduleRows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="text-center">{formatDateLabel(row.shift_date)}</TableCell>
                    <TableCell className="text-center">
                      <MultiSelectPopover
                        value={row.day_person_ids}
                        options={personOptions}
                        placeholder="未安排"
                        onChange={(values) => handleScheduleChange(row, "day_person_ids", values)}
                      />
                    </TableCell>
                    <TableCell className="text-center">
                      <MultiSelectPopover
                        value={row.night_person_ids}
                        options={personOptions}
                        placeholder="未安排"
                        onChange={(values) => handleScheduleChange(row, "night_person_ids", values)}
                      />
                    </TableCell>
                  </TableRow>
                ))}
                {scheduleRows.length === 0 ? <EmptyTableRow colSpan={3} message="暂无排班记录" /> : null}
              </TableBody>
            </Table>
            {scheduleLoading ? (
              <div className="p-4 text-center text-sm text-muted-foreground">加载中...</div>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setIsScheduleOpen(false)}>
              关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isEmployeeCreateOpen} onOpenChange={setIsEmployeeCreateOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>新增员工</DialogTitle>
            <DialogDescription>员工信息只能通过这里新增</DialogDescription>
          </DialogHeader>
          <EmployeeForm
            form={employeeCreateForm}
            onChange={setEmployeeCreateForm}
            faceFile={employeeCreateFaceFile}
            onFaceChange={setEmployeeCreateFaceFile}
            faceRegistered={false}
            options={supervisionEvents}
            jobTitleOptions={jobTitleOptions}
          />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setIsEmployeeCreateOpen(false)}>
              取消
            </Button>
            <Button type="button" onClick={handleEmployeeCreate} disabled={createPerson.isPending}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isEmployeeEditOpen} onOpenChange={setIsEmployeeEditOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>编辑员工</DialogTitle>
            <DialogDescription>修改员工基础资料</DialogDescription>
          </DialogHeader>
          <EmployeeForm
            form={employeeEditForm}
            onChange={setEmployeeEditForm}
            faceFile={employeeEditFaceFile}
            onFaceChange={setEmployeeEditFaceFile}
            faceRegistered={!!selectedEmployee?.face_registered}
            options={supervisionEvents}
            jobTitleOptions={jobTitleOptions}
          />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setIsEmployeeEditOpen(false)}>
              取消
            </Button>
            <Button type="button" onClick={handleEmployeeEdit} disabled={updatePerson.isPending}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isEmployeeDeleteOpen} onOpenChange={setIsEmployeeDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除员工</DialogTitle>
            <DialogDescription>
              确认删除 {selectedEmployee?.name || selectedEmployee?.id || "该员工"} 吗？
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setIsEmployeeDeleteOpen(false)}>
              取消
            </Button>
            <Button type="button" variant="destructive" onClick={handleEmployeeDelete} disabled={deletePerson.isPending}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isExternalCreateOpen} onOpenChange={setIsExternalCreateOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>新增外来人员</DialogTitle>
            <DialogDescription>维护长期外来人员信息与人脸库</DialogDescription>
          </DialogHeader>
          <ExternalPersonForm
            form={externalCreateForm}
            onChange={setExternalCreateForm}
            faceFile={externalCreateFaceFile}
            onFaceChange={setExternalCreateFaceFile}
            faceRegistered={false}
            options={supervisionEvents}
            cameras={supervisionCameras}
          />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setIsExternalCreateOpen(false)}>
              取消
            </Button>
            <Button type="button" onClick={handleExternalCreate} disabled={createExternalPerson.isPending}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isExternalEditOpen} onOpenChange={setIsExternalEditOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>编辑外来人员</DialogTitle>
            <DialogDescription>修改外来人员基础资料</DialogDescription>
          </DialogHeader>
          <ExternalPersonForm
            form={externalEditForm}
            onChange={setExternalEditForm}
            faceFile={externalEditFaceFile}
            onFaceChange={setExternalEditFaceFile}
            faceRegistered={!!selectedExternal?.face_registered}
            options={supervisionEvents}
            cameras={supervisionCameras}
          />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setIsExternalEditOpen(false)}>
              取消
            </Button>
            <Button type="button" onClick={handleExternalEdit} disabled={updateExternalPerson.isPending}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isExternalDeleteOpen} onOpenChange={setIsExternalDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除外来人员</DialogTitle>
            <DialogDescription>
              确认删除 {selectedExternal?.name || selectedExternal?.id || "该外来人员"} 吗？
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setIsExternalDeleteOpen(false)}>
              取消
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={handleExternalDelete}
              disabled={deleteExternalPerson.isPending}
            >
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </MotionWrapper>
  );
}

function PersonnelSectionCard({
  icon: Icon,
  title,
  search,
  onSearchChange,
  onCreate,
  createLabel,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  search: string;
  onSearchChange: (event: ChangeEvent<HTMLInputElement>) => void;
  onCreate: () => void;
  createLabel: string;
  children: React.ReactNode;
}) {
  return (
    <Card variant="glass" className="gap-4">
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-2 text-lg font-semibold text-foreground">
            <Icon className="h-5 w-5 text-primary" />
            <span>{title}</span>
          </div>
          <div className="flex w-full flex-col gap-3 md:w-auto md:flex-row">
            <div className="relative w-full md:w-72">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={search} onChange={onSearchChange} placeholder="搜索姓名" className="pl-9" />
            </div>
            <Button type="button" onClick={onCreate}>
              <Plus className="h-4 w-4" />
              {createLabel}
            </Button>
          </div>
        </div>
        {children}
      </CardContent>
    </Card>
  );
}

function ScopeBadges({ labels }: { labels: string[] }) {
  return (
    <div className="flex flex-wrap items-center justify-center gap-2">
      {labels.length > 0 ? (
        labels.map((label) => (
          <span
            key={label}
            className="rounded-md border border-red-500/60 bg-red-500/10 px-2 py-1 text-xs text-red-600"
          >
            {label}
          </span>
        ))
      ) : (
        <span className="text-sm text-muted-foreground">未设置</span>
      )}
    </div>
  );
}

function FaceCell({
  registered,
  imageUrl,
}: {
  registered: boolean;
  imageUrl?: string | null;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2">
      {imageUrl ? (
        <img
          src={resolveApiAssetUrl(imageUrl) || undefined}
          alt="face"
          className="h-12 w-12 rounded-lg border border-border/60 object-cover"
        />
      ) : (
        <div className="flex h-12 w-12 items-center justify-center rounded-lg border border-dashed border-border/60 text-[11px] text-muted-foreground">
          无图
        </div>
      )}
      <span className="text-xs text-muted-foreground">{faceLabel(registered)}</span>
    </div>
  );
}

function ActionButtons({
  onEdit,
  onDelete,
}: {
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-center gap-2">
      <Button type="button" size="sm" variant="outline" onClick={onEdit}>
        <Edit3 className="h-4 w-4" />
        编辑
      </Button>
      <Button type="button" size="sm" variant="destructive" onClick={onDelete}>
        <Trash2 className="h-4 w-4" />
        删除
      </Button>
    </div>
  );
}

function EmptyTableRow({
  colSpan,
  message,
}: {
  colSpan: number;
  message: string;
}) {
  return (
    <TableRow>
      <TableCell colSpan={colSpan} className="h-28 text-center text-muted-foreground">
        {message}
      </TableCell>
    </TableRow>
  );
}

function EmployeeForm({
  form,
  onChange,
  faceFile,
  onFaceChange,
  faceRegistered,
  options,
  jobTitleOptions,
}: {
  form: EmployeeFormState;
  onChange: (next: EmployeeFormState) => void;
  faceFile: File | null;
  onFaceChange: (file: File | null) => void;
  faceRegistered: boolean;
  options: SupervisionEventOption[];
  jobTitleOptions: JobTitleOption[];
}) {
  return (
    <div className="grid gap-4">
      <div className="space-y-2">
        <Label htmlFor="person_name">姓名</Label>
        <Input
          id="person_name"
          value={form.name}
          onChange={(event) => onChange({ ...form, name: event.target.value })}
          placeholder="请输入姓名"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="person_workshop">车间</Label>
        <Input
          id="person_workshop"
          value={form.workshop}
          onChange={(event) => onChange({ ...form, workshop: event.target.value })}
          placeholder="请输入车间"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="person_job_title">岗位</Label>
        <Select
          value={form.job_title || undefined}
          onValueChange={(value) => onChange({ ...form, job_title: value })}
        >
          <SelectTrigger id="person_job_title">
            <SelectValue placeholder="请选择岗位" />
          </SelectTrigger>
          <SelectContent>
            {jobTitleOptions.map((option) => (
              <SelectItem key={option.id} value={option.name}>
                {option.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <ScopeEditor
        selectedValues={form.supervision_scope}
        options={options}
        onChange={(values) => onChange({ ...form, supervision_scope: values })}
      />
      <FaceUploader
        inputId="person_face"
        faceFile={faceFile}
        onFaceChange={onFaceChange}
        faceRegistered={faceRegistered}
      />
    </div>
  );
}

function ExternalPersonForm({
  form,
  onChange,
  faceFile,
  onFaceChange,
  faceRegistered,
  options,
  cameras,
}: {
  form: ExternalFormState;
  onChange: (next: ExternalFormState) => void;
  faceFile: File | null;
  onFaceChange: (file: File | null) => void;
  faceRegistered: boolean;
  options: SupervisionEventOption[];
  cameras: SupervisionCamera[];
}) {
  return (
    <div className="grid gap-4">
      <div className="space-y-2">
        <Label htmlFor="external_name">姓名</Label>
        <Input
          id="external_name"
          value={form.name}
          onChange={(event) => onChange({ ...form, name: event.target.value })}
          placeholder="请输入姓名"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="external_organization">单位/部门</Label>
        <Input
          id="external_organization"
          value={form.organization}
          onChange={(event) => onChange({ ...form, organization: event.target.value })}
          placeholder="请输入单位或部门"
        />
      </div>
      <ScopeEditor
        selectedValues={form.supervision_scope}
        options={options}
        onChange={(values) =>
          onChange({
            ...form,
            supervision_scope: values,
            allowed_camera_ids: values.includes("unauthorized_intrusion") ? form.allowed_camera_ids : [],
          })
        }
      />
      {form.supervision_scope.includes("unauthorized_intrusion") ? (
        <CameraSelector
          selectedCameraIds={form.allowed_camera_ids}
          cameras={cameras}
          onChange={(values) => onChange({ ...form, allowed_camera_ids: values })}
        />
      ) : null}
      <FaceUploader
        inputId="external_face"
        faceFile={faceFile}
        onFaceChange={onFaceChange}
        faceRegistered={faceRegistered}
      />
    </div>
  );
}

function CameraSelector({
  selectedCameraIds,
  cameras,
  onChange,
}: {
  selectedCameraIds: string[];
  cameras: SupervisionCamera[];
  onChange: (values: string[]) => void;
}) {
  const selectedCameras = cameras.filter((item) => selectedCameraIds.includes(item.id));
  const availableCameras = cameras.filter((item) => !selectedCameraIds.includes(item.id));

  return (
    <div className="space-y-3">
      <div className="text-sm font-medium text-foreground">允许出现的监控画面</div>
      <div className="flex min-h-16 flex-wrap gap-2 rounded-lg border border-border/60 p-3">
        {selectedCameras.length > 0 ? (
          selectedCameras.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onChange(selectedCameraIds.filter((cameraId) => cameraId !== item.id))}
              className="rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground transition-colors hover:bg-muted"
            >
              {item.name}
            </button>
          ))
        ) : (
          <span className="text-sm text-muted-foreground">未设置</span>
        )}
      </div>
      <div className="flex min-h-16 flex-wrap gap-2 rounded-lg border border-border/60 p-3">
        {availableCameras.length > 0 ? (
          availableCameras.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onChange([...selectedCameraIds, item.id])}
              className="rounded-md border border-dashed border-border bg-background px-3 py-1.5 text-sm text-foreground transition-colors hover:bg-muted"
            >
              + {item.name}{item.enabled ? "" : "（已停用）"}
            </button>
          ))
        ) : (
          <span className="text-sm text-muted-foreground">暂无可选监控画面</span>
        )}
      </div>
    </div>
  );
}

function ScopeEditor({
  selectedValues,
  options,
  onChange,
}: {
  selectedValues: string[];
  options: SupervisionEventOption[];
  onChange: (values: string[]) => void;
}) {
  const currentOptions = options.filter((item) => selectedValues.includes(item.key));
  const availableOptions = options.filter((item) => !selectedValues.includes(item.key));

  return (
    <>
      <div className="space-y-3">
        <div className="text-sm font-medium text-foreground">当前监管事件</div>
        <div className="flex min-h-16 flex-wrap gap-2 rounded-lg border border-border/60 p-3">
          {currentOptions.length > 0 ? (
            currentOptions.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => onChange(selectedValues.filter((value) => value !== item.key))}
                className="rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground transition-colors hover:bg-muted"
              >
                {item.label}
              </button>
            ))
          ) : (
            <span className="text-sm text-muted-foreground">未设置</span>
          )}
        </div>
      </div>
      <div className="space-y-3">
        <div className="text-sm font-medium text-foreground">新增监管事件</div>
        <div className="flex min-h-16 flex-wrap gap-2 rounded-lg border border-border/60 p-3">
          {availableOptions.length > 0 ? (
            availableOptions.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => onChange([...selectedValues, item.key])}
                className="rounded-md border border-dashed border-border bg-background px-3 py-1.5 text-sm text-foreground transition-colors hover:bg-muted"
              >
                + {item.label}
              </button>
            ))
          ) : (
            <span className="text-sm text-muted-foreground">无可新增事件</span>
          )}
        </div>
      </div>
    </>
  );
}

function FaceUploader({
  inputId,
  faceFile,
  onFaceChange,
  faceRegistered,
}: {
  inputId: string;
  faceFile: File | null;
  onFaceChange: (file: File | null) => void;
  faceRegistered: boolean;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={inputId}>面容</Label>
      <Input
        id={inputId}
        type="file"
        accept="image/*"
        onChange={(event) => onFaceChange(event.target.files?.[0] ?? null)}
      />
      <div className="text-xs text-muted-foreground">
        {faceFile
          ? `已选择：${faceFile.name}`
          : faceRegistered
            ? "当前状态：已录入，可重新选择文件进行修改"
            : "当前状态：未录入"}
      </div>
    </div>
  );
}

function MultiSelectPopover({
  value,
  options,
  placeholder,
  onChange,
}: {
  value: string[];
  options: Array<{ value: string; label: string }>;
  placeholder: string;
  onChange: (values: string[]) => void;
}) {
  const [open, setOpen] = useState(false);

  const selectedLabels = options
    .filter((option) => value.includes(option.value))
    .map((option) => option.label);

  return (
    <div className="relative">
      <Button
        type="button"
        variant="outline"
        className="h-auto min-h-10 w-full justify-between py-2 text-left"
        onClick={() => setOpen((current) => !current)}
      >
        <span className="truncate">
          {selectedLabels.length > 0 ? selectedLabels.join("、") : placeholder}
        </span>
        <ChevronsUpDown className="h-4 w-4 opacity-60" />
      </Button>

      {open ? (
        <div className="absolute z-50 mt-2 max-h-64 w-full overflow-y-auto rounded-lg border border-border bg-popover p-1 shadow-lg">
          {options.map((option) => {
            const checked = value.includes(option.value);
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => {
                  if (checked) {
                    onChange(value.filter((item) => item !== option.value));
                  } else {
                    onChange([...value, option.value]);
                  }
                }}
                className={cn(
                  "flex w-full items-center justify-between rounded-md px-3 py-2 text-sm hover:bg-accent",
                  checked && "bg-accent/60"
                )}
              >
                <span>{option.label}</span>
                {checked ? <Check className="h-4 w-4" /> : null}
              </button>
            );
          })}
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="mt-1 w-full rounded-md border border-border px-3 py-2 text-sm"
          >
            完成
          </button>
        </div>
      ) : null}
    </div>
  );
}
