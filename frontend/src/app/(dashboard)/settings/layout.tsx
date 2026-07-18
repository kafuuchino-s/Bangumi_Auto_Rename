import { SettingsTabs } from "@/components/settings/settings-tabs";
import { Outlet } from "react-router-dom";

export default function SettingsLayout() {
  return (
    <div className="space-y-6">
      <SettingsTabs />
      <div className="flex-1"><Outlet /></div>
    </div>
  );
}
