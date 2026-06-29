"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api, { Camera, TimeFilters, VisualizationPeriod } from "@/lib/api";

export const queryKeys = {
  stats: {
    summary: (filters?: TimeFilters) => ["stats", "summary", filters ?? {}] as const,
    timeline: (days: number, filters?: TimeFilters) => ["stats", "timeline", days, filters ?? {}] as const,
    byPPE: ["stats", "by-ppe"] as const,
    visualization: (params: { trendDays: 7 | 30; typePeriod: VisualizationPeriod; rankingPeriod: VisualizationPeriod }) =>
      ["stats", "visualization", params] as const,
  },
  events: {
    all: ["events"] as const,
    list: (params?: Record<string, unknown>) => ["events", "list", params ?? {}] as const,
    recentViolations: (limit: number) => ["events", "recent", limit] as const,
    latestSnapshots: (limit: number) => ["events", "latest-snapshots", limit] as const,
  },
  persons: {
    all: ["persons"] as const,
    list: (params: { page: number; pageSize: number; search: string }) =>
      ["persons", "list", params] as const,
    externalList: (params: { page: number; pageSize: number; search: string }) =>
      ["persons", "external-list", params] as const,
    detail: (id: string) => ["persons", "detail", id] as const,
    topViolators: (limit: number) => ["persons", "top", limit] as const,
    todaySchedule: ["persons", "today-schedule"] as const,
    scheduleHistory: (page: number, pageSize: number) => ["persons", "schedule-history", page, pageSize] as const,
    supervisionEvents: ["persons", "supervision-events"] as const,
    jobTitles: ["persons", "job-titles"] as const,
  },
  cameras: {
    all: ["cameras"] as const,
    list: ["cameras", "list"] as const,
    default: ["cameras", "default"] as const,
    eventOptions: ["cameras", "event-options"] as const,
    livePeople: (cameraId: string) => ["cameras", "live-people", cameraId] as const,
    floorActivity: (floors: string[]) => ["cameras", "floor-activity", floors] as const,
  },
  supervision: {
    all: ["supervision"] as const,
    eventOptions: ["supervision", "event-options"] as const,
    cameras: ["supervision", "cameras"] as const,
    settings: ["supervision", "settings"] as const,
    visitorsActive: ["supervision", "visitors", "active"] as const,
    visitorsHistory: ["supervision", "visitors", "history"] as const,
    externalActive: ["supervision", "external", "active"] as const,
    externalHistory: ["supervision", "external", "history"] as const,
  },
};

export function useSummaryStats(filters?: TimeFilters) {
  return useQuery({
    queryKey: [...queryKeys.stats.summary(filters)],
    queryFn: () => api.getSummaryStats(filters),
    refetchInterval: 30000,
  });
}

export function useViolationTimeline(days: number = 7, filters?: TimeFilters) {
  return useQuery({
    queryKey: [...queryKeys.stats.timeline(days, filters)],
    queryFn: () => api.getViolationTimeline(days, filters),
  });
}

export function useViolationsByPPE() {
  return useQuery({
    queryKey: queryKeys.stats.byPPE,
    queryFn: () => api.getViolationsByPPE(),
  });
}

export function useVisualizationStats(params: {
  trendDays: 7 | 30;
  typePeriod: VisualizationPeriod;
  rankingPeriod: VisualizationPeriod;
  cameraPeriod: VisualizationPeriod;
}) {
  return useQuery({
    queryKey: queryKeys.stats.visualization(params),
    queryFn: () =>
      api.getVisualizationStats({
        trend_days: params.trendDays,
        type_period: params.typePeriod,
        ranking_period: params.rankingPeriod,
        camera_period: params.cameraPeriod,
      }),
    placeholderData: keepPreviousData,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
  });
}

