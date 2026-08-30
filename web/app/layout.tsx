import type { Metadata } from "next";
import { JetBrains_Mono, Manrope } from "next/font/google";
import { headers } from "next/headers";
import { PlatformChrome } from "./platform/platform-chrome";
import "./globals.css";

const manrope = Manrope({
  variable: "--font-manrope",
  subsets: ["latin"],
});

const jetBrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
});

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host =
    requestHeaders.get("x-forwarded-host") ??
    requestHeaders.get("host") ??
    "nse.ventoday.com";
  const protocol = requestHeaders.get("x-forwarded-proto") ?? "https";
  const origin = `${protocol}://${host}`;
  const title = "OpenDelta · Quant Research Platform";
  const description =
    "OpenDelta is a private modular research platform for NSE and crypto market analysis, factors, backtests, signals, risk, and data quality.";
  const socialImage = new URL("/og.png", origin).toString();

  return {
    metadataBase: new URL(origin),
    title: {
      default: title,
      template: "%s · OpenDelta",
    },
    description,
    openGraph: {
      title,
      description,
      type: "website",
      images: [{ url: socialImage, width: 1536, height: 1024 }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [socialImage],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${manrope.variable} ${jetBrainsMono.variable}`}>
        <PlatformChrome />
        <div className="platform-content">{children}</div>
      </body>
    </html>
  );
}
