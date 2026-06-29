import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Middleware 只处理一些基础的路径重定向，不做强制认证
// 认证逻辑由 AuthProvider 在客户端处理
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // 如果访问根路径，重定向到 dashboard
  if (pathname === "/") {
    return NextResponse.redirect(new URL("/dashboard", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|public|api).*)",
  ],
};
