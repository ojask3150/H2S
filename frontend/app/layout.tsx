import type { Metadata } from "next";
import "./globals.css";
import "leaflet/dist/leaflet.css";

export const metadata: Metadata = {
  title: "Anticipatory Action Platform — Bay of Bengal",
  description:
    "Pre-landfall coastal cyclone risk, vulnerability modeling, and anticipatory action.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
