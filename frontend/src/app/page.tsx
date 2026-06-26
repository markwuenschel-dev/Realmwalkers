import { redirect } from "next/navigation";

// The desk opens on the inbox / review queue (the prototype's default screen).
export default function Home() {
  redirect("/inbox");
}