export function useEvents(params?: {
  page?: number;
  pageSize?: number;
  cameraId?: string;
  personId?: string;
  personName?: string;
  violationsOnly?: boolean;
  violationType?: string;
  startTime?: string;
  endTime?: string;
}) {
  const apiParams = params ? {
    page: params.page,
    page_size: params.pageSize,
    camera_id: params.cameraId,
    person_id: params.personId,
    person_name: params.personName,
    violations_only: params.violationsOnly,
    violation_type: params.violationType,
    start_time: params.startTime,
    end_time: params.endTime,
  } : {};
  return useQuery({
    queryKey: queryKeys.events.list(apiParams),
    queryFn: () => api.getEvents(apiParams),
  });
}

export function useLivePeople(
  cameraId: string,
  enabled: boolean = true,
  refetchInterval: number = 1000
) {
  return useQuery({
    queryKey: queryKeys.cameras.livePeople(cameraId),
    queryFn: () => api.getLivePeople(cameraId),
    enabled: enabled && !!cameraId,
    refetchInterval,
  });
}

export function useFloorActivitySnapshots(floors: string[], enabled: boolean = true) {
  return useQuery({
    queryKey: queryKeys.cameras.floorActivity(floors),
    queryFn: () => api.getFloorActivitySnapshots(floors),
    enabled: enabled && floors.length > 0,
    refetchInterval: 1000,
    refetchIntervalInBackground: true,
    placeholderData: keepPreviousData,
  });
}

export function useRecentViolations(limit: number = 10) {
  return useQuery({
    queryKey: queryKeys.events.recentViolations(limit),
    queryFn: () => api.getRecentViolations(limit),
  });
}

export function useLatestViolationSnapshots(limit: number = 5) {
  return useQuery({
    queryKey: queryKeys.events.latestSnapshots(limit),
    queryFn: async () => {
      const response = await api.getEvents({
        page: 1,
        page_size: Math.max(limit * 4, 20),
        violations_only: true,
      });
      return response.events.filter((event) => !!event.snapshot_url).slice(0, limit);
    },
    placeholderData: keepPreviousData,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
  });
}

export function usePersons(params: { page?: number; pageSize?: number; search?: string } = {}) {
  const normalized = {
    page: params.page ?? 1,
    pageSize: params.pageSize ?? 20,
    search: params.search ?? "",
  };
  return useQuery({
    queryKey: queryKeys.persons.list(normalized),
    queryFn: () => api.getPersons(normalized),
  });
}

export function useExternalPersons(params: { page?: number; pageSize?: number; search?: string } = {}) {
  const normalized = {
    page: params.page ?? 1,
    pageSize: params.pageSize ?? 20,
    search: params.search ?? "",
  };
  return useQuery({
    queryKey: queryKeys.persons.externalList(normalized),
    queryFn: () => api.getExternalPersons(normalized),
  });
}

export function usePerson(id: string) {
  return useQuery({
    queryKey: queryKeys.persons.detail(id),
    queryFn: () => api.getPerson(id),
    enabled: !!id,
  });
}

export function useTopViolators(limit: number = 5) {
  return useQuery({
    queryKey: queryKeys.persons.topViolators(limit),
    queryFn: () => api.getTopViolators(limit),
  });
}

export function useTodaySchedule() {
  return useQuery({
    queryKey: queryKeys.persons.todaySchedule,
    queryFn: () => api.getTodaySchedule(),
  });
}

export function useScheduleHistory(page: number = 1, pageSize: number = 30) {
  return useQuery({
    queryKey: queryKeys.persons.scheduleHistory(page, pageSize),
    queryFn: () => api.getScheduleHistory({ page, pageSize }),
  });
}

export function useSupervisionEvents() {
  return useQuery({
    queryKey: queryKeys.persons.supervisionEvents,
    queryFn: () => api.getSupervisionEvents(),
  });
}

export function useJobTitleOptions() {
  return useQuery({
    queryKey: queryKeys.persons.jobTitles,
    queryFn: () => api.getJobTitleOptions(),
  });
}

export function useCreatePerson() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.createPerson>[0]) => api.createPerson(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.persons.all });
    },
  });
}

