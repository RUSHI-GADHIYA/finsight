import type { Metadata } from "next";
import { Archivo, IBM_Plex_Mono, Source_Serif_4 } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const archivo = Archivo({
  variable: "--font-archivo",
  subsets: ["latin"],
  axes: ["wdth"],
});
const sourceSerif = Source_Serif_4({
  variable: "--font-source-serif",
  subsets: ["latin"],
});
const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  title: "FinSight",
  description:
    "Research questions answered from SEC 10-K filings by a team of agents, with every claim cited and checked before you sign off.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${archivo.variable} ${sourceSerif.variable} ${plexMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col">
        <header className="border-b border-rule">
          <nav className="mx-auto flex max-w-6xl items-baseline gap-8 px-4 py-4 sm:px-6">
            <Link
              href="/"
              className="display text-2xl font-bold uppercase tracking-wide"
            >
              FinSight
            </Link>
            <span className="hidden font-mono text-xs text-graphite sm:inline">
              SEC 10-K research · 12 companies
            </span>
            <div className="display ml-auto flex gap-6 text-sm font-semibold uppercase">
              <Link href="/" className="hover:text-pencil">
                New research
              </Link>
              <Link href="/reports" className="hover:text-pencil">
                Signed-off reports
              </Link>
            </div>
          </nav>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6">
          {children}
        </main>
        <footer className="mx-auto w-full max-w-6xl px-4 py-6 font-mono text-xs text-graphite sm:px-6">
          For research and education only; not investment advice. Sources: SEC
          EDGAR filings and XBRL company facts.
        </footer>
      </body>
    </html>
  );
}
