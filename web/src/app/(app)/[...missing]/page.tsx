import { notFound } from "next/navigation";

/** Sends unknown URLs to this group's not-found page, so a 404 keeps the navigation. */
export default function Missing() {
  notFound();
}
