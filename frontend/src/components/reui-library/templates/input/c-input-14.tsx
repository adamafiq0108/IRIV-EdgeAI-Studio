import { Field, FieldLabel } from "@/components/reui-library/ui/field"
import { Input } from "@/components/reui-library/ui/input"

export default function Pattern() {
  return (
    <Field className="max-w-xs">
      <FieldLabel htmlFor="input-demo-time">Time</FieldLabel>
      <Input id="input-demo-time" type="time" />
    </Field>
  )
}
