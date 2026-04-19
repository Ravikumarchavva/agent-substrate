import { BarChart2, Code2, FileText, FolderKanban, ListTodo, Palette } from "lucide-react";
import { DocsIcon, GitHubIcon, GoogleIcon, SpotifyIcon } from "@/components/settings/icons";

interface AppIconProps {
  toolName?: string | null;
  className?: string;
}

export function AppIcon({ toolName, className = "h-4 w-4 text-foreground" }: AppIconProps) {
  const normalizedName = (toolName ?? "").toLowerCase();

  if (normalizedName.includes("spotify")) {
    return <SpotifyIcon className={className.replace("text-foreground", "text-[#1DB954]")} />;
  }

  if (
    normalizedName.includes("google_workspace") ||
    normalizedName.includes("workspace") ||
    normalizedName.includes("gmail") ||
    normalizedName.includes("calendar") ||
    normalizedName.includes("drive")
  ) {
    return <GoogleIcon className={className} />;
  }

  if (normalizedName.includes("task") || normalizedName.includes("kanban")) {
    return <ListTodo className={className} />;
  }

  if (normalizedName.includes("json")) {
    return <Code2 className={className} />;
  }

  if (normalizedName.includes("markdown")) {
    return <FileText className={className} />;
  }

  if (normalizedName.includes("visual") || normalizedName.includes("chart")) {
    return <BarChart2 className={className} />;
  }

  if (normalizedName.includes("color") || normalizedName.includes("palette")) {
    return <Palette className={className} />;
  }

  if (normalizedName.includes("github")) {
    return <GitHubIcon className={className} />;
  }

  if (normalizedName.includes("docs")) {
    return <DocsIcon className={className} />;
  }

  return <FolderKanban className={className} />;
}