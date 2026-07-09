export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");

export function resolveApiAssetUrl(path?: string | null): string | null {
  if (!path) return null;
  if (/^https?:\/\//.test(path)) return path;
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return API_BASE_URL ? `${API_BASE_URL}${normalizedPath}` : normalizedPath;
}

async function readErrorDetail(response: Response): Promise<string> {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      const payload = await response.json();
      if (payload && typeof payload.detail === "string" && payload.detail.trim()) {
        return payload.detail;
      }
    } catch {
      // Fall through to status-based message.
    }
  } else {
    try {
      const text = await response.text();
      if (text.trim()) {
        return text;
      }
    } catch {
      // Ignore and use status message.
    }
  }
  return `API Error: ${response.status} ${response.statusText}`;
}

export interface SummaryStats {
  total_events: number;
  today_events: number;
  total_violations: number;
  today_violations: number;
  total_persons: number;
  compliance_rate: number;
  last_updated: string;
}

export interface ComplianceEvent {
  id: string;
  person_id: string | null;
  person_name?: string | null;
  timestamp: string;
  video_source: string | null;
  camera_id: string | null;
  camera_ids?: string[];
  camera_name?: string | null;
  frame_number?: number;
  detected_ppe: string[];
  missing_ppe: string[];
  action_violations?: string[];
  violation_labels?: string[];
  danger_event_types?: string[];
  is_violation: boolean;
  start_frame?: number | null;
  end_frame?: number | null;
  end_timestamp?: string | null;
  duration_frames?: number;
  is_ongoing?: boolean;
  snapshot_overlay?: {
    image_width: number;
    image_height: number;
    boxes: Array<{
      kind: string;
      label: string;
      box: number[];
      violation_key?: string | null;
    }>;
  } | null;
  snapshot_url?: string | null;
  video_url?: string | null;
}

export interface LivePersonOverlay {
  track_id?: number | null;
  stable_track_id?: number | null;
  raw_track_id?: number | null;
  person_id?: string | null;
  person_name: string;
  box: number[];
  detected_ppe?: string[];
  missing_ppe?: string[];
  stable_missing_ppe?: string[];
  detection_confidence?: Record<string, number>;
}

export interface LivePersonOverlayResponse {
  camera_id: string;
  frame_width: number;
  frame_height: number;
  persons: LivePersonOverlay[];
  last_frame_at?: string | null;
}

export interface FloorActivitySnapshotItem {
  floor: string;
  camera_id: string;
  camera_name: string;
  person_count: number;
  last_frame_at?: string | null;
  frame_url?: string | null;
  frame_width: number;
  frame_height: number;
  persons: LivePersonOverlay[];
}

export interface FloorActivitySnapshotResponse {
  items: FloorActivitySnapshotItem[];
}

export interface Person {
  id: string;
  name: string | null;
  is_employee?: boolean;
  workshop?: string | null;
  job_title?: string | null;
  supervision_scope?: string[];
  supervision_scope_labels?: string[];
  face_registered?: boolean;
  today_violation_count?: number;
  seven_day_violation_count?: number;
  thirty_day_violation_count?: number;
  first_seen: string;
  last_seen: string;
  total_events: number;
  violation_count: number;
  compliance_rate: number;
}

export interface PersonListItem {
  id: string;
  name: string | null;
  workshop: string | null;
  job_title: string | null;
  supervision_scope: string[];
  supervision_scope_labels: string[];
  face_registered: boolean;
  face_image_url?: string | null;
  today_violation_count: number;
  seven_day_violation_count: number;
  thirty_day_violation_count: number;
  first_seen: string;
  last_seen: string;
}

