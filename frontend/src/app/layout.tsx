import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/providers/auth-provider";
import { QueryProvider } from "@/providers/query-provider";
import { Toaster } from "sonner";

export const metadata: Metadata = {
  title: "中复神鹰人员安全监管系统",
  description: "中复神鹰人员安全监管系统",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body className="antialiased">
        <QueryProvider>
          <AuthProvider>
            {children}
          </AuthProvider>
        </QueryProvider>
        <Toaster
          richColors
          position="top-right"
          expand={true}
          closeButton
          duration={4000}
          toastOptions={{
            classNames: {
              toast: "glass corner-cut border-2 border-border shadow-lg font-mono",
              title: "font-black uppercase tracking-wide text-sm",
              description: "text-muted-foreground text-xs uppercase tracking-wide",
              actionButton: "bg-primary text-primary-foreground corner-cut font-bold uppercase tracking-wider",
              cancelButton: "bg-muted text-muted-foreground corner-cut",
              closeButton: "bg-muted/80 border-border/50 hover:bg-muted corner-cut",
            },
          }}
        />
      </body>
    </html>
  );
}
