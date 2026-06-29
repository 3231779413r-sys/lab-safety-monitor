export interface User {
  id: number;
  username: string;
  email: string;
  full_name: string | null;
  department: string | null;
  job_title: string | null;
  responsibilities: string | null;
  role: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface RegisterRequest {
  username: string;
  email: string;
  password: string;
  full_name?: string;
  department?: string;
  job_title?: string;
  responsibilities?: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}
