"use client";

import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Edit3, History, Plus, ShieldCheck, Trash2, UserRoundPlus, Users } from "lucide-react";
import { toast } from "sonner";

import { PageLoader } from "@/components/page-loader";
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  useExternalPersons,
  useActiveExternalPersonnel,
  useActiveVisitors,
  useCreateExternalPersonnelRegistration,
  useCreateVisitorRegistration,
  useDeleteExternalPersonnelRegistration,
  useDeleteVisitorRegistration,
  useExternalPersonnelHistory,
  useSupervisionCameras,
  useSupervisionEventOptions,
  useSupervisionEvents,
  useSystemSupervisionSettings,
  useUpdateExternalPersonnelRegistration,
  useUpdateSystemSupervisionSettings,
  useUpdateVisitorRegistration,
  useVisitorHistory,
} from "@/lib/queries";
import type {
  ExternalPersonListItem,
  ExternalPersonnelRegistration,
  SystemSupervisionSettings,
  VisitorRegistration,
} from "@/lib/api";

type VisitorFormState = {
  start_date: string;
  start_clock: string;
  end_date: string;
  end_clock: string;
  visiting_company: string;
  total_people: string;
};

type ExternalFormState = {
  external_person_id: string;
  name: string;
  organization: string;
  start_date: string;
  start_clock: string;
  end_date: string;
  end_clock: string;
  visit_reason: string;
  supervision_events: string[];
  allowed_camera_ids: string[];
};

type SystemSettingsFormState = {
  other_person_scope: string[];
  area_missed_inspection_enabled: boolean;
  area_missed_inspection_interval_hours: string;
  area_missed_inspection_start_time: string;
  area_missed_inspection_camera_ids: string[];
  blind_spot_stay_enabled: boolean;
  blind_spot_stay_threshold_seconds: string;
  workshop_overcapacity_enabled: boolean;
  workshop_overcapacity_limit: string;
  alert_cooldown_minutes: string;
};

const EMPTY_VISITOR_FORM: VisitorFormState = {
  start_date: "",
  start_clock: "",
  end_date: "",
  end_clock: "",
  visiting_company: "",
  total_people: "",
};

const EMPTY_EXTERNAL_FORM: ExternalFormState = {
  external_person_id: "",
  name: "",
  organization: "",
  start_date: "",
  start_clock: "",
  end_date: "",
  end_clock: "",
  visit_reason: "",
  supervision_events: [],
  allowed_camera_ids: [],
};

const EMPTY_SYSTEM_SETTINGS_FORM: SystemSettingsFormState = {
  other_person_scope: [],
  area_missed_inspection_enabled: false,
  area_missed_inspection_interval_hours: "",
  area_missed_inspection_start_time: "",
  area_missed_inspection_camera_ids: [],
  blind_spot_stay_enabled: false,
  blind_spot_stay_threshold_seconds: "",
  workshop_overcapacity_enabled: false,
  workshop_overcapacity_limit: "",
  alert_cooldown_minutes: "5",
};

const SUPERVISION_FLOORS = ["一楼", "二楼", "三楼", "四楼"] as const;

function pad(value: number) {
  return String(value).padStart(2, "0");
}

function toDateParts(value?: string | null) {
  if (!value) {
    return { date: "", time: "" };
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return { date: "", time: "" };
  }
  return {
    date: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    time: `${pad(date.getHours())}:${pad(date.getMinutes())}`,
  };
}

function fromDateAndTime(datePart: string, timePart: string) {
  return new Date(`${datePart}T${timePart}`);
}

function normalizeDateTimePayload(datePart: string, timePart: string) {
  return fromDateAndTime(datePart, timePart).toISOString();
}

function isValidDatePart(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value);
}

function isValidTimePart(value: string) {
  return /^\d{2}:\d{2}$/.test(value);
}

function formatDateTime(value: string) {
  const parts = toDateParts(value);
  return `${parts.date} ${parts.time}`;
}

function stripFloorPrefix(name: string, floor?: string | null) {
  const normalizedName = name.trim();
  const normalizedFloor = (floor ?? "").trim();
  if (!normalizedFloor || !normalizedName.startsWith(normalizedFloor)) {
    return normalizedName;
  }
  const stripped = normalizedName.slice(normalizedFloor.length).trim();
  return stripped || normalizedName;
}

function normalizeVisitorPayload(form: VisitorFormState) {
  return {
    start_time: normalizeDateTimePayload(form.start_date, form.start_clock),
    end_time: normalizeDateTimePayload(form.end_date, form.end_clock),
    visiting_company: form.visiting_company.trim(),
    total_people: Number(form.total_people),
  };
}

function normalizeExternalPayload(form: ExternalFormState) {
  return {
    external_person_id: form.external_person_id || null,
    name: form.name.trim(),
    organization: form.organization.trim(),
    start_time: normalizeDateTimePayload(form.start_date, form.start_clock),
    end_time: normalizeDateTimePayload(form.end_date, form.end_clock),
    visit_reason: form.visit_reason.trim(),
    supervision_events: form.supervision_events,
    allowed_camera_ids: form.allowed_camera_ids,
  };
}

function validateVisitorForm(form: VisitorFormState) {
  if (
    !form.start_date ||
    !form.start_clock ||
    !form.end_date ||
    !form.end_clock ||
    !form.visiting_company.trim() ||
    !form.total_people.trim()
  ) {
    return "请补全访客登记信息";
  }
  if (Number(form.total_people) <= 0) {
    return "人数必须大于 0";
  }
  if (!isValidDatePart(form.start_date) || !isValidDatePart(form.end_date)) {
    return "日期格式必须为 YYYY-MM-DD";
  }
  if (!isValidTimePart(form.start_clock) || !isValidTimePart(form.end_clock)) {
    return "时间格式必须为 HH:mm";
  }
  return null;
}

function validateExternalForm(form: ExternalFormState) {
  if (
    !form.external_person_id ||
    !form.name.trim() ||
    !form.organization.trim() ||
    !form.start_date ||
    !form.start_clock ||
    !form.end_date ||
    !form.end_clock ||
    !form.visit_reason.trim()
  ) {
    return "请补全外来人员登记信息";
  }
  if (form.supervision_events.length === 0) {
    return "请至少选择一个监管事件";
  }
  if (form.supervision_events.includes("unauthorized_intrusion") && form.allowed_camera_ids.length === 0) {
    return "选择违规闯入时必须设置允许出现的监控画面";
  }
  if (!isValidDatePart(form.start_date) || !isValidDatePart(form.end_date)) {
    return "日期格式必须为 YYYY-MM-DD";
  }
  if (!isValidTimePart(form.start_clock) || !isValidTimePart(form.end_clock)) {
    return "时间格式必须为 HH:mm";
  }
  return null;
}