export interface ExternalPersonListItem {
  id: string;
  name: string;
  organization: string;
  supervision_scope: string[];
  supervision_scope_labels: string[];
  allowed_camera_ids: string[];
  face_registered: boolean;
  face_image_url?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface ShiftScheduleRow {
  id: string;
  shift_date: string;
  day_person_ids: string[];
  day_person_names: string[];
  night_person_ids: string[];
  night_person_names: string[];
}

export interface ShiftScheduleHistoryResponse {
  items: ShiftScheduleRow[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface SupervisionEventOption {
  key: string;
  label: string;
}

export interface JobTitleOption {
  id: string;
  code: string;
  name: string;
  sort_order: number;
}

export interface SystemSupervisionSettings {
  id: string;
  other_person_scope: string[];
  area_missed_inspection_enabled: boolean;
  area_missed_inspection_interval_hours?: number | null;
  area_missed_inspection_start_time?: string | null;
  area_missed_inspection_camera_ids: string[];
  blind_spot_stay_enabled: boolean;
  blind_spot_stay_threshold_seconds?: number | null;
  workshop_overcapacity_enabled: boolean;
  workshop_overcapacity_limit?: number | null;
  alert_cooldown_seconds?: number | null;
}

export interface TimelineData {
  date: string;
  violations: number;
}

export interface PPEBreakdown {
  ppe_type: string;
  count: number;
}

export type VisualizationPeriod = 'today' | '7d' | '30d';

export interface VisualizationTypeStat {
  event_type: string;
  label: string;
  count: number;
}

export interface VisualizationViolatorStat {
  person_id: string;
  person_name?: string | null;
  violation_count: number;
}

export interface VisualizationCameraTypeStat {
  event_type: string;
  label: string;
  count: number;
}

export interface VisualizationCameraStat {
  camera_id: string;
  camera_name: string;
  violation_count: number;
  type_breakdown: VisualizationCameraTypeStat[];
}

export interface VisualizationStats {
  today_violation_count: number;
  week_violation_count: number;
  online_camera_count: number;
  last_inspection_time: string | null;
  last_updated: string;
  trend_days: number;
  trend: TimelineData[];
  type_period: VisualizationPeriod;
  type_breakdown: VisualizationTypeStat[];
  ranking_period: VisualizationPeriod;
  top_violators: VisualizationViolatorStat[];
  top_cameras: VisualizationCameraStat[];
}

export interface Camera {
  id: string;
  name: string;
  floor?: string | null;
  name_suffix?: string | null;
  vendor?: string | null;
  source_type: string;
  host?: string | null;
  port?: number | null;
  username?: string | null;
  password?: string | null;
  channel?: number | null;
  stream_type?: string | null;
  enabled: boolean;
  is_default: boolean;
  last_test_status?: string | null;
  last_test_error?: string | null;
  last_seen_at?: string | null;
  created_at?: string | null;
  video_resolution?: string | null;
  frame_rate?: number | null;
  max_bitrate?: number | null;
  video_encoding?: string | null;
  transport_mode?: string | null;
  camera_detection_scope?: string[];
  camera_detection_scope_labels?: string[];
  backend_detection_scope?: string[];
  backend_detection_scope_labels?: string[];
  area_overcapacity_polygon?: number[][];
  area_overcapacity_limit?: number | null;
}

export interface CameraEventOption {
  key: string;
  label: string;
}

export interface CameraEventOptionsResponse {
  camera_detection: CameraEventOption[];
  backend_detection: CameraEventOption[];
}

export interface CameraTestResult {
  success: boolean;
  message: string;
  stream_url?: string | null;
  device_info?: Record<string, unknown> | null;
  error?: string | null;
}

export interface GalleryItem {
  id: string;
  snapshot_url: string | null;
  timestamp: string;
  camera_id: string | null;
  person_id: string | null;
  missing_ppe: string[];
  message: string;
}

export interface GalleryResponse {
  items: GalleryItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface TimeFilters {
  start_time?: string;
  end_time?: string;
  camera_id?: string;
}

export interface CameraConfigResult {
  success: boolean;
  message: string;
  config?: Record<string, unknown>;
  error?: string | null;
}

export interface CameraConfigPayload {
  video_encoding?: 'H.265' | 'H.264';
  video_resolution_width?: number;
  video_resolution_height?: number;
  frame_rate?: number;
  max_bitrate?: number;
  bit_rate?: number;
  gov_length?: number;
}

export interface VisitorRegistration {
  id: string;
  start_time: string;
  end_time: string;
  visiting_company: string;
  total_people: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ExternalPersonnelRegistration {
  id: string;
  external_person_id?: string | null;
  name: string;
  organization: string;
  start_time: string;
  end_time: string;
  visit_reason: string;
  supervision_events: string[];
  supervision_event_labels: string[];
  allowed_camera_ids: string[];
  face_registered: boolean;
  face_image_url?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SupervisionCamera {
  id: string;
  name: string;
  floor?: string | null;
  short_name?: string | null;
  enabled: boolean;
}

export interface FaceMatchCandidate {
  subject_id: string;
  subject_type: "employee" | "external_person" | "external_registration" | string;
  name: string;
  organization?: string | null;
  similarity: number;
  cosine_similarity?: number | null;
  face_image_url?: string | null;
}

export interface FaceMatchResponse {
  matched: boolean;
  best_match?: FaceMatchCandidate | null;
  candidates: FaceMatchCandidate[];
}

export interface CameraFaceMatchResponse {
  camera_id: string;
  matched: boolean;
  best_match?: FaceMatchCandidate | null;
  candidates: FaceMatchCandidate[];
  face_detected: boolean;
}

class ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string = API_BASE_URL) {
    this.baseUrl = baseUrl;
  }

  private async fetch<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (options?.headers && typeof options.headers === 'object' && !Array.isArray(options.headers)) {
      Object.assign(headers, options.headers);
    }
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      ...options,
      headers,
    });

    if (!response.ok) {
      throw new Error(await readErrorDetail(response));
    }

    return response.json();
  }

