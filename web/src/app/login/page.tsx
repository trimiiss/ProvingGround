import { Card } from "@/components/ui/card";
import { LoginForm } from "./login-form";

export const metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <main className="grid min-h-dvh place-items-center p-4">
      <Card className="w-full max-w-sm gap-5 p-6">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Sign in to ProvingGround</h1>
          <p className="mt-1 text-sm text-muted-foreground">Evaluation, red-teaming and reliability for AI agents.</p>
        </div>
        <LoginForm />
      </Card>
    </main>
  );
}