function validateExternalAppointmentForm(
  form: ExternalFormState,
  selectedPerson: ExternalPersonListItem | null,
) {
  if (
    !form.external_person_id ||
    !form.name.trim() ||
    !form.organization.trim() ||
    !form.start_date ||
    !form.start_clock ||
    !form.end_date ||
    !form.end_clock ||
    !form.visit_reason.trim()
  ) {
    return "请补全外来人员登记信息";
  }
  if (!selectedPerson) {
    return "请选择外来人员";
  }
  if (!selectedPerson.face_registered) {
    return "所选外来人员档案尚未录入面容，请先到人员管理页面录入";
  }
  if (form.supervision_events.length === 0) {
    return "请至少选择一个监管事件";
  }
  if (form.supervision_events.includes("unauthorized_intrusion") && form.allowed_camera_ids.length === 0) {
    return "选择违规闯入时必须设置允许出现的监控画面";
  }
  if (!isValidDatePart(form.start_date) || !isValidDatePart(form.end_date)) {
    return "日期格式必须为 YYYY-MM-DD";
  }
  if (!isValidTimePart(form.start_clock) || !isValidTimePart(form.end_clock)) {
    return "时间格式必须为 HH:mm";
  }
  return null;
}

function mapSystemSettingsToForm(settings?: SystemSupervisionSettings | null): SystemSettingsFormState {
  if (!settings) {
    return EMPTY_SYSTEM_SETTINGS_FORM;
  }
  return {
    other_person_scope: settings.other_person_scope ?? [],
    area_missed_inspection_enabled: settings.area_missed_inspection_enabled,
    area_missed_inspection_interval_hours:
      settings.area_missed_inspection_interval_hours != null
        ? String(settings.area_missed_inspection_interval_hours)
        : "",
    area_missed_inspection_start_time: settings.area_missed_inspection_start_time ?? "",
    area_missed_inspection_camera_ids: settings.area_missed_inspection_camera_ids ?? [],
    blind_spot_stay_enabled: false,
    blind_spot_stay_threshold_seconds: "",
    workshop_overcapacity_enabled: settings.workshop_overcapacity_enabled,
    workshop_overcapacity_limit:
      settings.workshop_overcapacity_limit != null
        ? String(settings.workshop_overcapacity_limit)
        : "",
    alert_cooldown_minutes:
      settings.alert_cooldown_seconds != null
        ? String(settings.alert_cooldown_seconds / 60)
        : "5",
  };
}

function validateSystemSettingsForm(form: SystemSettingsFormState) {
  if (form.area_missed_inspection_enabled) {
    const interval = Number(form.area_missed_inspection_interval_hours);
    if (!interval || interval <= 0) {
      return "区域漏巡检查周期必须大于 0";
    }
    if (!form.area_missed_inspection_start_time.trim()) {
      return "区域漏巡开始时间不能为空";
    }
    if (!isValidTimePart(form.area_missed_inspection_start_time.trim())) {
      return "区域漏巡开始时间格式必须为 HH:mm";
    }
  }
  if (form.workshop_overcapacity_enabled) {
    const limit = Number(form.workshop_overcapacity_limit);
    if (Number.isNaN(limit) || limit < 0) {
      return "车间超员人数阈值不能小于 0";
    }
  }
  const alertCooldownMinutes = Number(form.alert_cooldown_minutes);
  if (!alertCooldownMinutes || alertCooldownMinutes <= 0) {
    return "告警去重时间必须大于 0";
  }
  return null;
}

function buildSystemSettingsPayload(
  form: SystemSettingsFormState,
): Omit<SystemSupervisionSettings, "id"> {
  return {
    other_person_scope: form.other_person_scope,
    area_missed_inspection_enabled: form.area_missed_inspection_enabled,
    area_missed_inspection_interval_hours: form.area_missed_inspection_enabled
      ? Number(form.area_missed_inspection_interval_hours)
      : null,
    area_missed_inspection_start_time: form.area_missed_inspection_enabled
      ? form.area_missed_inspection_start_time.trim()
      : null,
    area_missed_inspection_camera_ids: form.area_missed_inspection_enabled
      ? form.area_missed_inspection_camera_ids
      : [],
    blind_spot_stay_enabled: false,
    blind_spot_stay_threshold_seconds: null,
    workshop_overcapacity_enabled: form.workshop_overcapacity_enabled,
    workshop_overcapacity_limit: form.workshop_overcapacity_enabled
      ? Number(form.workshop_overcapacity_limit)
      : null,
    alert_cooldown_seconds: Math.round(Number(form.alert_cooldown_minutes) * 60),
  };
}

function serializeSystemSettingsForm(form: SystemSettingsFormState) {
  return JSON.stringify({
    other_person_scope: [...form.other_person_scope].sort(),
    area_missed_inspection_enabled: form.area_missed_inspection_enabled,
    area_missed_inspection_interval_hours: form.area_missed_inspection_interval_hours.trim(),
    area_missed_inspection_start_time: form.area_missed_inspection_start_time.trim(),
    area_missed_inspection_camera_ids: [...form.area_missed_inspection_camera_ids].sort(),
    blind_spot_stay_enabled: form.blind_spot_stay_enabled,
    blind_spot_stay_threshold_seconds: form.blind_spot_stay_threshold_seconds.trim(),
    workshop_overcapacity_enabled: form.workshop_overcapacity_enabled,
    workshop_overcapacity_limit: form.workshop_overcapacity_limit.trim(),
    alert_cooldown_minutes: form.alert_cooldown_minutes.trim(),
  });
}