  private async fetchFormData<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
    const headers: Record<string, string> = {};
    if (options?.headers && typeof options.headers === 'object' && !Array.isArray(options.headers)) {
      Object.assign(headers, options.headers);
    }
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      ...options,
      headers,
    });

    if (!response.ok) {
      throw new Error(await readErrorDetail(response));
    }

    return response.json();
  }

  // Stats endpoints
  async getSummaryStats(filters?: TimeFilters): Promise<SummaryStats> {
    const params = new URLSearchParams();
    if (filters?.camera_id) params.set('camera_id', filters.camera_id);
    if (filters?.start_time) params.set('start_time', filters.start_time);
    if (filters?.end_time) params.set('end_time', filters.end_time);
    const qs = params.toString();
    return this.fetch<SummaryStats>(`/api/stats/summary${qs ? '?' + qs : ''}`);
  }

  async getViolationTimeline(days: number = 7, filters?: TimeFilters): Promise<TimelineData[]> {
    const params = new URLSearchParams();
    params.set('days', days.toString());
    if (filters?.camera_id) params.set('camera_id', filters.camera_id);
    if (filters?.start_time) params.set('start_time', filters.start_time);
    if (filters?.end_time) params.set('end_time', filters.end_time);
    return this.fetch<TimelineData[]>(`/api/stats/timeline?${params.toString()}`);
  }

  async getViolationsByPPE(): Promise<PPEBreakdown[]> {
    return this.fetch<PPEBreakdown[]>('/api/stats/by-ppe');
  }

  async getVisualizationStats(params: {
    trend_days?: 7 | 30;
    type_period?: VisualizationPeriod;
    ranking_period?: VisualizationPeriod;
    camera_period?: VisualizationPeriod;
  } = {}): Promise<VisualizationStats> {
    const searchParams = new URLSearchParams();
    searchParams.set('trend_days', String(params.trend_days ?? 7));
    searchParams.set('type_period', params.type_period ?? 'today');
    searchParams.set('ranking_period', params.ranking_period ?? 'today');
    searchParams.set('camera_period', params.camera_period ?? '7d');
    return this.fetch<VisualizationStats>(`/api/stats/visualization?${searchParams.toString()}`);
  }

  // Events endpoints
  async getEvents(params: {
    page?: number;
    page_size?: number;
    camera_id?: string;
    person_id?: string;
    person_name?: string;
    violations_only?: boolean;
    violation_type?: string;
    start_time?: string;
    end_time?: string;
  } = {}): Promise<{ events: ComplianceEvent[]; total: number; page: number; page_size: number }> {
    const searchParams = new URLSearchParams();
    if (params.page) searchParams.set('page', params.page.toString());
    if (params.page_size) searchParams.set('page_size', params.page_size.toString());
    if (params.camera_id) searchParams.set('camera_id', params.camera_id);
    if (params.person_id) searchParams.set('person_id', params.person_id);
    if (params.person_name) searchParams.set('person_name', params.person_name);
    if (params.violations_only) searchParams.set('violations_only', 'true');
    if (params.violation_type) searchParams.set('violation_type', params.violation_type);
    if (params.start_time) searchParams.set('start_time', params.start_time);
    if (params.end_time) searchParams.set('end_time', params.end_time);

    return this.fetch(`/api/events?${searchParams.toString()}`);
  }

  async getViolationGallery(params: {
    page?: number;
    page_size?: number;
    camera_id?: string;
    start_time?: string;
    end_time?: string;
  } = {}): Promise<GalleryResponse> {
    const searchParams = new URLSearchParams();
    if (params.page) searchParams.set('page', params.page.toString());
    if (params.page_size) searchParams.set('page_size', params.page_size.toString());
    if (params.camera_id) searchParams.set('camera_id', params.camera_id);
    if (params.start_time) searchParams.set('start_time', params.start_time);
    if (params.end_time) searchParams.set('end_time', params.end_time);
    return this.fetch<GalleryResponse>(`/api/events/violations/gallery?${searchParams.toString()}`);
  }

  async getRecentViolations(limit: number = 10): Promise<ComplianceEvent[]> {
    return this.fetch<ComplianceEvent[]>(`/api/events/recent/violations?limit=${limit}`);
  }

  // Persons endpoints
  async getPersons(params: {
    page?: number;
    pageSize?: number;
    search?: string;
  } = {}): Promise<{ persons: PersonListItem[]; total: number }> {
    const searchParams = new URLSearchParams();
    searchParams.set('page', String(params.page ?? 1));
    searchParams.set('page_size', String(params.pageSize ?? 20));
    if (params.search?.trim()) {
      searchParams.set('search', params.search.trim());
    }
    return this.fetch(`/api/persons?${searchParams.toString()}`);
  }

  async getExternalPersons(params: {
    page?: number;
    pageSize?: number;
    search?: string;
  } = {}): Promise<{ persons: ExternalPersonListItem[]; total: number }> {
    const searchParams = new URLSearchParams();
    searchParams.set('page', String(params.page ?? 1));
    searchParams.set('page_size', String(params.pageSize ?? 20));
    if (params.search?.trim()) {
      searchParams.set('search', params.search.trim());
    }
    return this.fetch(`/api/persons/external?${searchParams.toString()}`);
  }

  async getPerson(personId: string): Promise<Person> {
    return this.fetch<Person>(`/api/persons/${personId}`);
  }

  async getTopViolators(limit: number = 5): Promise<Person[]> {
    return this.fetch<Person[]>(`/api/persons/top/violators?limit=${limit}`);
  }

  async createPerson(payload: {
    name: string;
    workshop?: string | null;
    job_title?: string | null;
    supervision_scope?: string[];
  }): Promise<Person> {
    return this.fetch<Person>('/api/persons', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async createExternalPerson(payload: {
    name: string;
    organization: string;
    supervision_scope?: string[];
    allowed_camera_ids?: string[];
  }): Promise<ExternalPersonListItem> {
    return this.fetch<ExternalPersonListItem>('/api/persons/external', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updatePerson(personId: string, payload: {
    name?: string | null;
    workshop?: string | null;
    job_title?: string | null;
    supervision_scope?: string[];
  }): Promise<Person> {
    return this.fetch<Person>(`/api/persons/${personId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  async updateExternalPerson(personId: string, payload: {
    name?: string;
    organization?: string;
    supervision_scope?: string[];
    allowed_camera_ids?: string[];
  }): Promise<ExternalPersonListItem> {
    return this.fetch<ExternalPersonListItem>(`/api/persons/external/${personId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  async deletePerson(personId: string): Promise<{ message: string }> {
    return this.fetch<{ message: string }>(`/api/persons/${personId}`, {
      method: 'DELETE',
    });
  }

  async deleteExternalPerson(personId: string): Promise<{ message: string }> {
    return this.fetch<{ message: string }>(`/api/persons/external/${personId}`, {
      method: 'DELETE',
    });
  }

  async uploadPersonFace(personId: string, file: File): Promise<Person> {
    const formData = new FormData();
    formData.append('file', file);
    return this.fetchFormData<Person>(`/api/persons/${personId}/face`, {
      method: 'POST',
      body: formData,
    });
  }

  async uploadExternalPersonFace(personId: string, file: File): Promise<ExternalPersonListItem> {
    const formData = new FormData();
    formData.append('file', file);
    return this.fetchFormData<ExternalPersonListItem>(`/api/persons/external/${personId}/face`, {
      method: 'POST',
      body: formData,
    });
  }

  async getSupervisionEvents(): Promise<SupervisionEventOption[]> {
    return this.fetch<SupervisionEventOption[]>('/api/persons/supervision-events');
  }

  async getJobTitleOptions(): Promise<JobTitleOption[]> {
    return this.fetch<JobTitleOption[]>('/api/persons/job-titles');
  }

  async getTodaySchedule(): Promise<ShiftScheduleRow> {
    return this.fetch<ShiftScheduleRow>('/api/persons/schedule/today');
  }

  async getScheduleHistory(params: {
    page?: number;
    pageSize?: number;
  } = {}): Promise<ShiftScheduleHistoryResponse> {
    const searchParams = new URLSearchParams();
    searchParams.set('page', String(params.page ?? 1));
    searchParams.set('page_size', String(params.pageSize ?? 30));
    return this.fetch<ShiftScheduleHistoryResponse>(`/api/persons/schedule/history?${searchParams.toString()}`);
  }

  async updateSchedule(payload: {
    shift_date: string;
    day_person_ids?: string[];
    night_person_ids?: string[];
  }): Promise<ShiftScheduleRow> {
    return this.fetch<ShiftScheduleRow>('/api/persons/schedule', {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async createNextSchedule(payload: {
    base_shift_date?: string;
  } = {}): Promise<ShiftScheduleRow> {
    return this.fetch<ShiftScheduleRow>('/api/persons/schedule/next', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  getSnapshotUrl(filename: string): string {
    if (/^https?:\/\//.test(filename)) {
      return filename;
    }
    return `${this.baseUrl}/api/events/snapshots/${filename}`;
  }

  getLiveFeedUrl(cameraId?: string, options?: { raw?: boolean }): string {
    const rawQuery = options?.raw ? "?raw=true" : "";
    if (cameraId) {
      return `${this.baseUrl}/api/cameras/${cameraId}/live/feed${rawQuery}`;
    }
    return `${this.baseUrl}/api/stream/live/feed`;
  }

  getCameraFacePreviewUrl(cameraId: string): string {
    return `${this.baseUrl}/api/cameras/${cameraId}/face-preview/feed`;
  }

  getCameraPreviewFeedUrl(cameraId: string): string {
    return `${this.baseUrl}/api/cameras/${cameraId}/preview/feed`;
  }

  getLiveFrameImageUrl(cameraId: string, options?: { raw?: boolean }): string {
    const rawQuery = options?.raw ? "?raw=true" : "";
    return `${this.baseUrl}/api/cameras/${cameraId}/live/frame.jpg${rawQuery}`;
  }

  async getLivePeople(cameraId: string): Promise<LivePersonOverlayResponse> {
    return this.fetch<LivePersonOverlayResponse>(`/api/cameras/${cameraId}/live/people`);
  }

  async getFloorActivitySnapshots(floors: string[]): Promise<FloorActivitySnapshotResponse> {
    const searchParams = new URLSearchParams();
    if (floors.length > 0) {
      searchParams.set("floors", floors.join(","));
    }
    return this.fetch<FloorActivitySnapshotResponse>(`/api/cameras/live/floor-activity?${searchParams.toString()}`);
  }

  async getCameras(): Promise<Camera[]> {
    return this.fetch<Camera[]>('/api/cameras');
  }

  async getCameraEventOptions(): Promise<CameraEventOptionsResponse> {
    return this.fetch<CameraEventOptionsResponse>('/api/cameras/event-options');
  }

  async getDefaultCamera(): Promise<Camera> {
    return this.fetch<Camera>('/api/cameras/default');
  }

  async createCamera(payload: {
    floor: string;
    name_suffix: string;
    host: string;
    port?: number;
    username: string;
    password: string;
    channel?: number;
    stream_type?: string;
    enabled?: boolean;
    is_default?: boolean;
    vendor?: string;
    video_resolution?: string | null;
    frame_rate?: number | null;
    max_bitrate?: number | null;
    video_encoding?: string | null;
    transport_mode?: string | null;
    camera_detection_scope?: string[];
    backend_detection_scope?: string[];
    area_overcapacity_polygon?: number[][];
    area_overcapacity_limit?: number | null;
  }): Promise<Camera> {
    return this.fetch<Camera>('/api/cameras', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateCamera(cameraId: string, payload: Partial<{
    floor: string;
    name_suffix: string;
    host: string;
    port: number;
    username: string;
    password: string;
    channel: number;
    stream_type: string;
    enabled: boolean;
    is_default: boolean;
    vendor: string;
    video_resolution: string | null;
    frame_rate: number | null;
    max_bitrate: number | null;
    video_encoding: string | null;
    transport_mode: string | null;
    camera_detection_scope: string[];
    backend_detection_scope: string[];
    area_overcapacity_polygon: number[][];
    area_overcapacity_limit: number | null;
  }>): Promise<Camera> {
    return this.fetch<Camera>(`/api/cameras/${cameraId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  async deleteCamera(cameraId: string): Promise<{ message: string }> {
    return this.fetch<{ message: string }>(`/api/cameras/${cameraId}`, {
      method: 'DELETE',
    });
  }

  async testCamera(cameraId: string): Promise<CameraTestResult> {
    return this.fetch<CameraTestResult>(`/api/cameras/${cameraId}/test`, {
      method: 'POST',
    });
  }

  async enableCamera(cameraId: string): Promise<Camera> {
    return this.fetch<Camera>(`/api/cameras/${cameraId}/enable`, {
      method: 'POST',
    });
  }

  async disableCamera(cameraId: string): Promise<Camera> {
    return this.fetch<Camera>(`/api/cameras/${cameraId}/disable`, {
      method: 'POST',
    });
  }

  async setDefaultCamera(cameraId: string): Promise<Camera> {
    return this.fetch<Camera>(`/api/cameras/${cameraId}/set-default`, {
      method: 'POST',
    });
  }

  async getCameraConfig(cameraId: string): Promise<CameraConfigResult> {
    return this.fetch<CameraConfigResult>(`/api/cameras/${cameraId}/config`);
  }

  async updateCameraConfig(cameraId: string, payload: CameraConfigPayload): Promise<CameraConfigResult> {
    return this.fetch<CameraConfigResult>(`/api/cameras/${cameraId}/config`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async getSupervisionEventOptions(): Promise<SupervisionEventOption[]> {
    return this.fetch<SupervisionEventOption[]>('/api/supervision/event-options');
  }

  async getSupervisionCameras(): Promise<SupervisionCamera[]> {
    return this.fetch<SupervisionCamera[]>('/api/supervision/cameras');
  }

  async getSystemSupervisionSettings(): Promise<SystemSupervisionSettings> {
    return this.fetch<SystemSupervisionSettings>('/api/supervision/settings');
  }

  async updateSystemSupervisionSettings(
    payload: Omit<SystemSupervisionSettings, 'id'>
  ): Promise<SystemSupervisionSettings> {
    return this.fetch<SystemSupervisionSettings>('/api/supervision/settings', {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async getActiveVisitors(): Promise<VisitorRegistration[]> {
    return this.fetch<VisitorRegistration[]>('/api/supervision/visitors/active');
  }

  async getVisitorHistory(): Promise<VisitorRegistration[]> {
    return this.fetch<VisitorRegistration[]>('/api/supervision/visitors/history');
  }

  async createVisitorRegistration(payload: {
    start_time: string;
    end_time: string;
    visiting_company: string;
    total_people: number;
  }): Promise<VisitorRegistration> {
    return this.fetch<VisitorRegistration>('/api/supervision/visitors', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateVisitorRegistration(registrationId: string, payload: Partial<{
    start_time: string;
    end_time: string;
    visiting_company: string;
    total_people: number;
  }>): Promise<VisitorRegistration> {
    return this.fetch<VisitorRegistration>(`/api/supervision/visitors/${registrationId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  async deleteVisitorRegistration(registrationId: string): Promise<{ message: string }> {
    return this.fetch<{ message: string }>(`/api/supervision/visitors/${registrationId}`, {
      method: 'DELETE',
    });
  }

  async getActiveExternalPersonnel(): Promise<ExternalPersonnelRegistration[]> {
    return this.fetch<ExternalPersonnelRegistration[]>('/api/supervision/external/active');
  }

  async getExternalPersonnelHistory(): Promise<ExternalPersonnelRegistration[]> {
    return this.fetch<ExternalPersonnelRegistration[]>('/api/supervision/external/history');
  }

  async createExternalPersonnelRegistration(payload: {
    external_person_id?: string | null;
    name: string;
    organization: string;
    start_time: string;
    end_time: string;
    visit_reason: string;
    supervision_events: string[];
    allowed_camera_ids: string[];
  }): Promise<ExternalPersonnelRegistration> {
    return this.fetch<ExternalPersonnelRegistration>('/api/supervision/external', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateExternalPersonnelRegistration(registrationId: string, payload: Partial<{
    external_person_id: string | null;
    name: string;
    organization: string;
    start_time: string;
    end_time: string;
    visit_reason: string;
    supervision_events: string[];
    allowed_camera_ids: string[];
  }>): Promise<ExternalPersonnelRegistration> {
    return this.fetch<ExternalPersonnelRegistration>(`/api/supervision/external/${registrationId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  async uploadExternalPersonnelFace(registrationId: string, file: File): Promise<ExternalPersonnelRegistration> {
    const formData = new FormData();
    formData.append('file', file);
    return this.fetchFormData<ExternalPersonnelRegistration>(`/api/supervision/external/${registrationId}/face`, {
      method: 'POST',
      body: formData,
    });
  }

  async deleteExternalPersonnelRegistration(registrationId: string): Promise<{ message: string }> {
    return this.fetch<{ message: string }>(`/api/supervision/external/${registrationId}`, {
      method: 'DELETE',
    });
  }

  async compareFaceAgainstRegistry(file: File): Promise<FaceMatchResponse> {
    const formData = new FormData();
    formData.append('file', file);
    return this.fetchFormData<FaceMatchResponse>('/api/supervision/face-match', {
      method: 'POST',
      body: formData,
    });
  }

  async compareFaceFromCamera(cameraId: string): Promise<CameraFaceMatchResponse> {
    return this.fetch<CameraFaceMatchResponse>(`/api/cameras/${cameraId}/face-match`);
  }
}

export const api = new ApiClient();
export default api;
