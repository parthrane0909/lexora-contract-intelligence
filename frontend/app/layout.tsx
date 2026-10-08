import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { ToastProvider } from "@/components/Toast";
import { SiteNav } from "@/components/SiteNav";

export const metadata = {
  title: "Contract Intelligence",
  description: "AI Contract Risk & Compliance",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
          <ToastProvider>
            <a
              href="#main"
              className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-paper focus:px-3 focus:py-2 focus:shadow-lift"
            >
              Skip to content
            </a>
            <SiteNav />
            {/* Not a <main>: every page owns its own single <main> landmark.
                This is only the skip-link destination (tabIndex makes it
                focusable for keyboard users). */}
            <div id="main" tabIndex={-1} className="mx-auto max-w-6xl px-4 py-6 md:px-6 md:py-8">
              {children}
            </div>
          </ToastProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