export function useCreateExternalPerson() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.createExternalPerson>[0]) => api.createExternalPerson(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.persons.all });
    },
  });
}

export function useUpdatePerson() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ personId, data }: { personId: string; data: Parameters<typeof api.updatePerson>[1] }) =>
      api.updatePerson(personId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.persons.all });
    },
  });
}

export function useUpdateExternalPerson() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ personId, data }: { personId: string; data: Parameters<typeof api.updateExternalPerson>[1] }) =>
      api.updateExternalPerson(personId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.persons.all });
    },
  });
}

export function useDeletePerson() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (personId: string) => api.deletePerson(personId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.persons.all });
    },
  });
}

export function useDeleteExternalPerson() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (personId: string) => api.deleteExternalPerson(personId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.persons.all });
    },
  });
}

export function useUploadPersonFace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ personId, file }: { personId: string; file: File }) => api.uploadPersonFace(personId, file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.persons.all });
    },
  });
}

export function useUploadExternalPersonFace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ personId, file }: { personId: string; file: File }) => api.uploadExternalPersonFace(personId, file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.persons.all });
    },
  });
}

export function useUpdateTodaySchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.updateSchedule>[0]) => api.updateSchedule(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.persons.todaySchedule });
      queryClient.invalidateQueries({ queryKey: ["persons", "schedule-history"] });
      queryClient.invalidateQueries({ queryKey: queryKeys.persons.all });
    },
  });
}

export function useCreateNextSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.createNextSchedule>[0]) => api.createNextSchedule(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.persons.todaySchedule });
      queryClient.invalidateQueries({ queryKey: ["persons", "schedule-history"] });
    },
  });
}

export function useCameras() {
  return useQuery({
    queryKey: queryKeys.cameras.list,
    queryFn: () => api.getCameras(),
  });
}

export function useDefaultCamera() {
  return useQuery({
    queryKey: queryKeys.cameras.default,
    queryFn: () => api.getDefaultCamera(),
  });
}

export function useCameraEventOptions() {
  return useQuery({
    queryKey: queryKeys.cameras.eventOptions,
    queryFn: () => api.getCameraEventOptions(),
  });
}

export function useCreateCamera() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.createCamera>[0]) => api.createCamera(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.cameras.all });
    },
  });
}

export function useUpdateCamera() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ cameraId, data }: { cameraId: string; data: Parameters<typeof api.updateCamera>[1] }) =>
      api.updateCamera(cameraId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.cameras.all });
    },
  });
}

export function useDeleteCamera() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteCamera(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.cameras.all });
    },
  });
}

export function useEnableCamera() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.enableCamera(id),
    onMutate: async (id: string) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.cameras.all });
      const previous = queryClient.getQueriesData({ queryKey: queryKeys.cameras.all });
      queryClient.setQueriesData<Camera[]>({ queryKey: queryKeys.cameras.all }, (old) => {
        if (Array.isArray(old)) {
          return old.map((c: Camera) => c.id === id ? { ...c, enabled: true } : c);
        }
        return old;
      });
      return { previous };
    },
    onError: (_err, _id, context) => {
      if (context?.previous) {
        for (const [key, data] of context.previous) {
          queryClient.setQueryData(key, data);
        }
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.cameras.all });
    },
  });
}

export function useDisableCamera() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.disableCamera(id),
    onMutate: async (id: string) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.cameras.all });
      const previous = queryClient.getQueriesData({ queryKey: queryKeys.cameras.all });
      queryClient.setQueriesData<Camera[]>({ queryKey: queryKeys.cameras.all }, (old) => {
        if (Array.isArray(old)) {
          return old.map((c: Camera) => c.id === id ? { ...c, enabled: false } : c);
        }
        return old;
      });
      return { previous };
    },
    onError: (_err, _id, context) => {
      if (context?.previous) {
        for (const [key, data] of context.previous) {
          queryClient.setQueryData(key, data);
        }
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.cameras.all });
    },
  });
}

