"use client";

import { useState } from "react";
import { useAuth } from "@/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Shield, Loader2, AlertCircle } from "lucide-react";
import { motion } from "framer-motion";

import Image from "next/image";
import logoImg from "@/app/logo-night.png";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const { login } = useAuth();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setIsLoading(true);
    try {
      await login({ username, password });
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div
      className="min-h-screen flex items-center justify-center bg-cover bg-center bg-no-repeat p-4"
      style={{ backgroundImage: "url('/background.jpg')" }}
    >
      {/* 背景装饰 */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-40 -right-40 w-80 h-80 bg-primary/5 rounded-full blur-3xl" />
        <div className="absolute -bottom-40 -left-40 w-80 h-80 bg-primary/5 rounded-full blur-3xl" />
      </div>

      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }} className="w-full max-w-md relative z-10 -mt-32">
        {/* Logo */}
        <div className="text-center mb-8">
          <motion.div initial={{ scale: 0.8 }} animate={{ scale: 1 }} transition={{ delay: 0.2, type: "spring", stiffness: 200 }} className="inline-flex items-center justify-center px-4 py-2 h-16 rounded-2xl mb-4">
            <Image src={logoImg} alt="Logo" width={629} height={88} className="w-auto h-auto" />
          </motion.div>
          <h1 className="text-4xl font-bold text-white">人员安全监管系统</h1>
          <p className="text-white/80 mt-2">请登录以继续</p>
        </div>

        {/* 登录表单 */}
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }} className="bg-card rounded-2xl border border-border shadow-soft-lg p-8 relative z-10">
          <form onSubmit={handleSubmit} className="space-y-6">
            {error && (
              <motion.div initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} className="flex items-center gap-2 p-3 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive text-sm">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{error}</span>
              </motion.div>
            )}

            <div className="space-y-2">
              <Label htmlFor="username">用户名 / 邮箱</Label>
              <Input id="username" type="text" placeholder="请输入用户名或邮箱" value={username} onChange={(e) => setUsername(e.target.value)} required autoComplete="username" className="h-12" />
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">密码</Label>
              <Input id="password" type="password" placeholder="请输入密码" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" className="h-12" />
            </div>

            <Button type="submit" className="w-full h-12 cursor-pointer" disabled={isLoading}>
              {isLoading ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />登录中...</> : "登录"}
            </Button>
          </form>
        </motion.div>
      </motion.div>
    </div>
  );
}
