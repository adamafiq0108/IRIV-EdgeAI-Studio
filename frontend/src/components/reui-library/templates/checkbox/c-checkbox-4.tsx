import { Checkbox } from "@/components/reui-library/ui/checkbox"
import { Field, FieldLabel } from "@/components/reui-library/ui/field"

export default function Pattern() {
  return (
    <Field orientation="horizontal" className="w-auto">
      <Checkbox id="indeterminate" indeterminate />
      <FieldLabel htmlFor="indeterminate">Indeterminate state</FieldLabel>
    </Field>
  )
}