export default function SupervisionPage() {
  const { data: activeVisitors = [], isLoading: visitorsLoading } = useActiveVisitors();
  const { data: visitorHistory = [] } = useVisitorHistory();
  const { data: activeExternal = [], isLoading: externalLoading } = useActiveExternalPersonnel();
  const { data: externalHistory = [] } = useExternalPersonnelHistory();
  const { data: systemEventOptions = [] } = useSupervisionEventOptions();
  const { data: appointmentEventOptions = [] } = useSupervisionEvents();
  const { data: cameras = [] } = useSupervisionCameras();
  const { data: systemSettings, isLoading: systemSettingsLoading } = useSystemSupervisionSettings();
  const { data: externalPersonsData } = useExternalPersons({ page: 1, pageSize: 200, search: "" });
  const externalPersons = useMemo(() => externalPersonsData?.persons ?? [], [externalPersonsData?.persons]);

  const createVisitor = useCreateVisitorRegistration();
  const updateVisitor = useUpdateVisitorRegistration();
  const deleteVisitor = useDeleteVisitorRegistration();
  const createExternal = useCreateExternalPersonnelRegistration();
  const updateExternal = useUpdateExternalPersonnelRegistration();
  const deleteExternal = useDeleteExternalPersonnelRegistration();
  const updateSystemSettings = useUpdateSystemSupervisionSettings();

  const [visitorHistoryOpen, setVisitorHistoryOpen] = useState(false);
  const [externalDialogOpen, setExternalDialogOpen] = useState(false);
  const [externalHistoryOpen, setExternalHistoryOpen] = useState(false);
  const [visitorPage, setVisitorPage] = useState(0);
  const [externalPage, setExternalPage] = useState(0);
  const [historyEditingId, setHistoryEditingId] = useState<string | null>(null);
  const [historyForm, setHistoryForm] = useState<VisitorFormState>(EMPTY_VISITOR_FORM);
  const [externalHistoryEditingId, setExternalHistoryEditingId] = useState<string | null>(null);
  const [externalHistoryForm, setExternalHistoryForm] = useState<ExternalFormState>(EMPTY_EXTERNAL_FORM);

  const [visitorForm, setVisitorForm] = useState<VisitorFormState>(EMPTY_VISITOR_FORM);
  const [externalForm, setExternalForm] = useState<ExternalFormState>(EMPTY_EXTERNAL_FORM);
  const [systemSettingsForm, setSystemSettingsForm] = useState<SystemSettingsFormState | null>(null);
  const [visitorEditing, setVisitorEditing] = useState<VisitorRegistration | null>(null);
  const [externalEditing, setExternalEditing] = useState<ExternalPersonnelRegistration | null>(null);
  const systemSettingsCardRef = useRef<HTMLDivElement | null>(null);

  const loading = visitorsLoading || externalLoading || systemSettingsLoading;
  const visitorPageSize = 2;
  const externalPageSize = 1;

  const sortedVisitors = useMemo(
    () =>
      [...activeVisitors].sort((a, b) => {
        const startDiff = new Date(b.start_time).getTime() - new Date(a.start_time).getTime();
        if (startDiff !== 0) {
          return startDiff;
        }
        return new Date(b.end_time).getTime() - new Date(a.end_time).getTime();
      }),
    [activeVisitors],
  );

  const visitorPageCount = Math.max(1, Math.ceil(sortedVisitors.length / visitorPageSize));
  const safeVisitorPage = Math.min(visitorPage, visitorPageCount - 1);
  const pagedVisitors = useMemo(
    () => sortedVisitors.slice(safeVisitorPage * visitorPageSize, safeVisitorPage * visitorPageSize + visitorPageSize),
    [sortedVisitors, safeVisitorPage],
  );

  const sortedVisitorHistory = useMemo(
    () =>
      [...visitorHistory].sort((a, b) => {
        const startDiff = new Date(b.start_time).getTime() - new Date(a.start_time).getTime();
        if (startDiff !== 0) {
          return startDiff;
        }
        return new Date(b.end_time).getTime() - new Date(a.end_time).getTime();
      }),
    [visitorHistory],
  );
  const sortedExternal = useMemo(
    () =>
      [...activeExternal].sort((a, b) => {
        const startDiff = new Date(b.start_time).getTime() - new Date(a.start_time).getTime();
        if (startDiff !== 0) {
          return startDiff;
        }
        return new Date(b.end_time).getTime() - new Date(a.end_time).getTime();
      }),
    [activeExternal],
  );
  const externalPageCount = Math.max(1, Math.ceil(sortedExternal.length / externalPageSize));
  const safeExternalPage = Math.min(externalPage, externalPageCount - 1);
  const pagedExternal = useMemo(
    () => sortedExternal.slice(safeExternalPage * externalPageSize, safeExternalPage * externalPageSize + externalPageSize),
    [sortedExternal, safeExternalPage],
  );
  const sortedExternalHistory = useMemo(
    () =>
      [...externalHistory].sort((a, b) => {
        const startDiff = new Date(b.start_time).getTime() - new Date(a.start_time).getTime();
        if (startDiff !== 0) {
          return startDiff;
        }
        return new Date(b.end_time).getTime() - new Date(a.end_time).getTime();
      }),
    [externalHistory],
  );

  const selectedEventOptions = useMemo(
    () => appointmentEventOptions.filter((item) => externalForm.supervision_events.includes(item.key)),
    [appointmentEventOptions, externalForm.supervision_events],
  );
  const availableEventOptions = useMemo(
    () => appointmentEventOptions.filter((item) => !externalForm.supervision_events.includes(item.key)),
    [appointmentEventOptions, externalForm.supervision_events],
  );

  const selectedCameras = useMemo(
    () => cameras.filter((item) => externalForm.allowed_camera_ids.includes(item.id)),
    [cameras, externalForm.allowed_camera_ids],
  );
  const availableCameras = useMemo(
    () => cameras.filter((item) => !externalForm.allowed_camera_ids.includes(item.id)),
    [cameras, externalForm.allowed_camera_ids],
  );

  const selectedExternalPerson = useMemo(
    () => externalPersons.find((item) => item.id === externalForm.external_person_id) ?? null,
    [externalPersons, externalForm.external_person_id],
  );
  const groupedSupervisionCameras = useMemo(
    () =>
      SUPERVISION_FLOORS.map((floor) => ({
        floor,
        cameras: cameras.filter((camera) => camera.floor === floor),
      })),
    [cameras],
  );

  const persistedSystemSettingsForm = useMemo(
    () => mapSystemSettingsToForm(systemSettings),
    [systemSettings],
  );
  const currentSystemSettingsForm = systemSettingsForm ?? persistedSystemSettingsForm;
  const hasPendingSystemSettingsChanges = useMemo(
    () => serializeSystemSettingsForm(currentSystemSettingsForm) !== serializeSystemSettingsForm(persistedSystemSettingsForm),
    [currentSystemSettingsForm, persistedSystemSettingsForm],
  );
  const updateSystemSettingsForm = (
    updater: (current: SystemSettingsFormState) => SystemSettingsFormState,
  ) => {
    setSystemSettingsForm((current) => updater(current ?? currentSystemSettingsForm));
  };
  const openCreateVisitor = () => {
    setVisitorEditing(null);
    setVisitorForm(EMPTY_VISITOR_FORM);
  };

  const openEditVisitor = (item: VisitorRegistration) => {
    const start = toDateParts(item.start_time);
    const end = toDateParts(item.end_time);
    setVisitorEditing(item);
    setVisitorForm({
      start_date: start.date,
      start_clock: start.time,
      end_date: end.date,
      end_clock: end.time,
      visiting_company: item.visiting_company,
      total_people: String(item.total_people),
    });
  };

  const openEditHistoryRow = (item: VisitorRegistration) => {
    const start = toDateParts(item.start_time);
    const end = toDateParts(item.end_time);
    setHistoryEditingId(item.id);
    setHistoryForm({
      start_date: start.date,
      start_clock: start.time,
      end_date: end.date,
      end_clock: end.time,
      visiting_company: item.visiting_company,
      total_people: String(item.total_people),
    });
  };

  const openCreateExternal = () => {
    setExternalEditing(null);
    setExternalForm(EMPTY_EXTERNAL_FORM);
    setExternalDialogOpen(true);
  };

  const openEditExternal = (item: ExternalPersonnelRegistration) => {
    const start = toDateParts(item.start_time);
    const end = toDateParts(item.end_time);
    setExternalEditing(item);
    setExternalForm({
      external_person_id: item.external_person_id ?? "",
      name: item.name,
      organization: item.organization,
      start_date: start.date,
      start_clock: start.time,
      end_date: end.date,
      end_clock: end.time,
      visit_reason: item.visit_reason,
      supervision_events: item.supervision_events,
      allowed_camera_ids: item.allowed_camera_ids,
    });
    setExternalDialogOpen(true);
  };

  const openEditExternalHistoryRow = (item: ExternalPersonnelRegistration) => {
    const start = toDateParts(item.start_time);
    const end = toDateParts(item.end_time);
    setExternalHistoryEditingId(item.id);
    setExternalHistoryForm({
      external_person_id: item.external_person_id ?? "",
      name: item.name,
      organization: item.organization,
      start_date: start.date,
      start_clock: start.time,
      end_date: end.date,
      end_clock: end.time,
      visit_reason: item.visit_reason,
      supervision_events: item.supervision_events,
      allowed_camera_ids: item.allowed_camera_ids,
    });
  };

  const handleSaveVisitor = async () => {
    const error = validateVisitorForm(visitorForm);
    if (error) {
      toast.error(error);
      return;
    }
    const payload = normalizeVisitorPayload(visitorForm);
    try {
      if (visitorEditing) {
        await updateVisitor.mutateAsync({ registrationId: visitorEditing.id, data: payload });
        toast.success("访客预约已更新");
      } else {
        await createVisitor.mutateAsync(payload);
        toast.success("访客预约已登记");
      }
      setVisitorEditing(null);
      setVisitorForm(EMPTY_VISITOR_FORM);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存访客登记失败");
    }
  };

  const handleDeleteVisitor = async (id: string) => {
    try {
      await deleteVisitor.mutateAsync(id);
      toast.success("访客登记已删除");
      setHistoryEditingId((current) => (current === id ? null : current));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除访客登记失败");
    }
  };

  const handleSaveHistoryRow = async (id: string) => {
    const error = validateVisitorForm(historyForm);
    if (error) {
      toast.error(error);
      return;
    }
    try {
      await updateVisitor.mutateAsync({
        registrationId: id,
        data: normalizeVisitorPayload(historyForm),
      });
      toast.success("历史来访记录已更新");
      setHistoryEditingId(null);
      setHistoryForm(EMPTY_VISITOR_FORM);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "更新历史来访记录失败");
    }
  };

  const handleSaveExternal = async () => {
    const error = validateExternalAppointmentForm(externalForm, selectedExternalPerson);
    if (error) {
      toast.error(error);
      return;
    }
    const payload = normalizeExternalPayload(externalForm);
    try {
      if (externalEditing) {
        await updateExternal.mutateAsync({
          registrationId: externalEditing.id,
          data: payload,
        });
      } else {
        await createExternal.mutateAsync(payload);
      }
      toast.success(externalEditing ? "外来人员预约已更新" : "外来人员预约已新增");
      setExternalEditing(null);
      setExternalForm(EMPTY_EXTERNAL_FORM);
      setExternalDialogOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存外来人员登记失败");
    }
  };

  const handleDeleteExternal = async (id: string) => {
    try {
      await deleteExternal.mutateAsync(id);
      toast.success("外来人员登记已删除");
      setExternalHistoryEditingId((current) => (current === id ? null : current));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除外来人员登记失败");
    }
  };

  const handleSaveExternalHistoryRow = async (id: string) => {
    const error = validateExternalForm(externalHistoryForm);
    if (error) {
      toast.error(error);
      return;
    }
    try {
      await updateExternal.mutateAsync({
        registrationId: id,
        data: normalizeExternalPayload(externalHistoryForm),
      });
      toast.success("外来人员历史记录已更新");
      setExternalHistoryEditingId(null);
      setExternalHistoryForm(EMPTY_EXTERNAL_FORM);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "更新外来人员历史记录失败");
    }
  };

  const handleSaveSystemSettings = useCallback(async (form: SystemSettingsFormState) => {
    const error = validateSystemSettingsForm(form);
    if (error) {
      toast.error(error);
      return;
    }
    try {
      await updateSystemSettings.mutateAsync(buildSystemSettingsPayload(form));
      setSystemSettingsForm(null);
      toast.success("监管配置已更新");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存监管配置失败");
    }
  }, [updateSystemSettings]);

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (!hasPendingSystemSettingsChanges || updateSystemSettings.isPending) {
        return;
      }
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (systemSettingsCardRef.current?.contains(target)) {
        return;
      }
      void handleSaveSystemSettings(currentSystemSettingsForm);
    };

    document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [
    currentSystemSettingsForm,
    handleSaveSystemSettings,
    hasPendingSystemSettingsChanges,
    updateSystemSettings.isPending,
  ]);

  if (loading) {
    return <PageLoader />;
  }

  return (
    <div className="space-y-6">
      <Card ref={systemSettingsCardRef} className="rounded-xl border-border/60">
        <CardContent className="space-y-6 px-6">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-primary" />
              <h3 className="text-lg font-semibold text-foreground">监管配置</h3>
            </div>
            <p className="text-sm leading-6 text-muted-foreground">
              配置其他人员监管事件，以及非相机系统检测的记录参数。
            </p>
          </div>

          <div className="grid gap-4 xl:grid-cols-[5fr_7fr_3fr]">
            <div className="space-y-3 rounded-lg border border-border/60 p-2">
              <div className="text-sm font-medium text-foreground">其他人员监管事件</div>
              <div className="text-xs leading-5 text-muted-foreground">
                用于未知人员或未纳入员工/预约身份监管范围的人员。勾选违规闯入后，出现在任何一个监控画面内都算违规闯入。
              </div>
              <div className="flex min-h-[220px] flex-wrap content-start gap-2 rounded-lg border border-border/60 p-3">
                {systemEventOptions.map((item) => {
                  const active = currentSystemSettingsForm.other_person_scope.includes(item.key);
                  return (
                    <button
                      key={item.key}
                      type="button"
                      onClick={() =>
                        updateSystemSettingsForm((current) => ({
                          ...current,
                          other_person_scope: active
                            ? current.other_person_scope.filter((value) => value !== item.key)
                            : [...current.other_person_scope, item.key],
                        }))
                      }
                      className={[
                        "rounded-md border px-3 py-1.5 text-sm transition-colors",
                        active
                          ? "border-emerald-500 bg-emerald-50 text-emerald-700"
                          : "border-border bg-background text-foreground hover:bg-muted",
                      ].join(" ")}
                    >
                      {item.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="space-y-4 rounded-lg border border-border/60 p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-1">
                  <div className="text-sm font-medium text-foreground">区域漏巡</div>
                  <div className="text-xs leading-5 text-muted-foreground">
                    设置检查周期和开始时间，并选择巡逻区域。系统会将这些摄像头同步标记为巡逻区域，并按周期检查是否完成巡逻。
                  </div>
                </div>
                <Button
                  type="button"
                  variant={currentSystemSettingsForm.area_missed_inspection_enabled ? "default" : "outline"}
                  size="sm"
                  onClick={() =>
                    updateSystemSettingsForm((current) => ({
                      ...current,
                      area_missed_inspection_enabled: !current.area_missed_inspection_enabled,
                    }))
                  }
                >
                  {currentSystemSettingsForm.area_missed_inspection_enabled ? "启用" : "未启用"}
                </Button>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="检查周期（小时）">
                  <Input
                    type="number"
                    min="0.05"
                    step="0.1"
                    disabled={!currentSystemSettingsForm.area_missed_inspection_enabled}
                    value={currentSystemSettingsForm.area_missed_inspection_interval_hours}
                    onChange={(event) =>
                      updateSystemSettingsForm((current) => ({
                        ...current,
                        area_missed_inspection_interval_hours: event.target.value,
                      }))
                    }
                    placeholder="例如 2"
                  />
                </Field>
                <Field label="开始时间">
                  <Input
                    type="time"
                    disabled={!currentSystemSettingsForm.area_missed_inspection_enabled}
                    value={currentSystemSettingsForm.area_missed_inspection_start_time}
                    onChange={(event) =>
                      updateSystemSettingsForm((current) => ({
                        ...current,
                        area_missed_inspection_start_time: event.target.value,
                      }))
                    }
                  />
                </Field>
              </div>
              <div className="space-y-2">
                <div className="text-sm font-medium text-foreground">巡逻区域</div>
                <div className="space-y-2 rounded-lg border border-border/20 p-1">
                  {groupedSupervisionCameras.map(({ floor, cameras: floorCameras }) => (
                    <div key={floor} className="flex items-center gap-3">
                      <div className="w-12 shrink-0 text-sm font-medium text-foreground">{floor}</div>
                      <div className="flex-1 overflow-x-auto">
                        <div className="flex min-h-5 min-w-max gap-2">
                          {floorCameras.length > 0 ? (
                            floorCameras.map((camera) => {
                              const active = currentSystemSettingsForm.area_missed_inspection_camera_ids.includes(camera.id);
                              return (
                                <button
                                  key={camera.id}
                                  type="button"
                                  disabled={!currentSystemSettingsForm.area_missed_inspection_enabled}
                                  onClick={() =>
                                    updateSystemSettingsForm((current) => ({
                                      ...current,
                                      area_missed_inspection_camera_ids: active
                                        ? current.area_missed_inspection_camera_ids.filter((value) => value !== camera.id)
                                        : [...current.area_missed_inspection_camera_ids, camera.id],
                                    }))
                                  }
                                  className={[
                                    "whitespace-nowrap rounded-md border px-1.5 py-0.5 text-xs leading-5 transition-colors",
                                    !currentSystemSettingsForm.area_missed_inspection_enabled
                                      ? "cursor-not-allowed border-border/60 text-muted-foreground opacity-60"
                                      : "",
                                    active
                                      ? "border-emerald-500 bg-emerald-50 text-emerald-700"
                                      : "border-border bg-background text-foreground hover:bg-muted",
                                  ].join(" ")}
                                >
                                  {camera.short_name || stripFloorPrefix(camera.name, camera.floor)}
                                </button>
                              );
                            })
                          ) : (
                            <div className="pt-1 text-xs leading-5 text-muted-foreground">暂无摄像头</div>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="grid gap-4">
              <div className="space-y-3 rounded-lg border border-border/60 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="text-sm font-medium text-foreground">盲区驻留</div>
                    <div className="text-xs leading-5 text-muted-foreground">长时间驻留车间监控盲区。</div>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => toast.info("该功能尚未开放")}
                  >
                    未启用
                  </Button>
                </div>
                <Field label="驻留阈值（分钟）">
                  <Input
                    type="number"
                    min="1"
                    step="0.5"
                    disabled
                    value=""
                    onChange={() => undefined}
                    placeholder="例如 30"
                  />
                </Field>
              </div>
              <div className="space-y-3 rounded-lg border border-border/60 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="text-sm font-medium text-foreground">车间超员</div>
                    <div className="text-xs leading-5 text-muted-foreground">可停留车间的最大人数。</div>
                  </div>
                  <Button
                    type="button"
                    variant={currentSystemSettingsForm.workshop_overcapacity_enabled ? "default" : "outline"}
                    size="sm"
                    onClick={() =>
                      updateSystemSettingsForm((current) => ({
                        ...current,
                        workshop_overcapacity_enabled: !current.workshop_overcapacity_enabled,
                      }))
                    }
                  >
                    {currentSystemSettingsForm.workshop_overcapacity_enabled ? "启用" : "未启用"}
                  </Button>
                </div>
                <Field label="超员人数阈值">
                  <Input
                    type="number"
                    min="0"
                    disabled={!currentSystemSettingsForm.workshop_overcapacity_enabled}
                    value={currentSystemSettingsForm.workshop_overcapacity_limit}
                    onChange={(event) =>
                      updateSystemSettingsForm((current) => ({
                        ...current,
                        workshop_overcapacity_limit: event.target.value,
                      }))
                    }
                    placeholder="例如 3"
                  />
                </Field>
              </div>
            </div>
          </div>

          <div className="rounded-lg border border-border/60 p-4">
            <div className="space-y-1">
              <div className="text-sm font-medium text-foreground">告警去重时间</div>
              <div className="text-xs leading-5 text-muted-foreground">
                同一人员同一类违规在该时间内跨摄像头只播报一次；未知人员会基于人脸特征做跨摄像头关联。
              </div>
            </div>
            <div className="mt-3 grid gap-3 md:max-w-xs">
              <Field label="去重时间（分钟）">
                <Input
                  type="number"
                  min="0.5"
                  step="0.5"
                  value={currentSystemSettingsForm.alert_cooldown_minutes}
                  onChange={(event) =>
                    updateSystemSettingsForm((current) => ({
                      ...current,
                      alert_cooldown_minutes: event.target.value,
                    }))
                  }
                  placeholder="例如 5"
                />
              </Field>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 xl:grid-cols-[1.5fr_1fr]">
        <Card className="rounded-xl border-border/60">
          <CardContent className="space-y-6 px-6">
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <Users className="h-5 w-5 text-primary" />
                  <h3 className="text-lg font-semibold text-foreground">访客登记</h3>
                </div>
                <p className="text-sm leading-6 text-muted-foreground">
                  访客由专人引导，来访时间内将不触发人员违规告警，请登记来访时间和进入车间总人数（包含陪同人员）
                </p>
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-24 mb-2">
              <div className="md:col-span-4">
                <Field label="来访开始日期">
                  <Input value={visitorForm.start_date} onChange={(event) => setVisitorForm({ ...visitorForm, start_date: event.target.value })} placeholder="YYYY-MM-DD" inputMode="numeric" />
                </Field>
              </div>
              <div className="md:col-span-3">
                <Field label="开始时间">
                  <Input value={visitorForm.start_clock} onChange={(event) => setVisitorForm({ ...visitorForm, start_clock: event.target.value })} placeholder="HH:mm" inputMode="numeric" />
                </Field>
              </div>
              <div className="md:col-span-4">
                <Field label="来访结束日期">
                  <Input value={visitorForm.end_date} onChange={(event) => setVisitorForm({ ...visitorForm, end_date: event.target.value })} placeholder="YYYY-MM-DD" inputMode="numeric" />
                </Field>
              </div>
              <div className="md:col-span-3">
                <Field label="结束时间">
                  <Input value={visitorForm.end_clock} onChange={(event) => setVisitorForm({ ...visitorForm, end_clock: event.target.value })} placeholder="HH:mm" inputMode="numeric" />
                </Field>
              </div>
              <div className="md:col-span-7">
                <Field label="来访单位">
                  <Input value={visitorForm.visiting_company} onChange={(event) => setVisitorForm({ ...visitorForm, visiting_company: event.target.value })} placeholder="请输入来访单位" />
                </Field>
              </div>
              <div className="md:col-span-3">
                <Field label="人数">
                  <Input type="number" min="1" value={visitorForm.total_people} onChange={(event) => setVisitorForm({ ...visitorForm, total_people: event.target.value })} placeholder="总人数" />
                </Field>
              </div>
            </div>
            <div className="flex flex-wrap gap-3 justify-center">
              <Button onClick={() => void handleSaveVisitor()} disabled={createVisitor.isPending || updateVisitor.isPending} className="flex-1">
                <Plus className="mr-2 h-4 w-4" />
                {visitorEditing ? "更新登记" : "登记"}
              </Button>
              <Button variant="outline" onClick={() => setVisitorHistoryOpen(true)} className="flex-1">
                <History className="mr-2 h-4 w-4" />
                历史来访记录
              </Button>
              {visitorEditing ? (
                <Button variant="outline" onClick={openCreateVisitor} className="shrink-0 flex-1">
                  取消编辑
                </Button>
              ) : null}
            </div>

            <section className="space-y-3">
              <div className="flex items-center gap-4">
                <div className="h-px flex-1 bg-border/60" />
                <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                  <span>预约记录</span>
                  <Badge variant="outline" className="rounded-md">{activeVisitors.length} 条</Badge>
                </div>
                <div className="h-px flex-1 bg-border/60" />
              </div>
              <div className="space-y-3">
                {sortedVisitors.length > 0 ? (
                  <div className="flex items-stretch gap-3">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setVisitorPage((current) => Math.max(0, current - 1))}
                      disabled={safeVisitorPage === 0}
                      className="h-[104px] w-12 shrink-0 rounded-lg flex items-center justify-center"
                    >
                      &lt;
                    </Button>

                    <div className="flex-1 grid h-[104px] gap-3 grid-cols-1 md:grid-cols-2">
                      {pagedVisitors.map((item) => (
                        <RecordRow
                          key={item.id}
                          title={`${item.visiting_company} -- ${item.total_people} 人`}
                          subtitle={
                            <>
                              <div>{`起始时间: ${formatDateTime(item.start_time)}`}</div>
                              <div>{`结束时间: ${formatDateTime(item.end_time)}`}</div>
                            </>
                          }
                          onEdit={() => openEditVisitor(item)}
                          onDelete={() => void handleDeleteVisitor(item.id)}
                        />
                      ))}
                    </div>

                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setVisitorPage((current) => Math.min(visitorPageCount - 1, current + 1))}
                      disabled={safeVisitorPage >= visitorPageCount - 1}
                      className="h-[104px] w-12 shrink-0 rounded-lg flex items-center justify-center"
                    >
                      &gt;
                    </Button>
                  </div>
                ) : (
                  <EmptyState text="暂无预约记录" />
                )}
              </div>
            </section>
          </CardContent>
        </Card>

        <Card className="rounded-xl border-border/60">
          <CardContent className="space-y-6 px-6">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <ShieldCheck className="h-5 w-5 text-primary" />
                <h3 className="text-lg font-semibold text-foreground">外来人员登记</h3>
              </div>
              <p className="text-sm leading-6 text-muted-foreground">
                外来人员为施工人员等暂入车间工作人员，将严格执行监管。
              </p>
            </div>

            <section className="space-y-3">
              <div className="flex items-center gap-4">
                <div className="h-px flex-1 bg-border/60" />
                <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                  <span>预约记录</span>
                  <Badge variant="outline" className="rounded-md">{activeExternal.length} 条</Badge>
                </div>
                <div className="h-px flex-1 bg-border/60" />
              </div>
              <div className="space-y-3">
                {sortedExternal.length > 0 ? (
                  <div className="flex items-stretch gap-3">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setExternalPage((current) => Math.max(0, current - 1))}
                      disabled={safeExternalPage === 0}
                      className="h-[180px] w-12 shrink-0 rounded-lg flex items-center justify-center"
                    >
                      &lt;
                    </Button>

                    <div className="flex-1 grid h-[180px] gap-3 grid-cols-1">
                      {pagedExternal.map((item) => (
                        <ExternalRecordCard
                          key={item.id}
                          title={`${item.name} · ${item.organization}`}
                          startTime={formatDateTime(item.start_time)}
                          endTime={formatDateTime(item.end_time)}
                          eventLabels={item.supervision_event_labels}
                          onEdit={() => openEditExternal(item)}
                          onDelete={() => void handleDeleteExternal(item.id)}
                        />
                      ))}
                    </div>

                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setExternalPage((current) => Math.min(externalPageCount - 1, current + 1))}
                      disabled={safeExternalPage >= externalPageCount - 1}
                      className="h-[180px] w-12 shrink-0 rounded-lg flex items-center justify-center"
                    >
                      &gt;
                    </Button>
                  </div>
                ) : (
                  <div className="flex-1 grid h-[180px] gap-3 grid-cols-1">
                    <EmptyState text="暂无外来人员记录" />
                  </div>
                )}
              </div>
            </section>

            <div className="grid grid-cols-2 gap-3">
              <Button onClick={openCreateExternal} className="w-full">
                <UserRoundPlus className="mr-2 h-4 w-4" />
                新增预约
              </Button>
              <Button variant="outline" onClick={() => setExternalHistoryOpen(true)} className="w-full">
                <History className="mr-2 h-4 w-4" />
                历史记录
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      <Dialog open={visitorHistoryOpen} onOpenChange={setVisitorHistoryOpen}>
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <DialogTitle>历史来访记录</DialogTitle>
            <DialogDescription>展示全部访客登记信息。</DialogDescription>
          </DialogHeader>
          <div className="mx-auto max-h-[60vh] w-full overflow-y-auto pr-1">
            {sortedVisitorHistory.length > 0 ? (
              <Table className="mx-auto">
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-center">开始时间</TableHead>
                    <TableHead className="text-center">结束时间</TableHead>
                    <TableHead className="text-center">来访单位</TableHead>
                    <TableHead className="w-20 text-center">人数</TableHead>
                    <TableHead className="w-20 text-center">编辑</TableHead>
                    <TableHead className="w-20 text-center">删除</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedVisitorHistory.map((item) => {
                    const isEditing = historyEditingId === item.id;
                    return (
                      <TableRow key={item.id}>
                        <TableCell className="whitespace-normal text-center">
                          {isEditing ? (
                            <div className="grid gap-2">
                              <Input value={historyForm.start_date} onChange={(event) => setHistoryForm({ ...historyForm, start_date: event.target.value })} placeholder="YYYY-MM-DD" inputMode="numeric" />
                              <Input value={historyForm.start_clock} onChange={(event) => setHistoryForm({ ...historyForm, start_clock: event.target.value })} placeholder="HH:mm" inputMode="numeric" />
                            </div>
                          ) : (
                            formatDateTime(item.start_time)
                          )}
                        </TableCell>
                        <TableCell className="whitespace-normal text-center">
                          {isEditing ? (
                            <div className="grid gap-2">
                              <Input value={historyForm.end_date} onChange={(event) => setHistoryForm({ ...historyForm, end_date: event.target.value })} placeholder="YYYY-MM-DD" inputMode="numeric" />
                              <Input value={historyForm.end_clock} onChange={(event) => setHistoryForm({ ...historyForm, end_clock: event.target.value })} placeholder="HH:mm" inputMode="numeric" />
                            </div>
                          ) : (
                            formatDateTime(item.end_time)
                          )}
                        </TableCell>
                        <TableCell className="whitespace-normal text-center">
                          {isEditing ? (
                            <Input value={historyForm.visiting_company} onChange={(event) => setHistoryForm({ ...historyForm, visiting_company: event.target.value })} placeholder="请输入来访单位" />
                          ) : (
                            item.visiting_company
                          )}
                        </TableCell>
                        <TableCell className="text-center">
                          {isEditing ? (
                            <Input type="number" min="1" value={historyForm.total_people} onChange={(event) => setHistoryForm({ ...historyForm, total_people: event.target.value })} placeholder="人数" />
                          ) : (
                            `${item.total_people}`
                          )}
                        </TableCell>
                        <TableCell className="text-center">
                          {isEditing ? (
                            <Button size="sm" onClick={() => void handleSaveHistoryRow(item.id)} disabled={updateVisitor.isPending}>
                              保存
                            </Button>
                          ) : (
                            <Button variant="outline" size="sm" onClick={() => openEditHistoryRow(item)}>
                              编辑
                            </Button>
                          )}
                        </TableCell>
                        <TableCell className="text-center">
                          {isEditing ? (
                            <Button variant="outline" size="sm" onClick={() => { setHistoryEditingId(null); setHistoryForm(EMPTY_VISITOR_FORM); }}>
                              取消
                            </Button>
                          ) : (
                            <Button variant="outline" size="sm" onClick={() => void handleDeleteVisitor(item.id)}>
                              删除
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            ) : (
              <EmptyState text="暂无历史来访记录" />
            )}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={externalDialogOpen} onOpenChange={setExternalDialogOpen}>
        <DialogContent className="max-h-[88vh] max-w-4xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{externalEditing ? "编辑外来人员预约" : "新增外来人员预约"}</DialogTitle>
            <DialogDescription>从外来人员档案中选择人员后，自动带出姓名、单位/部门、面容与默认监管事件。</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2 md:grid-cols-3">
            <Field label="选择外来人员">
              <select
                value={externalForm.external_person_id}
                onChange={(event) => {
                  const selected = externalPersons.find((item) => item.id === event.target.value) ?? null;
                  setExternalForm({
                    ...externalForm,
                    external_person_id: event.target.value,
                    name: selected?.name ?? "",
                    organization: selected?.organization ?? "",
                    supervision_events: selected?.supervision_scope ?? [],
                    allowed_camera_ids: selected?.allowed_camera_ids ?? [],
                  });
                }}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="">请选择外来人员</option>
                {externalPersons.map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.name} - {person.organization}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="姓名">
              <Input value={externalForm.name} readOnly placeholder="请选择外来人员" />
            </Field>
            <Field label="单位/部门">
              <Input value={externalForm.organization} readOnly placeholder="请选择外来人员" />
            </Field>
          </div>
          <div className="grid gap-4 py-2 md:grid-cols-2">
            <Field label="来访开始日期">
              <Input value={externalForm.start_date} onChange={(event) => setExternalForm({ ...externalForm, start_date: event.target.value })} placeholder="YYYY-MM-DD" inputMode="numeric" />
            </Field>
            <Field label="来访开始时间">
              <Input value={externalForm.start_clock} onChange={(event) => setExternalForm({ ...externalForm, start_clock: event.target.value })} placeholder="HH:mm" inputMode="numeric" />
            </Field>
            <Field label="来访结束日期">
              <Input value={externalForm.end_date} onChange={(event) => setExternalForm({ ...externalForm, end_date: event.target.value })} placeholder="YYYY-MM-DD" inputMode="numeric" />
            </Field>
            <Field label="来访结束时间">
              <Input value={externalForm.end_clock} onChange={(event) => setExternalForm({ ...externalForm, end_clock: event.target.value })} placeholder="HH:mm" inputMode="numeric" />
            </Field>
            <div className="space-y-2 md:col-span-2">
              <Label>来访事由</Label>
              <Input value={externalForm.visit_reason} onChange={(event) => setExternalForm({ ...externalForm, visit_reason: event.target.value })} placeholder="请输入来访事由" />
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label>面容</Label>
              <div className="text-xs text-muted-foreground">
                {selectedExternalPerson?.face_registered
                  ? "已自动关联外来人员档案中的面容"
                  : "所选外来人员档案尚未录入面容，请先到人员管理页面录入"}
              </div>
            </div>
            <div className="space-y-3 md:col-span-2">
              <div className="text-sm font-medium text-foreground">监管事件</div>
              <div className="flex min-h-16 flex-wrap gap-2 rounded-lg border border-border/60 p-3">
                {selectedEventOptions.length > 0 ? (
                  selectedEventOptions.map((item) => (
                    <button
                      key={item.key}
                      type="button"
                      onClick={() =>
                        setExternalForm({
                          ...externalForm,
                          supervision_events: externalForm.supervision_events.filter((value) => value !== item.key),
                          allowed_camera_ids:
                            item.key === "unauthorized_intrusion" ? [] : externalForm.allowed_camera_ids,
                        })
                      }
                      className="rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground transition-colors hover:bg-muted"
                    >
                      {item.label}
                    </button>
                  ))
                ) : (
                  <span className="text-sm text-muted-foreground">未设置</span>
                )}
              </div>
              <div className="flex min-h-16 flex-wrap gap-2 rounded-lg border border-border/60 p-3">
                {availableEventOptions.length > 0 ? (
                  availableEventOptions.map((item) => (
                    <button
                      key={item.key}
                      type="button"
                      onClick={() =>
                        setExternalForm({
                          ...externalForm,
                          supervision_events: [...externalForm.supervision_events, item.key],
                        })
                      }
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

            {externalForm.supervision_events.includes("unauthorized_intrusion") && (
              <div className="space-y-3 md:col-span-2">
                <div className="text-sm font-medium text-foreground">允许出现的监控画面</div>
                <div className="flex min-h-16 flex-wrap gap-2 rounded-lg border border-border/60 p-3">
                  {selectedCameras.length > 0 ? (
                    selectedCameras.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() =>
                          setExternalForm({
                            ...externalForm,
                            allowed_camera_ids: externalForm.allowed_camera_ids.filter((cameraId) => cameraId !== item.id),
                          })
                        }
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
                        onClick={() =>
                          setExternalForm({
                            ...externalForm,
                            allowed_camera_ids: [...externalForm.allowed_camera_ids, item.id],
                          })
                        }
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
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setExternalDialogOpen(false)}>取消</Button>
            <Button onClick={() => void handleSaveExternal()} disabled={createExternal.isPending || updateExternal.isPending}>
              {externalEditing ? "保存预约" : "新增预约"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={externalHistoryOpen} onOpenChange={setExternalHistoryOpen}>
        <DialogContent className="max-h-[88vh] max-w-6xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>外来人员历史记录</DialogTitle>
            <DialogDescription>展示全部外来人员预约及其监管事件配置。</DialogDescription>
          </DialogHeader>
          <div className="max-h-[68vh] overflow-y-auto pr-1">
            {sortedExternalHistory.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[8%] text-center">姓名</TableHead>
                    <TableHead className="w-[14%] text-center">单位/部门</TableHead>
                    <TableHead className="w-[16%] text-center">开始时间</TableHead>
                    <TableHead className="w-[16%] text-center">结束时间</TableHead>
                    <TableHead className="w-[22%] text-center">来访事由</TableHead>
                    <TableHead className="w-[14%] text-center">监管事件</TableHead>
                    <TableHead className="w-20 text-center">编辑</TableHead>
                    <TableHead className="w-20 text-center">删除</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedExternalHistory.map((item) => {
                    const isEditing = externalHistoryEditingId === item.id;
                    return (
                      <TableRow key={item.id}>
                        <TableCell className="whitespace-normal text-center">
                          {isEditing ? (
                            <Input
                              value={externalHistoryForm.name}
                              onChange={(event) => setExternalHistoryForm({ ...externalHistoryForm, name: event.target.value })}
                              placeholder="请输入姓名"
                              readOnly={Boolean(externalHistoryForm.external_person_id)}
                            />
                          ) : (
                            item.name
                          )}
                        </TableCell>
                        <TableCell className="whitespace-normal text-center">
                          {isEditing ? (
                            <Input
                              value={externalHistoryForm.organization}
                              onChange={(event) => setExternalHistoryForm({ ...externalHistoryForm, organization: event.target.value })}
                              placeholder="请输入单位/部门"
                              readOnly={Boolean(externalHistoryForm.external_person_id)}
                            />
                          ) : (
                            item.organization
                          )}
                        </TableCell>
                        <TableCell className="whitespace-normal text-center">
                          {isEditing ? (
                            <div className="grid gap-2">
                              <Input value={externalHistoryForm.start_date} onChange={(event) => setExternalHistoryForm({ ...externalHistoryForm, start_date: event.target.value })} placeholder="YYYY-MM-DD" inputMode="numeric" />
                              <Input value={externalHistoryForm.start_clock} onChange={(event) => setExternalHistoryForm({ ...externalHistoryForm, start_clock: event.target.value })} placeholder="HH:mm" inputMode="numeric" />
                            </div>
                          ) : (
                            formatDateTime(item.start_time)
                          )}
                        </TableCell>
                        <TableCell className="whitespace-normal text-center">
                          {isEditing ? (
                            <div className="grid gap-2">
                              <Input value={externalHistoryForm.end_date} onChange={(event) => setExternalHistoryForm({ ...externalHistoryForm, end_date: event.target.value })} placeholder="YYYY-MM-DD" inputMode="numeric" />
                              <Input value={externalHistoryForm.end_clock} onChange={(event) => setExternalHistoryForm({ ...externalHistoryForm, end_clock: event.target.value })} placeholder="HH:mm" inputMode="numeric" />
                            </div>
                          ) : (
                            formatDateTime(item.end_time)
                          )}
                        </TableCell>
                        <TableCell className="whitespace-normal text-center">
                          {isEditing ? (
                            <Input value={externalHistoryForm.visit_reason} onChange={(event) => setExternalHistoryForm({ ...externalHistoryForm, visit_reason: event.target.value })} placeholder="请输入来访事由" />
                          ) : (
                            item.visit_reason
                          )}
                        </TableCell>
                        <TableCell className="whitespace-normal text-center">
                          <div className="flex flex-wrap justify-center gap-1.5">
                            {item.supervision_event_labels.length > 0 ? (
                              item.supervision_event_labels.map((label) => (
                                <span
                                  key={label}
                                  className="rounded border border-red-300 bg-red-50 px-2 py-0.5 text-[11px] leading-4 text-red-700"
                                >
                                  {label}
                                </span>
                              ))
                            ) : (
                              <span className="text-sm text-muted-foreground">未设置</span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="text-center">
                          {isEditing ? (
                            <Button size="sm" onClick={() => void handleSaveExternalHistoryRow(item.id)} disabled={updateExternal.isPending}>
                              保存
                            </Button>
                          ) : (
                            <Button variant="outline" size="sm" onClick={() => openEditExternalHistoryRow(item)}>
                              编辑
                            </Button>
                          )}
                        </TableCell>
                        <TableCell className="text-center">
                          {isEditing ? (
                            <Button variant="outline" size="sm" onClick={() => { setExternalHistoryEditingId(null); setExternalHistoryForm(EMPTY_EXTERNAL_FORM); }}>
                              取消
                            </Button>
                          ) : (
                            <Button variant="outline" size="sm" onClick={() => void handleDeleteExternal(item.id)}>
                              删除
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            ) : (
              <EmptyState text="暂无外来人员历史记录" />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function RecordRow({
  title,
  subtitle,
  onEdit,
  onDelete,
}: {
  title: string;
  subtitle: ReactNode;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border/60 p-4 md:flex-row md:items-center md:justify-between">
      <div className="space-y-1">
        <div className="text-sm font-medium text-foreground">{title}</div>
        <div className="text-sm text-muted-foreground">{subtitle}</div>
      </div>
      <div className="flex flex-col gap-2">
        <Button variant="outline" size="sm" onClick={onEdit}>
          <Edit3 className="mr-2 h-4 w-4" />
          编辑
        </Button>
        <Button variant="outline" size="sm" onClick={onDelete}>
          <Trash2 className="mr-2 h-4 w-4" />
          删除
        </Button>
      </div>
    </div>
  );
}

function ExternalRecordCard({
  title,
  startTime,
  endTime,
  eventLabels,
  onEdit,
  onDelete,
}: {
  title: string;
  startTime: string;
  endTime: string;
  eventLabels: string[];
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flex h-[180px] flex-col justify-between rounded-lg border border-border/60 p-4">
      <div className="space-y-2">
        <div className="text-sm font-medium text-foreground">{title}</div>
        <div className="text-sm text-muted-foreground">{`起始时间: ${startTime} | 结束时间: ${endTime}`}</div>
        <div className="text-sm text-muted-foreground">{``}</div>
        <div className="flex flex-wrap gap-1.5">
          {eventLabels.length > 0 ? (
            eventLabels.map((label) => (
              <span
                key={label}
                className="rounded border border-red-300 bg-red-50 px-2 py-0.5 text-[11px] leading-4 text-red-700"
              >
                {label}
              </span>
            ))
          ) : (
            <span className="text-xs text-muted-foreground">未设置监管事件</span>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Button variant="outline" size="sm" onClick={onEdit} className="w-full">
          <Edit3 className="mr-2 h-4 w-4" />
          编辑
        </Button>
        <Button variant="outline" size="sm" onClick={onDelete} className="w-full">
          <Trash2 className="mr-2 h-4 w-4" />
          删除
        </Button>
      </div>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="flex min-h-24 items-center justify-center rounded-lg border border-dashed border-border/60 p-6 text-center text-sm text-muted-foreground">
      {text}
    </div>
  );
}

function Field({ label, children }: { label: string; children: import("react").ReactNode }) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      {children}
    </div>
  );
}