export function useTestCamera() {
  return useMutation({
    mutationFn: (id: string) => api.testCamera(id),
  });
}

export function useSupervisionEventOptions() {
  return useQuery({
    queryKey: queryKeys.supervision.eventOptions,
    queryFn: () => api.getSupervisionEventOptions(),
  });
}

export function useSupervisionCameras() {
  return useQuery({
    queryKey: queryKeys.supervision.cameras,
    queryFn: () => api.getSupervisionCameras(),
  });
}

export function useSystemSupervisionSettings() {
  return useQuery({
    queryKey: queryKeys.supervision.settings,
    queryFn: () => api.getSystemSupervisionSettings(),
  });
}

export function useActiveVisitors() {
  return useQuery({
    queryKey: queryKeys.supervision.visitorsActive,
    queryFn: () => api.getActiveVisitors(),
    refetchInterval: 30000,
  });
}

export function useVisitorHistory() {
  return useQuery({
    queryKey: queryKeys.supervision.visitorsHistory,
    queryFn: () => api.getVisitorHistory(),
  });
}

export function useCreateVisitorRegistration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.createVisitorRegistration>[0]) => api.createVisitorRegistration(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.supervision.all });
    },
  });
}

export function useUpdateVisitorRegistration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ registrationId, data }: { registrationId: string; data: Parameters<typeof api.updateVisitorRegistration>[1] }) =>
      api.updateVisitorRegistration(registrationId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.supervision.all });
    },
  });
}

export function useDeleteVisitorRegistration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (registrationId: string) => api.deleteVisitorRegistration(registrationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.supervision.all });
    },
  });
}

export function useActiveExternalPersonnel() {
  return useQuery({
    queryKey: queryKeys.supervision.externalActive,
    queryFn: () => api.getActiveExternalPersonnel(),
    refetchInterval: 30000,
  });
}

export function useExternalPersonnelHistory() {
  return useQuery({
    queryKey: queryKeys.supervision.externalHistory,
    queryFn: () => api.getExternalPersonnelHistory(),
  });
}

export function useCreateExternalPersonnelRegistration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.createExternalPersonnelRegistration>[0]) =>
      api.createExternalPersonnelRegistration(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.supervision.all });
    },
  });
}

export function useUpdateExternalPersonnelRegistration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ registrationId, data }: { registrationId: string; data: Parameters<typeof api.updateExternalPersonnelRegistration>[1] }) =>
      api.updateExternalPersonnelRegistration(registrationId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.supervision.all });
    },
  });
}

export function useUploadExternalPersonnelFace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ registrationId, file }: { registrationId: string; file: File }) =>
      api.uploadExternalPersonnelFace(registrationId, file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.supervision.all });
    },
  });
}

export function useDeleteExternalPersonnelRegistration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (registrationId: string) => api.deleteExternalPersonnelRegistration(registrationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.supervision.all });
    },
  });
}

export function useUpdateSystemSupervisionSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof api.updateSystemSupervisionSettings>[0]) =>
      api.updateSystemSupervisionSettings(data),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.supervision.settings, data);
      queryClient.invalidateQueries({ queryKey: queryKeys.supervision.all });
    },
  });
}

export function useCompareFaceAgainstRegistry() {
  return useMutation({
    mutationFn: (file: File) => api.compareFaceAgainstRegistry(file),
  });
}

export function useCompareFaceFromCamera() {
  return useMutation({
    mutationFn: (cameraId: string) => api.compareFaceFromCamera(cameraId),
  });
}

export function useSetDefaultCamera() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.setDefaultCamera(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.cameras.all });
    },
  });
}

export function useDashboardData(filters?: TimeFilters) {
  const { data: stats, ...rest } = useSummaryStats(filters);
  const { data: violations } = useRecentViolations(10);
  const { data: timeline } = useViolationTimeline(7, filters);
  const { data: ppeBreakdown } = useViolationsByPPE();
  return { stats, violations, timeline, ppeBreakdown, ...rest };
}
