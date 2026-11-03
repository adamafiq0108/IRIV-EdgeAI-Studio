import { Field } from "@/components/reui-library/ui/field"
import { Input } from "@/components/reui-library/ui/input"
import { Label } from "@/components/reui-library/ui/label"

export default function Pattern() {
  return (
    <Field data-disabled={true} className="w-full max-w-xs">
      <Label htmlFor="label-demo-disabled">Disabled Field</Label>
      <Input id="label-demo-disabled" placeholder="Disabled input…" disabled />
    </Field>
  )
}
