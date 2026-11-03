import { Field, FieldLabel } from "@/components/reui-library/ui/field"
import { Input } from "@/components/reui-library/ui/input"

export default function Pattern() {
  return (
    <Field className="w-full max-w-xs">
      <FieldLabel htmlFor="custom-focus">Custom Focus</FieldLabel>
      <Input
        id="custom-focus"
        className="focus-visible:border-emerald-500 focus-visible:ring-emerald-500/50"
        placeholder="Green focus ring"
      />
    </Field>
  )
}
