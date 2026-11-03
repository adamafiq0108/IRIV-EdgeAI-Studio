import { Label } from "@/components/reui-library/ui/label"
import { Switch } from "@/components/reui-library/ui/switch"

export default function Pattern() {
  return (
    <div className="flex flex-col items-start justify-start gap-3">
      <div className="flex items-center gap-2">
        <Switch id="switch-sm" size="sm" />
        <Label htmlFor="switch-sm">Small Switch</Label>
      </div>
      <div className="flex items-center gap-2">
        <Switch id="switch-default" size="default" />
        <Label htmlFor="switch-default">Default Switch</Label>
      </div>
    </div>
  )
}
