import { Field } from "@/components/reui-library/ui/field"
import { Input } from "@/components/reui-library/ui/input"
import { Label } from "@/components/reui-library/ui/label"

export default function Pattern() {
  return (
    <Field className="w-full max-w-xs">
      <Label htmlFor="label-demo-username">Username</Label>
      <Input id="label-demo-username" placeholder="Enter your username…" />
    </Field>
  )
}
