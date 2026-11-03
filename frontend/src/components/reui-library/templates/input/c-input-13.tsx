import { Field, FieldLabel } from "@/components/reui-library/ui/field"
import { Input } from "@/components/reui-library/ui/input"

export default function Pattern() {
  return (
    <Field className="max-w-xs">
      <FieldLabel htmlFor="company">
        Company <span className="text-destructive">*</span>
      </FieldLabel>
      <Input id="company" placeholder="Wotso Inc." />
    </Field>
  )
}
