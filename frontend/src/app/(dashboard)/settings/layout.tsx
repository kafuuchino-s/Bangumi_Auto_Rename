import { SettingsTabs } from "@/components/settings/settings-tabs";

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-6">
      <SettingsTabs />
      <div className="flex-1">{children}</div>
    </div>
  );
}
